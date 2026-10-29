# Staging Deployment Guide — Plantation Rainfall Dashboard

Step-by-step runbook for deploying the dashboard to the DWS staging environment,
with sign-in through **DWS Hub (OIDC, strict mode)**.

| | |
| --- | --- |
| Frontend server | `172.28.92.56` — nginx container, host port **3070** |
| Backend server | `172.28.92.57` — Python container, host port **5070** (private address only) |
| Database | Apsara RDS for PostgreSQL `pgm-d9jx9o06qae8gf3h.pgsql.ap-southeast-5.rds.aliyuncs.com:5432` |
| DWS Hub (staging) | `http://test-dwshub.kpndomain.com` (issuer and all OIDC endpoints; resolves to `172.28.92.56`, port 80) |
| Users open | **http://172.28.92.56:3070/** |
| Install folder (both servers) | `/opt/plantation-rainfall` |
| Code | GitHub `dwsitproject-hub/Plantation-Rainfall-Dashboard`, branch `restructure/production-layout` (or `main` once merged) |

```
 Browser ──► FE 172.28.92.56:3070  (nginx: pages, /api proxy, page-access check)
                   │  http://172.28.92.57:5070  (private network, FE only)
                   ▼
            BE 172.28.92.57:5070  (Python API, sessions, roles, data refresh)
              │                │                     │
              ▼                ▼                     ▼
     Apsara RDS PostgreSQL   DWS Hub               Internet: Open-Meteo, SAP Syncera
     (SSL, whitelisted)      test-dwshub.kpndomain.com
                             (OIDC token + JWKS)

 Sign-in: Browser ─► FE /api/sso/login ─► DWS Hub authorize ─► FE /api/sso/callback
          (backend exchanges the code with PKCE and verifies the ID token via JWKS)
```

Allow about 60–90 minutes. Steps 1–3 are preparation, steps 4–9 deploy, and
step 10 verifies everything.

---

## 0. What changes on the servers

- One new container per server: `plantation-rainfall-frontend-staging` (FE) and
  `plantation-rainfall-backend-staging` (BE). No existing container, port or
  network is touched.
- One new database (`plantation_rainfall`) and account (`rainfall_app`) on the RDS instance.
- One new app registration in DWS Hub Admin.
- One cron entry on the backend server for the daily data refresh.

---

## 1. Preparation (on your PC)

### 1.1 Publish the code

The servers pull the code from GitHub, so the deployment branch must be pushed.

```powershell
cd "D:\Claude\Plant Rainfall"
git status
git push -u origin restructure/production-layout
```

### 1.2 Files to carry to the backend server

| File | Why |
| --- | --- |
| `data\export\rainfall_forecast_snapshots.csv` | 25 archived forecast runs (18 Sep – 19 Oct). They power *Forecast changes* and *Last week accuracy* and **cannot be downloaded again**. |
| Your local `.env` | Source for the two Syncera secrets (copy the values only, never the file). |

To refresh the export before you deploy (it includes every run up to today):

```powershell
python -c "import csv; from backend.database import repository as r; rows=r.read_snapshots(); f=open('data/export/rainfall_forecast_snapshots.csv','w',newline='',encoding='utf-8-sig'); w=csv.DictWriter(f,fieldnames=['run_date','country','region','latitude','longitude','date','rainfall_mm','data_type','retrieved_at_utc','source'],extrasaction='ignore'); w.writeheader(); w.writerows(rows); f.close(); print(len(rows),'rows')"
```

### 1.3 Information to collect

- [ ] RDS console access (to create the database account, whitelist and SSL).
- [ ] DWS Hub Admin access (to register the app).
- [ ] A strong password for the `rainfall_app` database account (keep it in your password manager).
- [ ] The Hub discovery document (step 3.1).

---

## 2. Pre-flight checks on the servers

### 2.1 Frontend server `172.28.92.56`

