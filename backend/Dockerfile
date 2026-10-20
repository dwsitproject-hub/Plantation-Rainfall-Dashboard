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

RUN useradd --create-home --uid 10001 dashboard && mkdir -p data/work && chown -R dashboard /app/data
USER dashboard

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('DASHBOARD_PORT', '8000'), timeout=5)"

CMD ["python", "-m", "backend.dashboard_server"]
