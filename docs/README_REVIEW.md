# README Review: Documentation vs. Code

Review of the original `README.txt` against the code at commit `91ee602`
("Document dashboard setup and refresh workflow"), carried out on 16 Oct 2026
before the production restructure.

**Status:** *Fixed* = resolved by the restructure. *Open* = needs a business or
infrastructure decision.

## 1. Where the README and the code disagreed

| # | README says | Code actually did | Status |
| --- | --- | --- | --- |
| 1 | On another computer, use `http://localhost:8080/`. | The launcher always opened the hard-coded `http://192.168.1.82:8080/`, so on any other PC the browser opened a dead address. The SAP refresh also printed that address. | **Fixed:** `DASHBOARD_PUBLIC_URL` in `.env`. |
| 2 | "If the update fails, the existing live dashboard data is not replaced." | True only for the SAP/in-house section, which staged into a temp folder before promoting. All Regions CSVs were overwritten step by step, and they had already been replaced by the time a later SAP failure happened. | **Fixed for SAP:** one database transaction. All Regions steps are each atomic and audited; the guide now states this. |
| 3 | `requirements.txt` lists the needed packages. | `requests` was listed but never imported (everything uses `urllib`). `keyring` was imported as an optional fallback but not listed. | **Fixed:** requirements are now `openpyxl`, `pandas`, `psycopg2-binary`, `python-dotenv`. |
| 4 | Secrets live in `sap_credentials.py`. | A second path read secrets from Windows Credential Manager (`keyring`) and could prompt interactively, so there were two sources of truth. | **Fixed:** `.env` (or real environment variables) is the only source. |
| 5 | The one-click workflow lists six operations. | Correct overall, but README steps 1 and 2 are interleaved in the code (historical rain and temperature, historical soil, forecast rain and temperature, forecast soil). | **Fixed:** the guide describes the real order. |
| 6 | Not mentioned. | The SAP refresh fails whenever PROD has no reading dated in the current month. This happens on the first days of every month, and the README doesn't warn about it. | **Documented** in the operations guide. Whether to relax it is open (see 3.2). |
| 7 | Not mentioned. | The server has an in-house dashboard at `/inhouse/` and five JSON API endpoints. Three pages (`monthly-seasonality`, `temperature-monthly-seasonality`, `rainfall-summary`) are not in the sidebar. | Documented in the PRD and Technical Design. |
| 8 | Not mentioned. | `.gitignore` excluded `.weather_update.lock`, but no code creates a lock, so two refreshes can run at the same time. | **Fixed:** PostgreSQL advisory lock in `backend/update_dashboard_data.py`; a second refresh exits with code 75 (tested). The unused `.weather_update.lock` entry can be removed from `.gitignore`. |
| 9 | Not mentioned. | The launcher killed processes matching `02_launch_dashboard.py`, a script name that no longer exists. | **Fixed:** the pattern now matches `backend.dashboard_server`. |

## 2. Defects found in the code

| # | Severity | Finding | Status |
| --- | --- | --- | --- |
| D1 | **Critical (security)** | `dashboard_server.py` served the whole repository folder as static files. Anyone on the LAN could download `/sap_credentials.py` (live SAP secrets), every CSV, and the `.git` folder. | **Fixed:** only `frontend/` is published; `/.env`, `/backend/*` and `/.git/*` return 404 (tested). |
| D2 | High | Client IDs, API base URLs, the LAN IP and the 59-estate list were hard-coded in Python. | **Fixed:** all moved to `.env`. |
| D3 | Medium | "Last week accuracy" read 26 years of rainfall to use 7 days (8.3 s per request). | **Fixed:** filtering by date in the database cut it to 0.24 s with identical output. |
| D4 | Medium | The SAP pull wrote each dataset twice (`… .csv` and `… - all regions.csv`). | **Fixed:** single load into `sap_rainfall_raw`. |
| D5 | Low | `frontend/js/searchable_selects.js` is not referenced by any page. | **Open:** safe to delete once confirmed unused. |
| D6 | Low | `/api/cumulative-rainfall` defaulted `to=2025`, a hard-coded year. | **Fixed:** defaults to last year. |
| D7 | Low | `update_location_forecast.py` calls Open-Meteo with no retry, unlike every other pull. | **Fixed:** uses the shared `fetch_json` (4 attempts with backoff, longer wait on HTTP 429); covered by tests. |

## 3. Open points needing a decision

1. **The SAP DEV environment is used as the production historical source.** The
   code's own comment says the Syncera guides do not define DEV as historical.
   Confirm with Syncera that DEV is a supported, stable source.
2. **The PROD freshness rule.** The refresh fails until a current-month reading
   exists. Consider a grace period (for example, the latest reading within N days).
3. **Estate list mismatch.** The workbook has 62 estates, while `SAP_ESTATES`
   lists 59. AM3B, BU9A and IU3A get forecasts but no SAP readings. Confirm
   whether they should be added to `SAP_ESTATES`.
4. ~~**No refresh lock.**~~ **Fixed:** a PostgreSQL advisory lock now allows
   only one refresh at a time; a second one exits at once with a clear message.
5. **Labelling.** Open-Meteo "historical_actual" is modelled reanalysis, not
   gauge data. The dashboard copy should keep saying "Open-Meteo" next to "actual".
6. **HTTPS.** Sign-in with role-based access is now in place (Administrator, Plantation user,
   Regional viewer). Traffic is still plain HTTP; a company-wide rollout needs an HTTPS reverse
   proxy with SESSION_COOKIE_SECURE=true.