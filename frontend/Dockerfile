# Plantation Rainfall Dashboard - frontend image (static pages served by nginx).
# Build context: the frontend/ folder.  Set BACKEND_URL to the backend's address.
# Stage 1: stamp CSS/JS links with content hashes (browser caching, see nginx config).
FROM python:3.12-alpine AS site
WORKDIR /site
# Copy only the site itself (never this Dockerfile or the nginx folder).
COPY *.html ./
COPY css/ css/
COPY js/ js/
COPY inhouse/ inhouse/
COPY admin/ admin/
COPY account/ account/
COPY nginx/stamp_assets.py /tmp/stamp_assets.py
RUN python /tmp/stamp_assets.py /site

FROM nginx:1.27-alpine

ENV BACKEND_URL=http://backend:8000

RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=site /site/ /usr/share/nginx/html/

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
