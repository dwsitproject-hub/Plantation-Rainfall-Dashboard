# Production Deployment Guide — Plantation Rainfall Dashboard

Step-by-step runbook for deploying the dashboard to the DWS **production** environment,
with sign-in through the production **DWS Hub (OIDC)**. It follows the same layout as
the staging deployment ([DEPLOYMENT_STAGING.md](DEPLOYMENT_STAGING.md)); the
differences are listed in section 0.

| | |
| --- | --- |
| Frontend server | `172.28.80.50` — nginx container, host port **3070** |
| Backend server | `172.28.80.51` — Python container, host port **5070** (private address only) |
| Database | Apsara RDS for PostgreSQL `pgm-d9jn3khh0b3907w4.pgsql.ap-southeast-5.rds.aliyuncs.com:5432` |
| DWS Hub (production) | `https://dwshub.kpndomain.com` (issuer and all OIDC endpoints; resolves to `172.28.80.50`, HTTPS with a Let's Encrypt certificate) |
| Users open | **http://172.28.80.50:3070/** |
| Install folder (both servers) | `/opt/plantation-rainfall` |
| Code | GitHub `dwsitproject-hub/Plantation-Rainfall-Dashboard`, a **release tag on `main`** (e.g. `v1.0.0`) |

```
 Browser ──► FE 172.28.80.50:3070  (nginx: pages, /api proxy, page-access check)
                   │  http://172.28.80.51:5070  (private network, FE only)
                   ▼
            BE 172.28.80.51:5070  (Python API, sessions, roles, data refresh)
              │                │                      │
              ▼                ▼                      ▼
     Apsara RDS PostgreSQL   DWS Hub (HTTPS)        Internet: Open-Meteo, SAP Syncera PROD
     (whitelisted)           dwshub.kpndomain.com
                             (OIDC token + JWKS)

 Sign-in: Browser ─► FE /api/sso/login ─► DWS Hub authorize ─► FE /api/sso/callback
          (backend exchanges the code with PKCE and verifies the ID token via JWKS)
```

Allow about **2 hours** in a change window: steps 1–4 prepare, steps 5–9 deploy
(the first data refresh alone takes 30–45 minutes), step 10 verifies.

---

## 0. What changes on the servers, and how production differs from staging

New on the servers (nothing existing is changed):

- One new container per server: `plantation-rainfall-frontend-production` (FE) and
  `plantation-rainfall-backend-production` (BE). No existing container, port or
  network is touched. Both servers already run many other apps, so only the free
  ports **3070** (FE) and **5070** (BE) are used.
- One new database (`plantation_rainfall`) and account (`rainfall_app`) on the production RDS instance.
- One new app registration in the **production** DWS Hub Admin.
- Two cron entries on the backend server for the data refresh.

| | Staging | Production |
| --- | --- | --- |
| Servers | FE `172.28.92.56`, BE `172.28.92.57` | FE `172.28.80.50`, BE `172.28.80.51` |
| Ports | FE 3070, BE 5070 | FE 3070, BE 5070 (both free, checked 23 Oct 2026) |
| RDS | `pgm-d9jx9o06qae8gf3h…` | `pgm-d9jn3khh0b3907w4…` |
| DWS Hub | `http://test-dwshub.kpndomain.com` (HTTP) | **`https://dwshub.kpndomain.com` (HTTPS)** |
| Hub client id | `plantation-rainfall-staging` | `plantation-rainfall` |
| Code | branch `restructure/production-layout` | **release tag on `main`** |
| Deployment folder | `deploy/staging/*` | `deploy/production/*` |
| Backend settings file | `deploy/staging/backend/.env.staging` | `deploy/production/backend/.env.production` |
| Forecast archive | imported from the old local installation | **exported from staging** (step 1.2) |
| Users | staging accounts | registered again in production (nothing is copied) |

Production Hub, checked 23 Oct 2026 from the IT PC:

```
issuer                  https://dwshub.kpndomain.com
authorization_endpoint  https://dwshub.kpndomain.com/api/sso/authorize
token_endpoint          https://dwshub.kpndomain.com/api/sso/token
jwks_uri                https://dwshub.kpndomain.com/api/sso/jwks
token auth "none" (public client, PKCE S256), ID tokens RS256, scopes openid profile email
certificate             Let's Encrypt, CN dwshub.kpndomain.com, valid until 6 Jan 2027
http://dwshub.kpndomain.com  → 301 to https://
```

---

## 1. Preparation (on your PC)

### 1.1 Release the code

Production runs a **tagged release on `main`**, never a working branch. Promote the
version that passed staging (and UAT, if your release process requires it):

1. Open a pull request from `restructure/production-layout` (or `UAT`) into `main` on GitHub,
   review it and merge it.
2. Tag the merged commit:

   ```powershell
   cd "D:\Claude\Plant Rainfall"
   git fetch origin
   git tag -a v1.0.0 origin/main -m "Plantation Rainfall Dashboard 1.0.0 (first production release)"
   git push origin v1.0.0
   ```

Throughout this guide `v1.0.0` is the release; use your actual tag.

### 1.2 Export the forecast archive from staging

The archive of past forecast runs powers *Forecast changes* and *Last week accuracy*
and **cannot be downloaded again**. Staging has the most complete copy (every run since
its deployment), so export it there.

On the **staging backend server** `172.28.92.57`:

```bash
cd /opt/plantation-rainfall/deploy/staging/backend
mkdir -p export && chmod 777 export
docker compose run --rm -v "$PWD/export:/app/export" backend python -c "import csv; from backend.database import repository as r; rows=r.read_snapshots(); f=open('/app/export/rainfall_forecast_snapshots.csv','w',newline='',encoding='utf-8-sig'); w=csv.DictWriter(f,fieldnames=['run_date','country','region','latitude','longitude','date','rainfall_mm','data_type','retrieved_at_utc','source'],extrasaction='ignore'); w.writeheader(); w.writerows(rows); f.close(); print(len(rows),'rows')"
ls -l export/
```

Download `export/rainfall_forecast_snapshots.csv` to your PC (MobaXterm file panel);
you upload it to the production backend in step 5.5. Delete the `export` folder on
staging afterwards (`rm -r export`).

### 1.3 Information to collect

- [ ] Production RDS console access (database account, whitelist, SSL).
- [ ] Production DWS Hub Admin access (to register the app).
- [ ] A strong password for the production `rainfall_app` database account — **different
      from staging**; keep it in your password manager.
- [ ] The Syncera PROD client secret (from staging's `.env.staging` or your local `.env`;
      copy the value only).
- [ ] The office network / VPN address ranges that should reach the dashboard (step 7).
- [ ] The release tag from 1.1.

### 1.4 Before the change window

- Tell the Hub owner a new app registration is coming (step 3).
- Users start with an empty user list: prepare the list of people, their DWS Hub
  emails and roles (Administrator / Plantation user / Regional viewer) for step 9.

---

## 2. Pre-flight checks on the servers

### 2.1 Frontend server `172.28.80.50`

```bash
# Port 3070 must be free (no output = free)
ss -ltnp | grep -E ':3070\b'
docker ps --format '{{.Names}}\t{{.Ports}}' | grep -E ':3070->' || echo "3070 free in Docker"

docker --version && docker compose version     # Docker 20.10+ and Compose v2 required
curl -s -o /dev/null -w '%{http_code}\n' http://172.28.80.51:4000/   # any code = BE server reachable
```

> Ports seen in `docker ps` on 23 Oct 2026: 2001, 3001, 3020, 3042, 3080, 3100, 8000,
> 8080, 8081, 8443, and 3040/8090 on localhost. **3070 is not used.** The DWS Hub itself
> runs on this server (`downstream-hub-web`) behind the HTTPS front end on port 443.
> If the check shows 3070 in use, pick another free port and use it everywhere this
> guide says 3070 (`FRONTEND_HOST_PORT`, the Hub redirect URI, `SSO_REDIRECT_URI`,
> `DASHBOARD_PUBLIC_URL`).

### 2.2 Backend server `172.28.80.51`

```bash
ss -ltnp | grep -E ':5070\b'                     # no output = free
docker ps --format '{{.Names}}\t{{.Ports}}' | grep -E ':5070->' || echo "5070 free in Docker"
docker --version && docker compose version

# Outbound access the refresh needs
curl -s -o /dev/null -w 'open-meteo %{http_code}\n' "https://api.open-meteo.com/v1/forecast?latitude=0&longitude=101&daily=precipitation_sum"
curl -s -o /dev/null -w 'open-meteo archive %{http_code}\n' "https://archive-api.open-meteo.com/v1/archive?latitude=0&longitude=101&start_date=2026-01-01&end_date=2026-01-02&daily=precipitation_sum"
curl -s -o /dev/null -w 'syncera-prod %{http_code}\n' https://syncera.gamasap.com/api/v1/auth
# Production DWS Hub (the backend container pins the name to 172.28.80.50)
curl -s https://dwshub.kpndomain.com/api/sso/.well-known/openid-configuration ; echo
curl -s --resolve dwshub.kpndomain.com:443:172.28.80.50 -o /dev/null -w 'hub via 172.28.80.50 %{http_code}\n' https://dwshub.kpndomain.com/api/sso/jwks
getent hosts dwshub.kpndomain.com || echo "not in server DNS (fine: pinned in the container)"
# RDS reachable on 5432 (after the whitelist in step 4.2)
timeout 5 bash -c '</dev/tcp/pgm-d9jn3khh0b3907w4.pgsql.ap-southeast-5.rds.aliyuncs.com/5432' && echo "RDS port open"
timedatectl | grep 'Time zone'
```

Expected: Open-Meteo `200` twice; Syncera any HTTP code other than `000` (e.g. `404`/`405`
for a GET on the auth URL); the Hub returns JSON whose `issuer` is
`https://dwshub.kpndomain.com`; `hub via 172.28.80.50 200`. **Write down the time zone**:
the cron lines in step 8 depend on it.

> Ports seen in `docker ps` on 23 Oct 2026: 3000, 4000, 4001, 5000, 5001, 5003, 5010,
> 5050, 5434, 8001, 8091, 13000, and 5433 on localhost. **5070 is not used.**
>
> Checked 23 Oct 2026 (host `ECS-DB`): 5070 free on the host and in Docker, Docker 29.1.3,
> Compose v2.31.0, Open-Meteo 200 (forecast and archive), Syncera PROD 405, Hub discovery
> OK (issuer `https://dwshub.kpndomain.com`), `hub via 172.28.80.50 200`, the server DNS
> already resolves `dwshub.kpndomain.com` to `172.28.80.50`, RDS port open. The server
> clock is **Asia/Shanghai (+08:00)**, so step 8 uses the 07:30 / 13:00 cron lines.

---

## 3. Register the app in the production DWS Hub

### 3.1 Read the Hub discovery document (backend server)

```bash
curl -s https://dwshub.kpndomain.com/api/sso/.well-known/openid-configuration | python3 -m json.tool
```

Compare with the values in section 0. What each value is used for:

| Field | Used for |
| --- | --- |
| `issuer` | Checked automatically against every ID token (`iss`). Nothing to configure. |
| `authorization_endpoint` | Users' **browsers** are sent here; it must open from user PCs (3.3). |
| `token_endpoint`, `jwks_uri` | Called by the **backend container**; must be reachable from `172.28.80.51` (checked in 5.3). |

### 3.2 Create the registration in production Hub Admin

| Hub field | Value |
| --- | --- |
| App name | Plantation Rainfall Dashboard |
| `sso_mode` | `oidc` |
| `oauth_client_id` | `plantation-rainfall` |
| `oidc_redirect_uris` | `http://172.28.80.50:3070/api/sso/callback` (exact, no trailing slash) |
| App / launch URL (Hub tile) | `http://172.28.80.50:3070/api/sso/login` |

The production Hub advertises `token_endpoint_auth_methods_supported: ["none"]`, so this
is a public client with PKCE: leave `SSO_CLIENT_SECRET` empty.

> Unlike staging, production's legacy `/api/sso/bridge` still answers (`400 Missing ref`
> instead of `410`). The dashboard does not use the bridge, so this does not matter;
> the registration must still be `sso_mode = oidc`.

