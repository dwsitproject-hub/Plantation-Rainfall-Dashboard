# Plantation Rainfall Dashboard - frontend image (static pages served by nginx).
# Build context: the frontend/ folder.  Set BACKEND_URL to the backend's address.
FROM nginx:1.27-alpine

ENV BACKEND_URL=http://backend:8000

RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template

# Copy only the site itself (never this Dockerfile or the nginx folder).
COPY *.html /usr/share/nginx/html/
COPY css/ /usr/share/nginx/html/css/
COPY js/ /usr/share/nginx/html/js/
COPY inhouse/ /usr/share/nginx/html/inhouse/
COPY admin/ /usr/share/nginx/html/admin/
COPY account/ /usr/share/nginx/html/account/

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
