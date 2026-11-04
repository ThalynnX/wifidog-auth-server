"use strict";

/**
 * 集中配置管理
 * 
 * 所有配置从环境变量读取，带默认值。
 * 启动时通过 validate.js 校验必填项。
 */

const path = require("path");

const toInt = (v, def) => {
  const n = parseInt(v, 10);
  return Number.isFinite(n) ? n : def;
};

const toBool = (v, def = false) => {
  if (v === undefined) return def;
  return v === "true" || v === "1";
};

const nodeEnv = process.env.NODE_ENV || "development";

const config = {
  nodeEnv,
  isProd: nodeEnv === "production",
  isDev: nodeEnv === "development",

  // 服务
  host: process.env.HOST || "0.0.0.0",
  port: toInt(process.env.PORT, 2060),
  authServerUrl: process.env.AUTH_SERVER_URL || `http://localhost:${toInt(process.env.PORT, 2060)}`,

  // RADIUS
  radius: {
    host: process.env.RADIUS_SERVER_HOST || "127.0.0.1",
    port: toInt(process.env.RADIUS_SERVER_PORT, 1812),
    secret: process.env.RADIUS_SECRET || "testing123",
    nasIp: process.env.RADIUS_NAS_IP || "127.0.0.1",
    nasPort: toInt(process.env.RADIUS_NAS_PORT, 0),
    timeoutMs: toInt(process.env.RADIUS_TIMEOUT_MS, 5000),
    retries: toInt(process.env.RADIUS_RETRIES, 2),
  },

  // 数据库
  db: {
    path: path.resolve(process.env.DB_PATH || "./data/wifidog.db"),
    flushIntervalMs: toInt(process.env.DB_FLUSH_INTERVAL_MS, 500),
  },

  // Token / 会话
  tokenExpirySec: toInt(process.env.TOKEN_EXPIRY, 3600),
  gwCheckIntervalSec: toInt(process.env.GW_CHECK_INTERVAL, 600),

  // 限流
  rateLimit: {
    windowMs: toInt(process.env.RATE_LIMIT_WINDOW_MS, 60000),
    max: toInt(process.env.RATE_LIMIT_MAX, 10),
  },

  // 网关默认值 (路由器端)
  gateway: {
    defaultAddress: process.env.DEFAULT_GATEWAY_ADDRESS || "192.168.1.1",
    defaultId: process.env.DEFAULT_GATEWAY_ID || "default_gw",
  },

  // 日志
  logLevel: process.env.LOG_LEVEL || "info",
};

module.exports = config;