```bash
# Port 3070 must be free (no output = free)
ss -ltnp | grep -E ':3070\b'
docker ps --format '{{.Names}}\t{{.Ports}}' | grep -E ':3070->' || echo "3070 free in Docker"

docker --version && docker compose version     # Docker 20.10+ and Compose v2 required
curl -s -o /dev/null -w '%{http_code}\n' http://172.28.92.57:4000/   # any code = BE server reachable (404 is fine)
```

> `tas_staging_nginx` was in a restart loop when the ports were chosen, so its
> port is unknown. If the check above shows 3070 in use, pick another free port and
> use it everywhere this guide says 3070 (`FRONTEND_HOST_PORT` in the frontend `.env`, the
> Hub redirect URI and `SSO_REDIRECT_URI`).

### 2.2 Backend server `172.28.92.57`

```bash
ss -ltnp | grep -E ':5070\b'                     # no output = free
docker ps --format '{{.Names}}\t{{.Ports}}' | grep -E ':5070->' || echo "5070 free in Docker"
docker --version && docker compose version

# Outbound access the refresh needs
curl -s -o /dev/null -w 'open-meteo %{http_code}\n' "https://api.open-meteo.com/v1/forecast?latitude=0&longitude=101&daily=precipitation_sum"
curl -s -o /dev/null -w 'syncera-prod %{http_code}\n' https://syncera.gamasap.com/api/v1/auth
curl -s -o /dev/null -w 'syncera-dev %{http_code}\n'  https://syncera-dev.gamasap.com/api/v1/auth
# DWS Hub (issuer hostname; the backend container pins it to 172.28.92.56)
curl -s http://test-dwshub.kpndomain.com/api/sso/.well-known/openid-configuration ; echo
getent hosts test-dwshub.kpndomain.com || echo "not in server DNS (fine: pinned in the container)"
# RDS reachable on 5432 (after the whitelist in step 4.2)
timeout 5 bash -c '</dev/tcp/pgm-d9jx9o06qae8gf3h.pgsql.ap-southeast-5.rds.aliyuncs.com/5432' && echo "RDS port open"
timedatectl | grep 'Time zone'
```

Expected: Open-Meteo `200`; Syncera any HTTP code other than `000` (e.g. `404`/`405` for a GET on the auth URL);
the Hub returns JSON whose `issuer` is `http://test-dwshub.kpndomain.com`.

> Checked 20 Oct 2026: both ports free, Docker 29, Open-Meteo 200, Syncera 405,
> RDS port open, Hub discovery OK. The backend server clock is **Asia/Shanghai (+08:00)**;
> the backend container runs on Asia/Jakarta time, and the cron line in step 8 is
> written in server time.

---

## 3. Register the app in DWS Hub

### 3.1 Read the Hub discovery document (backend server)

```bash
curl -s http://test-dwshub.kpndomain.com/api/sso/.well-known/openid-configuration | python3 -m json.tool
```

Staging returns (20 Oct 2026):

```
issuer                  http://test-dwshub.kpndomain.com
authorization_endpoint  http://test-dwshub.kpndomain.com/api/sso/authorize
token_endpoint          http://test-dwshub.kpndomain.com/api/sso/token
jwks_uri                http://test-dwshub.kpndomain.com/api/sso/jwks
algorithms RS256, PKCE S256, token auth "none" (public client, no client secret)
```

What each value is used for:

| Field | Used for |
| --- | --- |
| `issuer` | Checked automatically against every ID token (`iss`). Nothing to configure. |
| `authorization_endpoint` | Users' **browsers** are sent here. It must be reachable from user PCs (see 3.3). |
| `token_endpoint`, `jwks_uri` | Called by the **backend container**; must be reachable from `172.28.92.57`. |

Strict-mode sanity checks (from the Hub guide):

```bash
curl -s -o /dev/null -w 'jwks %{http_code}\n'   http://test-dwshub.kpndomain.com/api/sso/jwks       # 200
curl -s -o /dev/null -w 'bridge %{http_code}\n' http://test-dwshub.kpndomain.com/api/sso/bridge     # 410
```

