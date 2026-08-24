# Frontend image: build the SPA, then serve it from nginx.
#
# nginx also reverse-proxies /api and /health to the backend, so the browser
# talks to a single origin exactly as it does behind the Vite dev proxy. That
# keeps CORS out of the picture entirely and means no API base URL has to be
# configured into the client at build time.

# Node 22: 20 reached end of life in April 2026, and the backend image needs
# 22 for the Salesforce CLI. Keeping both on the same major means one
# toolchain to reason about rather than two.
FROM node:22-alpine AS build
WORKDIR /src

# Lockfile first so dependency installs cache independently of source edits.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine AS runtime

COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist /usr/share/nginx/html

# nginx:alpine already drops to an unprivileged worker; the master needs root
# to bind :80 inside the container, which is fine because nothing else runs here.
EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget --quiet --tries=1 --spider http://127.0.0.1/ || exit 1
