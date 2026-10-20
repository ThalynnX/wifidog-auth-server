# ---- Base ----
FROM node:22-alpine AS base
ENV NODE_ENV=production
WORKDIR /app

# ---- Dependencies (cached build layer) ----
FROM base AS deps
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

# ---- Runtime ----
FROM base
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN mkdir -p /app/data && chown -R node:node /app
USER node
EXPOSE 2060

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1:2060/health >/dev/null 2>&1 || exit 1

CMD ["node", "src/server.js"]