### 3.2 Create the registration in Hub Admin

| Hub field | Value |
| --- | --- |
| App name | Plantation Rainfall Dashboard (Staging) |
| `sso_mode` | `oidc` |
| `oauth_client_id` | `plantation-rainfall-staging` |
| `oidc_redirect_uris` | `http://172.28.92.56:3070/api/sso/callback` (exact, no trailing slash) |
| App / launch URL | `http://172.28.92.56:3070/api/sso/login` |

The staging Hub advertises `token_endpoint_auth_methods_supported: ["none"]`, so this is a
public client with PKCE: leave `SSO_CLIENT_SECRET` empty.

### 3.3 Check the authorize address from a user PC

Open `http://test-dwshub.kpndomain.com/api/sso/authorize` in a browser on the office network/VPN. A Hub page,
a redirect or a Hub error about missing parameters means it is reachable (checked
20 Oct 2026 from the IT PC: `302`). `SSO_AUTHORIZATION_URL` then stays empty. Only if
users cannot open it, set `SSO_AUTHORIZATION_URL` to a Hub address they can open.

---

## 4. Database on Apsara RDS

### 4.1 Version and SSL

- RDS console → instance → **Basic Information**: note the PostgreSQL version.
  Version 14 or later is fine. PostgreSQL 18 is what the dashboard was built and
  tested on, and the setup only warns on older versions.
- **Data Security → SSL**: enable SSL (recommended). The backend uses
  `DATABASE_SSLMODE=require`. If SSL stays off, use `prefer` in step 5.2.

### 4.2 Whitelist the backend server

RDS console → **Whitelist Settings** → add `172.28.92.57` to a whitelist group
(e.g. `plantation_rainfall_be`). Re-run the RDS port check from 2.2: it should
print `RDS port open`.

### 4.3 Create the account and database

**Option A — RDS console (recommended):**

1. **Accounts → Create Account**: name `rainfall_app`, type **Standard Account**,
   your strong password.
2. **Databases → Create Database**: name `plantation_rainfall`, owner/authorized
   account `rainfall_app`, character set **UTF8**.
3. **Allow `rainfall_app` to create the schema.** The console usually makes the
   privileged account the database owner and gives the standard account read/write
   rights only, so `init_db` fails with `permission denied for database
   plantation_rainfall`. Signed in with the **privileged account** (console →
   **Log On to Database** / DMS, database `plantation_rainfall`), run once:

   ```sql
   GRANT CONNECT, CREATE, TEMPORARY ON DATABASE plantation_rainfall TO rainfall_app;
   ```

   Alternative from the backend server (asks for the privileged password):

   ```bash
   docker run --rm -it postgres:17-alpine psql "host=pgm-d9jx9o06qae8gf3h.pgsql.ap-southeast-5.rds.aliyuncs.com port=5432 dbname=plantation_rainfall user=<privileged account> sslmode=prefer" -c "GRANT CONNECT, CREATE, TEMPORARY ON DATABASE plantation_rainfall TO rainfall_app;"
   ```

**Option B — command line with the RDS privileged account:** after step 5, run:

```bash
cd /opt/plantation-rainfall/deploy/staging/backend
docker compose run --rm backend python -m backend.database.bootstrap
```

It asks for the privileged account login (hidden input, never stored) and
creates `rainfall_app` (password from `.env.staging`), the database and the schema.

---

## 5. Backend server `172.28.92.57`

### 5.1 Get the code

```bash
sudo mkdir -p /opt/plantation-rainfall && sudo chown "$USER" /opt/plantation-rainfall
cd /opt/plantation-rainfall

# One read-only deploy key per server (GitHub → repo → Settings → Deploy keys → Add, "Allow write" OFF)
ssh-keygen -t ed25519 -C "plantrainfall-staging-be" -f ~/.ssh/id_ed25519_plantrainfall -N ""
cat ~/.ssh/id_ed25519_plantrainfall.pub        # paste into GitHub as a deploy key
cat >> ~/.ssh/config <<'EOF'
Host github-plantrainfall
  HostName github.com
  User git
  IdentityFile ~/.ssh/id_ed25519_plantrainfall
  IdentitiesOnly yes
EOF
ssh -T git@github-plantrainfall                # "successfully authenticated"

git clone -b restructure/production-layout \
  git@github-plantrainfall:dwsitproject-hub/Plantation-Rainfall-Dashboard.git .
```

