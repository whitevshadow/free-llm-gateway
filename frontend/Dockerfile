# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────────────
#  OmniRoute dashboard — the gateway's UI.
#
#  A Next.js server, not a static bundle: pages are server-rendered on demand and
#  the `/api/*` bridge (src/app/api/[...path]/route.ts) runs server-side so it can
#  reach the gateway on a container-internal address the browser never sees.
#
#  Build context is THIS directory (frontend/).
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: dependencies ────────────────────────────────────────────────────
FROM node:22-alpine AS deps
WORKDIR /build

# Native toolchain: a handful of transitive packages ship node-gyp builds. The
# modules this project actually aliases away (better-sqlite3, keytar, …) never
# compile, but their siblings still need a compiler present to install cleanly.
RUN apk add --no-cache libc6-compat python3 make g++

COPY package.json package-lock.json* ./
# --legacy-peer-deps: the upstream tree is resolved by pnpm, and several React 19
# peer ranges have not caught up. npm's strict resolver rejects the tree outright.
RUN npm install --no-audit --no-fund --legacy-peer-deps


# ── Stage 2: build ───────────────────────────────────────────────────────────
FROM node:22-alpine AS builder
WORKDIR /build

COPY --from=deps /build/node_modules ./node_modules
COPY . .

# The address OpenAI/Anthropic clients point at — the gateway, not this
# dashboard. Shown on the Endpoints screen and in every copy-paste snippet.
#
# NEXT_PUBLIC_* is INLINED AT BUILD TIME by Next, so this has to be a build arg:
# setting it only at runtime would leave the compiled bundle advertising the
# wrong port. Changing the gateway's port therefore needs a dashboard rebuild.
ARG NEXT_PUBLIC_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_BASE_URL=${NEXT_PUBLIC_BASE_URL}

# The dashboard imports a large share of OmniRoute's original module graph, and
# the default heap is not enough to finish the production pass.
ENV NODE_OPTIONS=--max-old-space-size=8192
ENV NEXT_TELEMETRY_DISABLED=1

RUN npm run build


# ── Stage 3: runtime ─────────────────────────────────────────────────────────
FROM node:22-alpine AS runtime
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# `output: "standalone"` emits a self-contained server plus only the node_modules
# it actually traced — the difference between a ~200 MB image and a ~2 GB one.
# static/ and public/ are not included in that trace and must be copied beside it.
COPY --from=builder /build/.next/standalone ./
COPY --from=builder /build/.next/static ./.next/static
COPY --from=builder /build/public ./public

# Non-root. The dashboard holds session cookies in flight and has no reason to
# own its own filesystem.
RUN addgroup --system --gid 10002 nodejs \
    && adduser --system --uid 10002 --ingroup nodejs nextjs \
    && chown -R nextjs:nodejs /app
USER nextjs

EXPOSE 3000

# Hits the bridge's own liveness path, which in turn pings the gateway — so an
# unhealthy backend surfaces here instead of a dashboard that loads and then
# fails on every request.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD wget -qO- http://127.0.0.1:3000/api/health/ping >/dev/null || exit 1

CMD ["node", "server.js"]
