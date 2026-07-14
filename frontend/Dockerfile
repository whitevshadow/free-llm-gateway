# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────────────
#  Frontend — the React SPA, served by nginx, which also proxies the API.
#
#  Build context is frontend/.   docker compose build web
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: compile the SPA ────────────────────────────────────────────────
FROM node:20-alpine AS build

WORKDIR /build

# Manifests first, so `npm install` stays cached until deps actually change.
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

COPY . .

# vite.config.ts writes to ../backend/static (the single-image layout). In this
# container that resolves to /backend/static — outside WORKDIR, which is fine;
# we just copy from there in the next stage.
RUN npm run build


# ── Stage 2: nginx ──────────────────────────────────────────────────────────
FROM nginx:1.27-alpine AS runtime

# Replace the default site with ours (SPA fallback + API proxy).
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/gateway.conf

COPY --from=build /backend/static /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q --spider http://localhost/ || exit 1