### 5.2 Settings and secrets

```bash
cd /opt/plantation-rainfall/deploy/staging/backend
cp .env.staging.example .env.staging
chmod 600 .env.staging
nano .env.staging
```

Fill in:

| Setting | Value |
| --- | --- |
| `DATABASE_PASSWORD` | the `rainfall_app` password from 4.3 |
| `DATABASE_SSLMODE` | `require` if RDS SSL is on, otherwise `prefer` (SSL was **off** on the staging instance on 20 Oct 2026) |
| `SYNCERA_PROD_CLIENT_SECRET` | from your local `.env` (DEV is not used) |
| `SSO_CLIENT_ID` | as registered in 3.2 (`plantation-rainfall-staging`) |
| `SSO_CLIENT_SECRET` | empty (the Hub uses public clients with PKCE) |
| `SSO_REDIRECT_URI` | `http://172.28.92.56:3070/api/sso/callback` (must match Hub exactly) |
| `SSO_DISCOVERY_URL` | `http://test-dwshub.kpndomain.com/api/sso/.well-known/openid-configuration` (already in the template) |
| `SSO_AUTHORIZATION_URL` | empty, unless step 3.3 required it |
| `SSO_AUTO_PROVISION_ROLE` | empty (recommended: only registered users get in) or `regional` |

Check that nothing is left unset:

```bash
grep -n 'REPLACE_ME' .env.staging || echo "all values set"
```

### 5.3 Build, create the schema and start

The health check reads the database, so create the schema before starting.
(If you used Option B in 4.3, bootstrap already created it; the `init_db` line is
harmless to run again.)

```bash
cd /opt/plantation-rainfall/deploy/staging/backend
docker compose build
docker compose run --rm backend python -m backend.database.init_db
docker compose up -d
docker compose ps                              # STATUS: Up (healthy) after ~20 s
curl -s http://172.28.92.57:5070/api/health ; echo
```

Expected: `Database schema is up to date; 18 weather regions seeded.`, then
`{"status":"ok","plantationVersion":"plantation-v0"}`.

If health returns `{"status":"error","problem":...}`, the problem says which:
*Database schema missing* (run the `init_db` line) or *Database unreachable*
(password, whitelist or SSL mode; the exact error is in `docker compose logs backend`).

Confirm the backend container can reach the Hub (token endpoint and signing keys):

```bash
docker compose exec backend python -c "import urllib.request as u; print(u.urlopen('http://test-dwshub.kpndomain.com/api/sso/jwks', timeout=10).status)"   # 200
```

### 5.4 (merged into 5.3)

The schema is created in step 5.3.

### 5.5 Import the forecast archive

Copy the CSV from your PC (step 1.2), e.g. with MobaXterm's file panel or:

```powershell
scp "D:\Claude\Plant Rainfall\data\export\rainfall_forecast_snapshots.csv" root@172.28.92.57:/opt/plantation-rainfall/deploy/staging/backend/import/
```

Then on the backend server:

```bash
cd /opt/plantation-rainfall/deploy/staging/backend
mkdir -p import            # if scp did not create it
docker compose run --rm -v "$PWD/import:/app/import:ro" backend \
  python -m backend.database.import_legacy_files import --only snapshots
```

Expected: `ok rainfall_forecast_snapshots.csv: 25 runs, …`.

### 5.6 First data refresh (≈ 5–10 minutes)

```bash
chmod +x refresh.sh
./refresh.sh ; tail -30 "$(ls -t logs/refresh-*.log | head -1)"
```