### 3.3 Check the authorize address from a user PC

Open `https://dwshub.kpndomain.com/api/sso/authorize` in a browser on the office
network/VPN. A Hub page, a redirect or a Hub error about missing parameters means it
is reachable; `SSO_AUTHORIZATION_URL` then stays empty.

---

## 4. Database on the production Apsara RDS

### 4.1 Version and SSL

- RDS console → instance → **Basic Information**: note the PostgreSQL version.
  Version 14 or later is fine; the dashboard was built and tested on PostgreSQL 18.
- **Data Security → SSL**: enabling SSL is recommended for production. The template uses
  `DATABASE_SSLMODE=require`; if SSL stays off, use `prefer` in step 5.2 (staging had to,
  see its troubleshooting).

### 4.2 Whitelist the backend server

RDS console → **Whitelist Settings** → add `172.28.80.51` to a whitelist group
(e.g. `plantation_rainfall_be`). Re-run the RDS port check from 2.2: it should print
`RDS port open`.

### 4.3 Create the account and database

**Option A — RDS console (recommended):**

1. **Accounts → Create Account**: name `rainfall_app`, type **Standard Account**, the
   production password from 1.3.
2. **Databases → Create Database**: name `plantation_rainfall`, authorized account
   `rainfall_app`, character set **UTF8**.
