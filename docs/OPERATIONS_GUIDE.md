# Plantation Rainfall Dashboard — Operations Guide

This repository contains the data-pull pipelines, the PostgreSQL 18 database
schema and the dashboard web application. All data lives in PostgreSQL; the
pipelines download it from Open-Meteo and the SAP/Syncera API. Users never
import CSV files manually.

## Repository layout

| Folder | Contents |
| --- | --- |
| `backend/` | Python pipelines, API server and database code (`backend/Dockerfile`) |
| `backend/all_regions/` | Open-Meteo rainfall, temperature and soil-moisture pipeline (18 regions) |
| `backend/plantation/` | SAP/Syncera rainfall and estate-level Open-Meteo pipeline (in-house estates) |
| `backend/database/` | `schema.sql`, connection, repository, setup and legacy-import scripts |
| `backend/reference_data/` | `koordinat_estate_update.xlsx` (estate coordinates) |
| `frontend/` | Static dashboard pages (`*.html`, `css/`, `js/`) and the nginx image (`frontend/Dockerfile`, `frontend/nginx/`) |
| `frontend/inhouse/` | In-house plantation dashboard (single-page app) |
| `docs/` | This guide, the README review, the PRD and the Technical Design |
| `data/work/` | Temporary staging files created during a refresh (git-ignored) |

All commands below are run from the repository root.

## First-time setup

1. Install **Python 3.10 or later** and **PostgreSQL 18**.
2. Install the Python packages:

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. Create the settings file and fill in every value marked `REPLACE_ME`:

   ```powershell
   Copy-Item .env.example .env
   ```

   - `DATABASE_PASSWORD`: choose a strong password for the application database account.
   - `SYNCERA_DEV_CLIENT_SECRET` / `SYNCERA_PROD_CLIENT_SECRET`: the Syncera client secrets.
   - `DASHBOARD_PUBLIC_URL`: the address users open, e.g. `http://192.168.1.82:8080/`.

   `.env` contains live secrets. Never commit or share it (it is in `.gitignore`).

4. Create the database (run once, by someone with the PostgreSQL superuser password):

   ```powershell
   python -m backend.database.bootstrap
   ```

   It asks for the superuser login with hidden input, creates the `DATABASE_USER`
   role and `DATABASE_NAME` database from `.env`, and creates all tables. The
   superuser password is never stored.

5. **Upgrading a machine that ran the old file-based version?** Import its
   files once, before the first refresh. This keeps the archived forecast runs,
   which cannot be downloaded again:

   ```powershell
   python -m backend.database.import_legacy_files "C:\path\to\old\Plant Rainfall"
   ```

   If the database already has fresher weather data, import only the forecast
   archive, for example from CSVs copied into the git-ignored `resources/` folder:

   ```powershell
   python -m backend.database.import_legacy_files resources --only snapshots
   ```

   Older files that store Johor, Pahang and Perak separately are merged into
   "Pennisular" on import, the same way the dashboard combines them.

6. Create the first administrator account. It asks for an email address, full
   name and password (typed with hidden input):

   ```powershell
   python -m backend.auth.create_admin
   ```

   Sign in with that email and password, then register everyone else on
   **Administration → User management**.

## Users and access

Users sign in with their **email address and password**. Email addresses are
unique and not case-sensitive. Every page needs a signed-in user, and what a user
can open depends on their role:

| Role | All Regions pages | In-house Plantation | User management |
| --- | --- | --- | --- |
| Administrator | Yes | Yes | Yes |
| Plantation user | Yes | Yes | No |
| Regional viewer | Yes | No | No |

- **Register a user:** User management → *Register new user*. Enter their email,
  name and role, and set an initial password (*Generate* creates a strong one).
  Give it to the user securely; they can change it under *Change password* in the
  sidebar. Changing a user's email on *Edit* changes the address they sign in with.
- **Edit, disable, reset or unlock:** use the buttons on each user row. Disabling
  a user or resetting their password signs them out everywhere.
- **Lockout:** after `LOGIN_MAX_FAILED_ATTEMPTS` wrong passwords (default 5) an
  account is locked for `LOGIN_LOCKOUT_MINUTES` (default 15). An administrator can
  unlock it straight away. Repeated failures from one computer are also slowed down.
