# syntax=docker/dockerfile:1.7
# Fabric Warden: the API and the web app in one image. Build from the repository root:
#   docker build -t fabric-warden .
# Base images are pinned by version and digest; bump both together
# (docker buildx imagetools inspect <image:tag> prints the digest).
ARG PYTHON_IMAGE=python:3.13.17-slim-bookworm@sha256:a881a81282cca8c57f97014867fa651def1b3799030cc6b1cff45ed55ba8ef36
ARG NODE_IMAGE=node:24.22.1-bookworm-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424

FROM ${UV_IMAGE} AS uv

FROM ${NODE_IMAGE} AS node

FROM ${PYTHON_IMAGE} AS api
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app/api
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY apps/api/src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM ${NODE_IMAGE} AS web-deps
WORKDIR /app
RUN corepack enable
COPY apps/web/package.json apps/web/pnpm-lock.yaml apps/web/pnpm-workspace.yaml* ./
RUN pnpm install --frozen-lockfile

FROM ${NODE_IMAGE} AS web
WORKDIR /app
RUN corepack enable
COPY --from=web-deps /app/node_modules ./node_modules
COPY apps/web ./
# Rewrites are resolved at build time: the API listens on loopback in the same container.
# WARDEN_HSTS=true adds Strict-Transport-Security to every response (HTTPS-only sites).
ARG WARDEN_HSTS=false
ENV WARDEN_API_URL=http://127.0.0.1:8000 WARDEN_HSTS=$WARDEN_HSTS NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

FROM ${PYTHON_IMAGE}
ARG VERSION=dev
LABEL org.opencontainers.image.title="Fabric Warden" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/dlommm/Fabric-Warden" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
COPY --from=node /usr/local/bin/node /usr/local/bin/node
RUN useradd --system --uid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin warden \
 && mkdir -p /data && chown warden:warden /data && chmod 0700 /data
# Code is owned by root and read-only for the service user; only /data and Next's cache are writable.
COPY --from=api /app/api/.venv /app/api/.venv
COPY --from=web /app/.next/standalone /app/web/
COPY --from=web /app/.next/static /app/web/.next/static
COPY --from=web /app/public /app/web/public
RUN mkdir -p /app/web/.next/cache && chown warden /app/web/.next/cache
COPY --chmod=0755 deploy/entrypoint.sh /usr/local/bin/fabric-warden
ENV PATH="/app/api/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    WARDEN_DATA_DIR=/data WARDEN_DATABASE_URL=sqlite+aiosqlite:////data/warden.db \
    NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
USER warden
WORKDIR /app
EXPOSE 3000
VOLUME ["/data"]
STOPSIGNAL SIGTERM
# Fetches the API through the web app's /api proxy, so healthy means both processes answer
# and migrations have run.
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/api/health/ready').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
CMD ["fabric-warden"]