Expected ending: `Latest PROD actual date: <yesterday or today>` and
`All dashboard data was refreshed successfully.` If Syncera rate-limits a few
estates you will see `rate limited, will retry after a pause` — that is handled
automatically.

### 5.7 Break-glass administrator

Create one local administrator (works even if the Hub is down). **Use the same
email as your DWS Hub account**: your first Hub sign-in then links to it.

```bash
docker compose run --rm backend python -m backend.auth.create_admin
```

---

## 6. Frontend server `172.28.92.56`

### 6.1 Get the code

Same as 5.1, with its own deploy key (comment `plantrainfall-staging-fe`):

```bash
sudo mkdir -p /opt/plantation-rainfall && sudo chown "$USER" /opt/plantation-rainfall
cd /opt/plantation-rainfall
# ... create ~/.ssh/id_ed25519_plantrainfall, add it as a read-only deploy key, ssh config as in 5.1 ...
git clone -b restructure/production-layout \
  git@github-plantrainfall:dwsitproject-hub/Plantation-Rainfall-Dashboard.git .
```

### 6.2 Settings

```bash
cd /opt/plantation-rainfall/deploy/staging/frontend
cp .env.example .env
nano .env
```

| Setting | Value |
| --- | --- |
| `BACKEND_URL` | `http://172.28.92.57:5070` (backend private address and port from step 5) |
| `FRONTEND_HOST_PORT` | `3070` (must match the port in the Hub redirect URI and `SSO_REDIRECT_URI`) |

`docker compose` reads `.env` from this folder automatically. If a value is missing,
startup stops with `Set BACKEND_URL in deploy/staging/frontend/.env` (or
`FRONTEND_HOST_PORT`). Check what compose will use:

```bash
docker compose config | grep -E 'BACKEND_URL|published'
```

### 6.3 Build and start

```bash
cd /opt/plantation-rainfall/deploy/staging/frontend
docker compose up -d --build
docker compose ps                                           # Up (healthy)
curl -s http://127.0.0.1:3070/healthz                       # ok
curl -s http://127.0.0.1:3070/api/health ; echo             # {"status":"ok",...}  (through to the backend)
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://127.0.0.1:3070/   # 302 .../login.html
```

To change the backend address or port later, edit `.env` and run
`docker compose up -d` again (compose recreates the container with the new values).

---

## 7. Network access (Alibaba Cloud security groups)

| Server | Inbound rule | Source |
| --- | --- | --- |
| BE `172.28.92.57` | TCP **5070** | `172.28.92.56/32` **only** (the frontend server) |
| FE `172.28.92.56` | TCP **3070** | office network / VPN ranges that should use staging |
| RDS | 5432 (whitelist) | `172.28.92.57` |

> The backend trusts the visitor address that the frontend reports
> (`TRUST_PROXY_HEADERS=true`) for audit logs and sign-in throttling, so port 5070
> must not be reachable from anywhere else.

Check from a PC that is **not** the frontend server: `http://172.28.92.57:5070/api/health`
should time out.

---

## 8. Daily refresh (cron on the backend server)

```bash
crontab -e
```

The refresh runs twice a day, Monday–Saturday:

- **06:30 WIB** — the new Open-Meteo forecast is ready for the start of the working day.
- **12:00 WIB** — picks up yesterday's SAP rain readings. Estates read the gauges in the
  morning and enter them in SAP during the morning, so at 06:30 SAP usually has nothing
  for yesterday yet and the dashboard's latest SAP date stays two days back (for example,
  the 06:30 run on 21 Oct showed SAP data up to 19 Oct; the 20 Oct readings were in SAP
  by early afternoon).

The backend server clock is **Asia/Shanghai (+08:00)**, one hour ahead of Jakarta, so
the cron lines use server time (07:30 and 13:00):