- **Safety rules:** administrators cannot disable themselves or remove their own
  administrator role, and the last active administrator cannot be removed.
- **Locked out of every admin account?** Run `python -m backend.auth.create_admin`
  on the server to create a new administrator.
- **Activity:** sign-ins, failed attempts and account changes are listed under
  *Recent activity* and stored in the `rainfall.auth_audit` table.
- **Roles** are stored in the `rainfall.app_role` and `rainfall.app_role_section`
  tables. A role's access can be changed there; refreshes do not overwrite it.

Sessions last `SESSION_HOURS` (default 12). When users reach the dashboard over
HTTPS, set `SESSION_COOKIE_SECURE=true` in `.env`.

## Recommended: refresh everything and open the dashboard

Double-click `RUN_RAINFALL_DASHBOARD.bat`, or run:

```powershell
python -m backend.run_dashboard
```

This performs, in order:

1. Applies any pending database schema changes.
2. All Regions: historical rainfall and temperature (2000 to yesterday), historical
   soil moisture (missing years only, from 2017), and the 14-day rainfall,
   temperature and soil-moisture forecast. Each forecast run is archived.
3. In-house plantations: SAP rainfall for every estate in `SAP_ESTATES` (DEV =
   historical, PROD = current), the Open-Meteo 92-day recent actuals and 14-day
   forecast for each estate in the workbook, then one transactional load into PostgreSQL.
4. Stops a dashboard server previously started by this launcher, starts a new
   one and opens `DASHBOARD_PUBLIC_URL`.

The terminal shows the latest confirmed SAP PROD actual date.

## Refresh data without opening the dashboard

```powershell
python -m backend.update_dashboard_data
```

Individual pipelines (normally not needed):

```powershell
python -m backend.all_regions.update_all_regions_data
python -m backend.plantation.update_all_rainfall_dashboard --workers 4
python -m backend.plantation.update_all_rainfall_dashboard --check
```

`--check` validates `.env` and the workbook without downloading anything.

## Launch the dashboard without refreshing data

```powershell
python -m backend.dashboard_server
```

Then open `http://localhost:8080/`, or `DASHBOARD_PUBLIC_URL` from another
computer on the network. Keep the server process running while the dashboard is in use.

The server publishes only the `frontend/` folder. Health check: `GET /api/health`.

### Page speed: caching

Pages load in well under 2 seconds because nothing is computed while a visitor waits:

- Database connections are pooled (`DATABASE_POOL_SIZE`, default 10) instead of opened per query.
- Every data response is kept in memory, compressed, and versioned by the `data_version`
  table, which each refresh bumps in the same transaction as its writes.
- A background warmer pre-builds every page's data at startup and checks for new data
  every 60 seconds. After a refresh, visitors see the previous data for up to about a
  minute while the new version is built in the background, then the new data.
- The All Regions refresh also stores the `/api/data` payload in `dashboard_cache`, so a
  restarted backend serves it immediately.
- Browsers keep CSS and JavaScript files for up to a year, so a page needs only its HTML
  and data requests. When the frontend image is built, every CSS/JS link is stamped with
  a hash of the file (`frontend/nginx/stamp_assets.py`); a changed file gets a new link,
  so nobody has to bump `?v=` by hand and users never get stale code.

Nothing needs to be cleared by hand; restarting the backend simply rebuilds the caches.

## Run the dashboard in Docker (separate frontend and backend)

Two containers, so staging and production can put them on separate servers:

| Container | Image | Role | Port |
| --- | --- | --- | --- |
| `plantation-rainfall-frontend` | `frontend/Dockerfile` (nginx) | Serves the pages; forwards `/api/*` and `/inhouse/data/*` to the backend | 8080 (published) |
| `plantation-rainfall-backend` | `backend/Dockerfile` (Python) | API, sign-in, roles; can also run the data refresh | 8000 (internal only) |

The database stays on PostgreSQL 18 on the host (`host.docker.internal`, or
`DOCKER_DATABASE_HOST` in `.env`). The backend reads `.env` at runtime; no
secrets are copied into either image.

