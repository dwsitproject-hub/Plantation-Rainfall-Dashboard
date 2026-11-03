# Plantation Rainfall Dashboard - backend image (API, sign-in, data refresh).
# Build context: the repository root (needs requirements.txt).
# Settings and secrets come from .env at runtime; none are baked in.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DASHBOARD_HOST=0.0.0.0 \
    DASHBOARD_PORT=8000 \
    TZ=Asia/Jakarta

# Forecast run dates and "today" are local dates, so the container keeps WIB time.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/

# Git commit of this build, reported to the DWS Hub SDK so Sentinel reads exactly this code:
#   APP_RELEASE=$(git rev-parse HEAD) docker compose build
ARG APP_RELEASE=
ENV APP_RELEASE=${APP_RELEASE}

RUN useradd --create-home --uid 10001 dashboard && mkdir -p data/work && chown -R dashboard /app/data
USER dashboard

EXPOSE 8000
# Checks /api/health; one-off containers (refresh, init_db, ...) report healthy (backend/healthcheck.py).
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD ["python", "-m", "backend.healthcheck"]

CMD ["python", "-m", "backend.dashboard_server"]