```
# Plantation Rainfall staging refresh (server time is Asia/Shanghai = WIB + 1 hour)
# 07:30 server time = 06:30 WIB: new forecast for the working day
30 7 * * 1-6 /opt/plantation-rainfall/deploy/staging/backend/refresh.sh
# 13:00 server time = 12:00 WIB: yesterday's SAP rain readings
0 13 * * 1-6 /opt/plantation-rainfall/deploy/staging/backend/refresh.sh
```

Check the server's timezone first with `timedatectl | grep "Time zone"`; if it is not
Asia/Shanghai, shift both lines by the difference from WIB.

If a run is still going when the next one starts, the second one exits at once and
changes nothing (its log shows "Refresh FAILED with exit code 75", meaning a refresh
was already running). Pages keep serving the previous data
while a refresh runs and switch to the new data within about a minute after it finishes.

The SAP date shown on the dashboard is the latest day on which at least one estate
recorded rain above 0 mm. SAP pre-fills every day to the end of the month with 0 mm,
so a day with no reported rain anywhere is treated as not yet entered.

The refresh itself runs on Jakarta time inside the container, so forecast run dates
are WIB dates.

Logs: `/opt/plantation-rainfall/deploy/staging/backend/logs/refresh-*.log` (kept 30 days).
Every step is also recorded in the `rainfall.refresh_run` table.

---

## 9. Users and roles

1. Sign in at http://172.28.92.56:3070/ with **Sign in with DWS Hub** (or your
   break-glass email + password).
2. **Administration → User management → Register new user** for each person:
   their **DWS Hub email**, name and role (Administrator / Plantation user /
   Regional viewer). Leave the password **empty** — they sign in with DWS Hub.
3. On their first Hub sign-in the account is linked to their Hub user id (`sub`);
   it then keeps working even if their email changes in the Hub. Linked users show
   a **DWS Hub** badge.

Hub users who are not registered see "has no access to this dashboard yet"
(unless `SSO_AUTO_PROVISION_ROLE` is set).

---

## 10. Verification checklist

| # | Check | Expected |
| --- | --- | --- |
| 1 | `docker ps` on both servers | both containers `Up (healthy)`; no other container restarted |
| 2 | Open http://172.28.92.56:3070/ | redirected to the sign-in page with the **Sign in with DWS Hub** button |
| 3 | Click **Sign in with DWS Hub** | Hub sign-in, then back on the dashboard (first time: account linked) |
| 4 | Sidebar | your name and role; sections match your role |
| 5 | All Regions pages | coverage line shows yesterday as latest actual |
| 6 | In-house → Weather overview | 62 estates, latest SAP date = yesterday/today |
| 7 | Last week accuracy / Forecast changes | numbers for all regions (from the imported archive) |
| 8 | User management → Recent activity | `sso_login_succeeded` with your **real PC address** (not a Docker address) |
| 9 | Regional viewer test user opens `/inhouse/` | redirected to `/?denied=1` |
| 10 | `http://172.28.92.57:5070` from your PC | not reachable |
| 11 | Next morning | new `logs/refresh-*.log` ending with success |

---

## 11. Updating staging later

```bash
# backend server
cd /opt/plantation-rainfall && git pull
cd deploy/staging/backend && docker compose build
docker compose run --rm backend python -m backend.database.init_db   # schema first: additive and idempotent
docker compose up -d

# frontend server
cd /opt/plantation-rainfall && git pull
cd deploy/staging/frontend && docker compose up -d --build
```

**Rollback:** `git checkout <previous commit>` on the affected server, then
`docker compose up -d --build`. Schema changes are additive, so older code keeps
working with the newer schema.

**Backups:** rely on RDS automated backups (console → Backup and Restoration;
keep at least 7 days). The forecast archive (`rainfall.forecast_snapshot`) is the
only data that cannot be re-downloaded.

**Remove completely:** `docker compose down` in both `deploy/staging/*` folders,
delete `/opt/plantation-rainfall`, remove the cron line, the Hub registration, the
security-group rules and (optionally) the RDS database and account.

---

