# syntax=docker/dockerfile:1.7
#
# The YardLens dashboard as a standalone Next.js server. Build from the repository root:
#
#   docker build -f apps/web/Dockerfile -t yardlens-web .
#
# Behind Caddy the browser reaches the API on the dashboard's own origin, so the API address is
# left empty at build time.

FROM node:24-slim AS build
COPY --from=oven/bun:1.4.3 /usr/local/bin/bun /usr/local/bin/bun
WORKDIR /repo
COPY package.json bun.lock ./
COPY apps/web/package.json apps/web/
RUN --mount=type=cache,target=/root/.bun/install/cache \
    bun install --frozen-lockfile --filter web
COPY apps/web apps/web
ENV NEXT_TELEMETRY_DISABLED=1 \
    NEXT_STANDALONE=1 \
    NEXT_PUBLIC_API_URL= \
    API_INTERNAL_URL=http://api:8100
RUN cd apps/web && bun run build


FROM node:24-slim
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3100
WORKDIR /app
# The standalone output keeps the monorepo layout: the server lives under apps/web.
COPY --from=build --chown=node:node /repo/apps/web/.next/standalone ./
COPY --from=build --chown=node:node /repo/apps/web/.next/static ./apps/web/.next/static
USER node
EXPOSE 3100
CMD ["node", "apps/web/server.js"]