3. **Allow `rainfall_app` to create the schema** (otherwise `init_db` fails with
   `permission denied for database plantation_rainfall`, as it did on staging). Signed in
   with the **privileged account** (console → **Log On to Database** / DMS, database
   `plantation_rainfall`), run once:

   ```sql
   GRANT CONNECT, CREATE, TEMPORARY ON DATABASE plantation_rainfall TO rainfall_app;
   ```

   Alternative from the backend server (asks for the privileged password):

   ```bash
   docker run --rm -it postgres:17-alpine psql "host=pgm-d9jn3khh0b3907w4.pgsql.ap-southeast-5.rds.aliyuncs.com port=5432 dbname=plantation_rainfall user=<privileged account> sslmode=prefer" -c "GRANT CONNECT, CREATE, TEMPORARY ON DATABASE plantation_rainfall TO rainfall_app;"
   ```

**Option B — command line with the RDS privileged account:** after step 5.2, run:

```bash
cd /opt/plantation-rainfall/deploy/production/backend
docker compose run --rm backend python -m backend.database.bootstrap
```

It asks for the privileged account login (hidden input, never stored) and creates
`rainfall_app` (password from `.env.production`), the database and the schema.

### 4.4 Backups

Console → **Backup and Restoration**: automated backups on, retention at least **7 days**
(30 recommended for production). Take a manual backup before every release (step 11).

