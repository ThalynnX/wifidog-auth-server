"use strict";

const path = require("path");

const toInt = (v, def) => {
  const n = parseInt(v, 10);
  return Number.isFinite(n) ? n : def;
};

const nodeEnv = process.env.NODE_ENV || "development";

const config = {
  nodeEnv,
  isProd: nodeEnv === "production",

  host: process.env.HOST || "0.0.0.0",
  port: toInt(process.env.PORT, 2060),
  authServerUrl: process.env.AUTH_SERVER_URL || `http://localhost:${toInt(process.env.PORT, 2060)}`,

  radius: {
    host: process.env.RADIUS_SERVER_HOST || "127.0.0.1",
    port: toInt(process.env.RADIUS_SERVER_PORT, 1812),
    secret: process.env.RADIUS_SECRET || "testing123",
    nasIp: process.env.RADIUS_NAS_IP || "127.0.0.1",
    nasPort: toInt(process.env.RADIUS_NAS_PORT, 0),
    timeoutMs: toInt(process.env.RADIUS_TIMEOUT_MS, 5000),
    retries: toInt(process.env.RADIUS_RETRIES, 2),
  },

  db: {
    path: path.resolve(process.env.DB_PATH || "./data/wifidog.db"),
    flushIntervalMs: toInt(process.env.DB_FLUSH_INTERVAL_MS, 500),
  },

  tokenExpirySec: toInt(process.env.TOKEN_EXPIRY, 3600),
  gwCheckIntervalSec: toInt(process.env.GW_CHECK_INTERVAL, 600),

  rateLimit: {
    windowMs: toInt(process.env.RATE_LIMIT_WINDOW_MS, 60000),
    max: toInt(process.env.RATE_LIMIT_MAX, 10),
  },

  logLevel: process.env.LOG_LEVEL || "info",
};

module.exports = config;
