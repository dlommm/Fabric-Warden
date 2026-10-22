# Fabric Warden API. Base images are pinned by version and digest; bump both together
# (docker buildx imagetools inspect <image:tag> prints the digest).
ARG PYTHON_IMAGE=python:3.13.17-slim-bookworm@sha256:a881a81282cca8c57f97014867fa651def1b3799030cc6b1cff45ed55ba8ef36
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM ${PYTHON_IMAGE}
LABEL org.opencontainers.image.title="Fabric Warden API" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.licenses="UNLICENSED"
RUN useradd --system --uid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin warden \
 && mkdir -p /data && chown warden:warden /data && chmod 0700 /data
WORKDIR /app
# Code is owned by root and read-only for the service user; only /data is writable.
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 WARDEN_DATA_DIR=/data
USER warden
EXPOSE 8000
VOLUME ["/data"]
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health/ready', timeout=2).status == 200 else 1)"]
# Client addresses come from WARDEN_FORWARDED_HOPS (see DEPLOYMENT.md), not uvicorn's proxy headers,
# which would trust a client-supplied X-Forwarded-For.
CMD ["uvicorn", "warden.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-server-header"]
