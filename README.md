# Plantation Rainfall Dashboard

Rainfall, temperature and soil-moisture monitoring for plantation operations.
It combines Open-Meteo regional weather for Indonesia, Malaysia and Thailand
with SAP/Syncera rain-gauge readings from the in-house estates.

- `backend/`: Python data pipelines, web server and PostgreSQL 18 database code
- `frontend/`: dashboard pages
- `docs/`: [Operations guide](docs/OPERATIONS_GUIDE.md), [Staging deployment](docs/DEPLOYMENT_STAGING.md), [Production deployment](docs/DEPLOYMENT_PRODUCTION.md), [README review](docs/README_REVIEW.md), PRD and Technical Design
- `deploy/staging/`: compose files and settings template for the staging frontend and backend servers

Quick start (full steps are in the operations guide):

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env              # then fill in the REPLACE_ME values
python -m backend.database.bootstrap     # once: creates the database
python -m backend.auth.create_admin      # once: first administrator account
python -m backend.run_dashboard          # refresh data and open the dashboard
```

Or run it as two containers, nginx frontend and Python backend (see the operations guide):

```powershell
docker compose up -d --build             # http://localhost:8080/
```