---

## 5. Backend server `172.28.80.51`

### 5.1 Get the code

```bash
sudo mkdir -p /opt/plantation-rainfall && sudo chown "$USER" /opt/plantation-rainfall
cd /opt/plantation-rainfall

# One read-only deploy key per server (GitHub → repo → Settings → Deploy keys → Add, "Allow write" OFF)
ssh-keygen -t ed25519 -C "plantrainfall-prod-be" -f ~/.ssh/id_ed25519_plantrainfall -N ""
cat ~/.ssh/id_ed25519_plantrainfall.pub        # paste into GitHub as a deploy key
cat >> ~/.ssh/config <<'EOF'
Host github-plantrainfall
  HostName github.com
  User git
  IdentityFile ~/.ssh/id_ed25519_plantrainfall
  IdentitiesOnly yes
EOF
ssh -T git@github-plantrainfall                # "successfully authenticated"

git clone git@github-plantrainfall:dwsitproject-hub/Plantation-Rainfall-Dashboard.git .
git checkout v1.0.0                            # the release tag from 1.1
git describe --tags                            # shows v1.0.0
```

> If `~/.ssh/id_ed25519_plantrainfall` or the `github-plantrainfall` entry already exists
> on this server, reuse it instead of creating it again.

### 5.2 Settings and secrets

```bash
cd /opt/plantation-rainfall/deploy/production/backend
cp .env.production.example .env.production
chmod 600 .env.production
nano .env.production
```

Fill in:

| Setting | Value |
| --- | --- |
| `DATABASE_PASSWORD` | the production `rainfall_app` password from 4.3 |
| `DATABASE_SSLMODE` | `require` if RDS SSL is on, otherwise `prefer` |
| `SYNCERA_PROD_CLIENT_SECRET` | the Syncera PROD secret (1.3) |
| `SSO_CLIENT_ID` | as registered in 3.2 (`plantation-rainfall`) |
| `SSO_CLIENT_SECRET` | empty (public client with PKCE) |
| `SSO_REDIRECT_URI` | `http://172.28.80.50:3070/api/sso/callback` (must match the Hub exactly) |
| `SSO_DISCOVERY_URL` | `https://dwshub.kpndomain.com/api/sso/.well-known/openid-configuration` (already in the template) |
| `SSO_AUTHORIZATION_URL` | empty, unless step 3.3 required it |
| `SSO_AUTO_PROVISION_ROLE` | empty (recommended: only registered users get in) |
| `SESSION_COOKIE_SECURE` | `false` while users open the dashboard over `http://` (see section 13) |

