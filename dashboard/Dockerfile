FROM oven/bun:1.3.14-alpine AS build

WORKDIR /app
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile
COPY src ./src
COPY public ./public
COPY tsconfig.json biome.json ./
RUN bun run build && bun run typecheck

FROM oven/bun:1.3.14-alpine AS runtime

ENV NODE_ENV=production
WORKDIR /app
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile --production
COPY --from=build /app/src ./src
COPY --from=build /app/public ./public
USER bun
CMD ["bun", "src/server.ts"]
