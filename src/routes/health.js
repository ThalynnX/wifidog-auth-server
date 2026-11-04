"use strict";

/**
 * 健康检查与状态路由
 * 
 *   GET /health → 容器健康检查
 *   GET /status → 运行状态统计
 */

const express = require("express");
const { isReady } = require("../db");
const { countActiveTokens } = require("../services/token-service");
const { countGateways } = require("../services/gateway-service");
const { countUsers } = require("../services/user-service");

const router = express.Router();

// 容器健康检查
router.get("/health", (req, res) => {
  res.json({ status: isReady() ? "ok" : "starting" });
});

// 运行状态
router.get("/status", (req, res) => {
  res.json({
    status: "ok",
    version: require("../../package.json").version,
    uptimeSec: Math.floor(process.uptime()),
    activeTokens: countActiveTokens(),
    registeredGateways: countGateways(),
    registeredUsers: countUsers(),
  });
});

module.exports = router;