Check that nothing is left unset:

```bash
grep -n 'REPLACE_ME' .env.production || echo "all values set"
```

### 5.3 Build, create the schema and start

The health check reads the database, so create the schema before starting. (If you used
Option B in 4.3, bootstrap already created it; running `init_db` again is harmless.)

```bash
cd /opt/plantation-rainfall/deploy/production/backend
docker compose build
docker compose run --rm backend python -m backend.database.init_db
docker compose up -d
docker compose ps                              # STATUS: Up (healthy) after ~20 s
curl -s http://172.28.80.51:5070/api/health ; echo
```

Expected: `Database schema is up to date; 18 weather regions seeded.`, then
`{"status":"ok","plantationVersion":"plantation-v0"}`.

If health returns `{"status":"error","problem":...}`, the problem says which:
*Database schema missing* (run the `init_db` line) or *Database unreachable* (password,
whitelist or SSL mode; the exact error is in `docker compose logs backend`).

Confirm the backend container reaches the production Hub over HTTPS and trusts its
certificate:

```bash
docker compose exec backend python -c "import urllib.request as u; print(u.urlopen('https://dwshub.kpndomain.com/api/sso/jwks', timeout=10).status)"   # 200
```

### 5.4 Break-glass administrator

Create one local administrator (works even if the Hub is down). **Use the same email as
your production DWS Hub account**: your first Hub sign-in then links to it.

```bash
docker compose run --rm backend python -m backend.auth.create_admin
```

### 5.5 Import the forecast archive (from staging)

Upload `rainfall_forecast_snapshots.csv` (step 1.2) to
`/opt/plantation-rainfall/deploy/production/backend/import/` (MobaXterm file panel, or
`scp` from your PC), then:

```bash
cd /opt/plantation-rainfall/deploy/production/backend
mkdir -p import            # if the upload did not create it
docker compose run --rm -v "$PWD/import:/app/import:ro" backend \
  python -m backend.database.import_legacy_files import --only snapshots
```

Expected: `ok rainfall_forecast_snapshots.csv: <N> runs, …` (one run per refresh day
on staging, plus the runs imported there). Keep the file until the verification in step 10 is done, then delete it.

### 5.6 First data refresh (30–45 minutes)

The database is empty, so the first refresh downloads everything once:

- Open-Meteo history: rainfall and temperature since 2000, soil moisture since 2017
  (later refreshes only download the last two weeks);
- SAP PROD: previous months (`rain-data`) **and** the current month (`rain-data-current`)
  for 59 estates — 118 requests, paced at 5 a minute to stay under Syncera's limit.

Run it in the background so a dropped SSH session does not stop it:

```bash
cd /opt/plantation-rainfall/deploy/production/backend
chmod +x refresh.sh
nohup ./refresh.sh >/dev/null 2>&1 &
sleep 5; tail -f "$(ls -t logs/refresh-*.log | head -1)"     # Ctrl+C stops watching, not the refresh
```

Expected in the log:

- `SAP previous months: pulling, because no SAP history has been pulled yet`
- `prod: rain-data-current, rain-data for 59 estates = 118 requests at 5/minute (about 24 min)`
- a few `rate limited; waiting 60s` lines are normal (handled automatically)
- `Dashboard latest SAP date: <yesterday or the day before>`
- `All dashboard data was refreshed successfully.`

Check the result:

```bash
grep -E "previous months|SAP PROD rows|Dashboard latest SAP date|WARNING|successfully|stopped" "$(ls -t logs/refresh-*.log | head -1)"
curl -s http://172.28.80.51:5070/api/health ; echo     # plantationVersion is now plantation-v1 or higher
```

---

## 6. Frontend server `172.28.80.50`

### 6.1 Get the code

Same as 5.1, with its own read-only deploy key (comment `plantrainfall-prod-fe`), and the
same release tag:

```bash
sudo mkdir -p /opt/plantation-rainfall && sudo chown "$USER" /opt/plantation-rainfall
cd /opt/plantation-rainfall
# ... create ~/.ssh/id_ed25519_plantrainfall, add it as a read-only deploy key, ssh config as in 5.1 ...
git clone git@github-plantrainfall:dwsitproject-hub/Plantation-Rainfall-Dashboard.git .
git checkout v1.0.0
```

