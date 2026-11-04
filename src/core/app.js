"use strict";

/**
 * Express 应用工厂
 * 
 * 模块组装:
 *   middleware/security.js  → 安全头
 *   middleware/rate-limit.js → 登录限流
 *   middleware/error-handler.js → 统一错误处理
 *   routes/wifidog.js      → WiFiDog 协议端点
 *   routes/portal.js       → 认证门户页面
 *   routes/health.js       → 健康检查
 */

const express = require("express");
const path = require("path");

const { securityMiddleware } = require("../middleware/security");
const { portalLimiter } = require("../middleware/rate-limit");
const { notFoundHandler, errorHandler } = require("../middleware/error-handler");
const { requestLogger } = require("../middleware/request-logger");

const wifidogRoutes = require("../routes/wifidog");
const portalRoutes = require("../routes/portal");
const healthRoutes = require("../routes/health");

function createApp(config) {
  const app = express();

  // 视图引擎
  app.set("view engine", "ejs");
  app.set("views", path.join(__dirname, "..", "views"));

  // 安全中间件
  app.use(securityMiddleware());

  // 解析
  app.use(express.urlencoded({ extended: true, limit: "16kb" }));
  app.use(express.json({ limit: "16kb" }));

  // 静态资源
  app.use("/static", express.static(path.join(__dirname, "..", "public"), {
    maxAge: "7d",
    immutable: true,
  }));

  // 请求日志
  app.use(requestLogger);

  // 路由 (挂载顺序 = 匹配优先级)
  app.use("/health", healthRoutes);
  app.use("/status", healthRoutes);
  app.use("/", wifidogRoutes({ config, portalLimiter }));
  app.use("/portal", portalRoutes({ config, portalLimiter }));

  // 404 + 错误处理
  app.use(notFoundHandler);
  app.use(errorHandler);

  return app;
}

module.exports = { createApp };
