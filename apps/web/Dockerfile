# Fabric Warden web app. Base image pinned by version and digest; bump both together
# (docker buildx imagetools inspect <image:tag> prints the digest).
ARG NODE_IMAGE=node:24.22.1-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53

FROM ${NODE_IMAGE} AS deps
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml* ./
RUN pnpm install --frozen-lockfile

FROM ${NODE_IMAGE} AS build
WORKDIR /app
RUN corepack enable
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Rewrites are resolved at build time; the API hostname inside compose.
ARG WARDEN_API_URL=http://api:8000
ENV WARDEN_API_URL=$WARDEN_API_URL NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

FROM ${NODE_IMAGE}
LABEL org.opencontainers.image.title="Fabric Warden web" \
      org.opencontainers.image.version="1.0.0-rc.1" \
      org.opencontainers.image.licenses="UNLICENSED"
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
# Corepack/npm are not needed at runtime.
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
 && adduser -S -D -H -u 10001 web
# Files are owned by root and read-only for the service user.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
# The only writable path: Next's runtime cache.
RUN mkdir -p .next/cache && chown web .next/cache
USER web
EXPOSE 3000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/login').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
CMD ["node", "server.js"]