### 6.2 Settings

```bash
cd /opt/plantation-rainfall/deploy/production/frontend
cp .env.example .env
nano .env
```

| Setting | Value |
| --- | --- |
| `BACKEND_URL` | `http://172.28.80.51:5070` (backend private address and port from step 5) |
| `FRONTEND_HOST_PORT` | `3070` (must match the Hub redirect URI and `SSO_REDIRECT_URI`) |

`docker compose` reads `.env` from this folder automatically; if a value is missing,
startup stops with `Set BACKEND_URL in deploy/production/frontend/.env`. Check what
compose will use:

```bash
docker compose config | grep -E 'BACKEND_URL|published'
```

### 6.3 Build and start

```bash
cd /opt/plantation-rainfall/deploy/production/frontend
docker compose up -d --build
docker compose ps                                           # Up (healthy)
curl -s http://127.0.0.1:3070/healthz                       # ok
curl -s http://127.0.0.1:3070/api/health ; echo             # {"status":"ok",...}  (through to the backend)
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://127.0.0.1:3070/   # 302 .../login.html
```

The build stamps every CSS/JS link with a content hash (`Stamped 88 CSS/JS links with
content hashes.` in the build output), so browsers cache them safely.

---

## 7. Network access (Alibaba Cloud security groups)

| Server | Inbound rule | Source |
| --- | --- | --- |
| BE `172.28.80.51` | TCP **5070** | `172.28.80.50/32` **only** (the frontend server) |
| FE `172.28.80.50` | TCP **3070** | the office network / VPN ranges from 1.3 |
| RDS | 5432 (whitelist) | `172.28.80.51` |

> The backend trusts the visitor address the frontend reports (`TRUST_PROXY_HEADERS=true`)
> for audit logs and sign-in throttling, so port 5070 must not be reachable from anywhere else.

Check from a PC that is **not** the frontend server: `http://172.28.80.51:5070/api/health`
must time out, and `http://172.28.80.50:3070/` must open the sign-in page.

---

## 8. Data refresh schedule (cron on the backend server)

The refresh runs twice a day, Monday–Saturday:

- **06:30 WIB** — the new Open-Meteo forecast is ready for the start of the working day.
- **12:00 WIB** — picks up yesterday's SAP rain readings (estates enter them during the morning).

A normal refresh takes about **13 minutes** (current month only: 59 SAP requests at 5 a
minute). On the first refresh of a month, and once a week, previous months are pulled
again as well (about 25 minutes). Only one refresh runs at a time; a second one exits at once
(`Refresh FAILED with exit code 75`) and changes nothing.

**Cron uses the server clock.** Ubuntu's cron ignores `CRON_TZ` lines (on staging the
`CRON_TZ=Asia/Jakarta` line had no effect), so write the times in the time zone you noted
in 2.2:

```bash
crontab -l > ~/crontab.backup.$(date +%Y%m%d)    # keep a copy of the existing entries
crontab -e
```

If the server is on **Asia/Shanghai (+08:00)**, like staging:

```
# Plantation Rainfall production refresh (server time = WIB + 1 hour)
# 07:30 server time = 06:30 WIB: new forecast for the working day
30 7 * * 1-6 /opt/plantation-rainfall/deploy/production/backend/refresh.sh
# 13:00 server time = 12:00 WIB: yesterday's SAP rain readings
0 13 * * 1-6 /opt/plantation-rainfall/deploy/production/backend/refresh.sh
```

If the server is on **Asia/Jakarta (+07:00)**:

```
# Plantation Rainfall production refresh (server time = WIB)
30 6 * * 1-6 /opt/plantation-rainfall/deploy/production/backend/refresh.sh
0 12 * * 1-6 /opt/plantation-rainfall/deploy/production/backend/refresh.sh
```

After the first scheduled run, confirm it started at the intended WIB time (log file
names use the server clock):

```bash
ls -l --time-style=+%H:%M /opt/plantation-rainfall/deploy/production/backend/logs | tail -3
```

Logs: `/opt/plantation-rainfall/deploy/production/backend/logs/refresh-*.log` (kept 30
days). Every step is also recorded in the `rainfall.refresh_run` table. The refresh runs
on Jakarta time inside the container, so forecast run dates are WIB dates.