## 12. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `docker compose` says `Set BACKEND_URL in deploy/staging/frontend/.env` | frontend `.env` missing or incomplete | `cp .env.example .env` in `deploy/staging/frontend` (6.2) |
| FE container exits: `host not found in upstream` | `BACKEND_URL` wrong or BE down when FE started | start BE first; check `BACKEND_URL` in `deploy/staging/frontend/.env`; `docker compose up -d` again |
| Pages show **502 Bad Gateway** | FE cannot reach BE `172.28.92.57:5070` | security group rule (7); `curl http://172.28.92.57:5070/api/health` from FE server |
| `init_db`: `permission denied for database plantation_rainfall` | `rainfall_app` may not create objects in the database | run the GRANT in 4.3 step 3 with the privileged account, then `init_db` again |
| `/api/health` → `Database schema missing` | `init_db` not run yet | `docker compose run --rm backend python -m backend.database.init_db` |
| BE unhealthy, log shows `password authentication failed` / `timeout` | DB password, whitelist, SSL mode | 4.2 whitelist; `.env.staging`; try `DATABASE_SSLMODE=prefer` if SSL is off |
| `server does not support SSL, but SSL was required` | `DATABASE_SSLMODE=require` but RDS SSL is off (the staging instance on 20 Oct 2026) | set `DATABASE_SSLMODE=prefer` in `.env.staging`, then `docker compose up -d --force-recreate`; or enable SSL on RDS (4.1) |
| Sign-in page says **DWS Hub could not be reached** | BE container cannot reach `test-dwshub.kpndomain.com` | check `extra_hosts` / `HUB_HOST_IP` in `deploy/staging/backend/docker-compose.yml`; `docker compose exec backend python -c "import urllib.request as u;print(u.urlopen('http://test-dwshub.kpndomain.com/api/sso/jwks').status)"` |
| Hub shows `invalid redirect_uri` / enforcement error | registration differs | 3.2: `sso_mode=oidc`, exact `oidc_redirect_uris`, same `SSO_CLIENT_ID` |
| Browser cannot open the Hub page after clicking the button | `authorization_endpoint` not reachable from PCs | set `SSO_AUTHORIZATION_URL` (3.3), restart BE |
| Back on sign-in page: **token … rejected** | wrong `SSO_CLIENT_ID` (aud), issuer mismatch, server clock | check client id; `timedatectl` (NTP on); Hub `issuer` |
| **has no access to this dashboard yet** | Hub user not registered | register their Hub email (9), or set `SSO_AUTO_PROVISION_ROLE` |
| **sign-in link has expired or was already used** | a second unknown sign-in within 2 minutes (back button, > 10 minutes at the Hub). A first one, e.g. opening the app from the DWS Hub tile, restarts the sign-in automatically (audit: `sso_launch_restarted`) | click **Sign in with DWS Hub** again; if every Hub tile launch ends here, collect `docker logs plantation-rainfall-frontend-staging 2>&1 \| grep /api/sso` on the FE server |
| Refresh log shows `WARNING: SAP PROD returned no rain readings for <month>` | SAP PROD has no current-month data yet (early month, or Syncera has not loaded it) | the refresh still loads (forecast updated, SAP date stays); ask Syncera if it lasts more than a day or two |
| Refresh fails: HTTP 429 after retries, or many `rate limited; waiting 60s` lines | Syncera rate limit lowered (about 6 a minute on 29 Oct 2026) | set a lower `SAP_REQUESTS_PER_MINUTE` (default 5, e.g. `4`) in `.env.staging` |
| `docker ps` shows `plantation-rainfall-staging-be-backend-run-…` as **unhealthy** | older image: the temporary refresh container inherited the web health check | harmless (the refresh log says whether it worked); rebuild the image (11) and refresh containers report healthy |
| Audit shows `172.x` Docker addresses | `TRUST_PROXY_HEADERS` not applied | compose sets it; check `docker compose config` |

Logs: `docker compose logs -f` in the respective `deploy/staging/*` folder.