```powershell
docker compose up -d --build     # build and start on http://localhost:8080/
docker compose logs -f           # follow both logs
docker compose down              # stop
docker compose run --rm backend python -m backend.update_dashboard_data   # refresh data inside Docker
```

How a page request works: the browser only ever talks to the frontend. Before
serving any page, nginx asks the backend (`GET /api/auth/check`) whether the
signed-in user may see it. The backend answers 204 (serve it) or 401/403 plus
the address to redirect to. All access rules therefore stay in the backend, and
the session cookie works unchanged because pages and API share one address.
Health checks: frontend `/healthz`, backend `/api/health`.

Do not run `python -m backend.dashboard_server` (or `RUN_RAINFALL_DASHBOARD.bat`,
which starts it) on the host while the containers run: both want port 8080. Use
`python -m backend.update_dashboard_data` for refreshes from the host instead.

### Staging and production on separate servers

The complete staging runbook (servers, ports, Apsara RDS, DWS Hub SSO) is
[DEPLOYMENT_STAGING.md](DEPLOYMENT_STAGING.md). In short:

1. **Backend server:** run the backend image with the production `.env`
   (database, Syncera secrets), `DASHBOARD_PORT=8000` and
   `TRUST_PROXY_HEADERS=true`. Allow port 8000 **only from the frontend server**
   (firewall or private network): with `TRUST_PROXY_HEADERS=true` the backend
   believes the visitor address the frontend reports.
2. **Frontend server:** run the frontend image with
   `BACKEND_URL=http://<backend-host>:8000` and publish port 8080 (or put it
   behind your load balancer).
3. **HTTPS:** terminate TLS at the load balancer or in front of nginx, then set
   `SESSION_COOKIE_SECURE=true` on the backend.
4. **Run the refresh on a schedule** on the backend server (Task Scheduler or
   cron): `python -m backend.update_dashboard_data`, or the
   `docker compose run --rm backend …` form above.

## Daily operating procedure

1. Connect to the internet and the company network/VPN required for SAP access.
2. Double-click `RUN_RAINFALL_DASHBOARD.bat`.
3. Wait for `All dashboard data was refreshed successfully.`
4. Confirm that the displayed `Latest PROD actual date` is reasonable.
5. Open or refresh the dashboard.

### When a refresh fails

- **In-house plantation data** is replaced only after every download has been
  validated, in a single database transaction. A failure leaves the live data untouched.
- **All Regions data** is written per step (historical rainfall, soil moisture,
  forecasts). Each step is atomic, but steps that finished before a failure keep
  their new data.
- Every step is recorded in the `rainfall.refresh_run` table with its status and error message.
- **Early in a month** the refresh stops with `PROD SAP data is stale` until SAP
  PROD contains a reading dated in the current month.
- **Only one refresh runs at a time.** A second refresh started while one is
  running stops at once with `Another data refresh is already running` (exit
  code 75) and changes nothing. Wait for the first to finish. The lock is a
  PostgreSQL advisory lock, so it is released automatically if a refresh
  crashes or its window is closed.

Read the failed-step message, correct the connection, credential or API issue,
and run the launcher again.

## Automated tests

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

The tests cover the SAP cleaning rules, the database repository, the refresh
lock and the Open-Meteo retry. Database tests create a throwaway PostgreSQL 18
cluster (`initdb`, port 55432, trust authentication) in a temporary folder and
remove it afterwards. They never use `.env` database settings, Open-Meteo or
the Syncera API. If `initdb` is not in `C:\Program Files\PostgreSQL\18\bin` or
on `PATH`, set `PG_BIN` to the PostgreSQL bin folder.

## Security

- `.env` holds the database password and the Syncera secrets; keep it private.
  `.env.example` is the safe template stored on GitHub.
- Every page requires sign-in. Passwords are stored only as scrypt hashes;
  sessions are server-side and the browser holds an HttpOnly, SameSite=Strict cookie.
- Traffic is plain HTTP on the LAN. For access beyond a trusted network, put the
  dashboard behind an HTTPS reverse proxy and set `SESSION_COOKIE_SECURE=true`.
- Email generation, PDF generation and hosted-site deployment are not part of this repository.