---

## 9. Users and roles

Production starts with only the break-glass administrator (5.4); staging accounts are
not copied.

1. Sign in at http://172.28.80.50:3070/ with **Sign in with DWS Hub** (or the
   break-glass email + password).
2. **Administration → User management → Register new user** for each person from 1.4:
   their **production DWS Hub email**, name and role (Administrator / Plantation user /
   Regional viewer). Leave the password **empty** — they sign in with DWS Hub.
3. On their first Hub sign-in the account is linked to their Hub user id (`sub`); it then
   keeps working even if their email changes in the Hub. Linked users show a **DWS Hub** badge.

Hub users who are not registered see "has no access to this dashboard yet".

---

## 10. Verification checklist

| # | Check | Expected |
| --- | --- | --- |
| 1 | `docker ps` on both servers | both new containers `Up (healthy)`; **no other container restarted** (compare uptimes with before) |
| 2 | Open http://172.28.80.50:3070/ | redirected to the sign-in page with the **Sign in with DWS Hub** button |
| 3 | Click **Sign in with DWS Hub** | production Hub sign-in (HTTPS), then back on the dashboard (first time: account linked) |
| 4 | Launch from the DWS Hub tile | lands on the dashboard (the first launch may restart sign-in once; audit `sso_launch_restarted`) |
| 5 | Sidebar | your name and role; sections match your role |
| 6 | All Regions pages | coverage line shows yesterday as latest actual |
| 7 | In-house → Weather overview | 62 estates, latest SAP date = yesterday or the day before |
| 8 | Seasonality / cumulative pages | history back to 2020–2021 (from SAP `rain-data`) |
| 9 | Last week accuracy / Forecast changes | numbers for all regions (from the imported archive) |
| 10 | User management → Recent activity | `sso_login_succeeded` with your **real PC address** (not a Docker address) |
| 11 | Regional viewer test user opens `/inhouse/` | redirected to `/?denied=1` |
| 12 | `http://172.28.80.51:5070` from your PC | not reachable |
| 13 | Page speed | pages load in under 2 seconds on a repeat visit |
| 14 | After the first scheduled run (step 8) | new `logs/refresh-*.log` ending with `All dashboard data was refreshed successfully.` |

---

## 11. Releasing updates later

1. Deploy and verify the change on **staging** first.
2. Merge it into `main` and tag it (`v1.0.1`, `v1.1.0`, ...), as in 1.1.
3. Take a manual RDS backup (console → Backup and Restoration → Back Up Instance).
4. Deploy the tag:

```bash
# backend server 172.28.80.51
cd /opt/plantation-rainfall && git fetch --tags && git checkout v1.0.1
cd deploy/production/backend && docker compose build
docker compose run --rm backend python -m backend.database.init_db   # schema first: additive and idempotent
docker compose up -d

# frontend server 172.28.80.50 (only when frontend files changed; harmless otherwise)
cd /opt/plantation-rainfall && git fetch --tags && git checkout v1.0.1
cd deploy/production/frontend && docker compose up -d --build
```

Check the release notes for new settings in `.env.production.example` and add them to
`.env.production` before `docker compose up -d`.

**Rollback:** `git checkout <previous tag>` on the affected server, then
`docker compose up -d --build`. Schema changes are additive, so older code keeps working
with the newer schema. Restore the RDS backup only if data itself was damaged.

**Backups:** RDS automated backups (4.4). The forecast archive (`rainfall.forecast_snapshot`)
is the only data that cannot be downloaded again; everything else is rebuilt by a refresh.

**Remove completely:** `docker compose down` in both `deploy/production/*` folders, delete
`/opt/plantation-rainfall`, remove the two cron lines, the Hub registration, the
security-group rules and (optionally) the RDS database and account.

---

## 12. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `docker compose` says `Set BACKEND_URL in deploy/production/frontend/.env` | frontend `.env` missing or incomplete | `cp .env.example .env` in `deploy/production/frontend` (6.2) |
| FE container exits: `host not found in upstream` | `BACKEND_URL` wrong or BE down when FE started | start BE first; check `BACKEND_URL`; `docker compose up -d` again |
| Pages show **502 Bad Gateway** | FE cannot reach BE `172.28.80.51:5070` | security group rule (7); `curl http://172.28.80.51:5070/api/health` from the FE server |
| `init_db`: `permission denied for database plantation_rainfall` | `rainfall_app` may not create objects | run the GRANT in 4.3 step 3 with the privileged account, then `init_db` again |
| `/api/health` → `Database schema missing` | `init_db` not run yet | `docker compose run --rm backend python -m backend.database.init_db` |
| BE unhealthy, log shows `password authentication failed` / `timeout` | DB password, whitelist, SSL mode | 4.2 whitelist; `.env.production` |
| `server does not support SSL, but SSL was required` | `DATABASE_SSLMODE=require` but RDS SSL is off | set `DATABASE_SSLMODE=prefer` in `.env.production`, then `docker compose up -d --force-recreate`; or enable SSL on RDS (4.1) |
| Sign-in page says **DWS Hub could not be reached** | BE container cannot reach `dwshub.kpndomain.com:443` | check the jwks command in 5.3; `HUB_HOST_IP` / `extra_hosts` in `deploy/production/backend/docker-compose.yml`; the Hub's HTTPS front end on `172.28.80.50` must accept connections from `172.28.80.51` |
| `CERTIFICATE_VERIFY_FAILED` in the backend log | the Hub certificate expired or was replaced by an untrusted one | the Hub owner renews it (Let's Encrypt, valid until 6 Jan 2027 when checked) |
| Hub shows `invalid redirect_uri` / enforcement error | registration differs | 3.2: `sso_mode=oidc`, exact `oidc_redirect_uris`, same `SSO_CLIENT_ID` |
| Back on sign-in page: **token … rejected** | wrong `SSO_CLIENT_ID` (aud), issuer mismatch, server clock | client id `plantation-rainfall`; issuer `https://dwshub.kpndomain.com`; `timedatectl` (NTP on) |
| **has no access to this dashboard yet** | Hub user not registered | register their Hub email (9) |
| **sign-in link has expired or was already used** | a second unknown sign-in within 2 minutes (back button, > 10 minutes at the Hub) | click **Sign in with DWS Hub** again; if every Hub tile launch ends here, collect `docker logs plantation-rainfall-frontend-production 2>&1 \| grep /api/sso` |
| Refresh log shows `WARNING: SAP PROD returned no rain readings for <month>` | SAP PROD has no current-month data yet (early month, or Syncera has not loaded it) | the refresh still loads (forecast updated, SAP date stays); ask Syncera if it lasts more than a day or two |
| Many `rate limited; waiting 60s` lines, or estates failing with HTTP 429 | Syncera's limit is lower than expected | set a lower `SAP_REQUESTS_PER_MINUTE` (e.g. `4`) in `.env.production`, then `docker compose up -d` |
| `docker ps` shows `plantation-rainfall-prod-be-backend-run-…` as **unhealthy** | a refresh started before release v1.0.1: its temporary container inherited the web health check | harmless (the refresh's own log says whether it worked); from v1.0.1 on, refresh containers report healthy |
| Refresh runs at the wrong hour | cron uses the server clock (`CRON_TZ` is ignored) | 8: check `timedatectl`, adjust the hours |
| Audit shows `172.x` Docker addresses | `TRUST_PROXY_HEADERS` not applied | compose sets it; check `docker compose config` |

Logs: `docker compose logs -f` in the respective `deploy/production/*` folder.

---

## 13. Recommended after go-live

- **Serve the dashboard over HTTPS.** The Hub already runs HTTPS with a Let's Encrypt
  certificate on `172.28.80.50`. Publishing the dashboard the same way (for example
  `https://rainfall.kpndomain.com` through that HTTPS front end, proxying to
  `127.0.0.1:3070`) protects passwords and session cookies on the network. Afterwards:
  set `SESSION_COOKIE_SECURE=true`, `DASHBOARD_PUBLIC_URL` and `SSO_REDIRECT_URI` to the
  HTTPS address, update the Hub registration (redirect URI and launch URL), and restrict
  port 3070 to the front end only.
- **Turn off password sign-in for normal users** once everyone uses the Hub: keep
  `LOCAL_LOGIN_ENABLED=true` only as long as the break-glass administrator needs it.
- **Monitor** `http://172.28.80.51:5070/api/health` (from the FE server) and the newest
  `logs/refresh-*.log` with your existing monitoring.
- **Rotate secrets** that have been shared in documents (the Syncera API guide PDF
  contains client secrets) and keep `.env.production` readable by root only (`chmod 600`).
