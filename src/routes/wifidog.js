"use strict";

/**
 * WiFiDog 协议路由
 * 
 * 标准端点:
 *   GET /login?ip=&mac=&token=&gateway_id=&gateway_address=
 *     → 已认证则重定向回网关，否则跳转 /portal
 *   GET /auth?token=...
 *     → 返回 "Auth: 1" 或 "Auth: 0" (网关轮询)
 *   GET /ping?gw_id=&sys_uptime=&sys_memfree=&sys_load=&num_users=
 *     → 网关心跳，返回 "Pong"
 *   GET /msg?msg=...
 *     → 显示消息页面
 * 
 * 调用链:
 *   routes/wifidog.js
 *     → services/token-service.js (validateToken)
 *     → services/gateway-service.js (upsertGateway)
 *     → utils/validation.js (safeGatewayAddress)
 */

const express = require("express");
const { validateToken } = require("../services/token-service");
const { upsertGateway } = require("../services/gateway-service");
const { safeGatewayAddress, str } = require("../utils/validation");

function wifidogRoutes({ config }) {
  const router = express.Router();

  // ── /login: 网关重定向入口 ──────────────────────────────
  router.get("/login", (req, res) => {
    const { ip, mac, token, gateway_id, gateway_address } = req.query;

    // 必需参数校验
    if (!ip || !mac || !token || !gateway_id) {
      return res.status(400).send("Missing required parameters: ip, mac, token, gateway_id");
    }

    // 已认证 → 直接放行回网关
    if (validateToken(token)) {
      const gwAddr = safeGatewayAddress(gateway_address) || config.gateway.defaultAddress;
      return res.redirect(`http://${gwAddr}`);
    }

    // 未认证 → 跳转门户页面
    const qs = new URLSearchParams({
      ip: str(ip, 64),
      mac: str(mac, 64),
      token: str(token, 64),
      ssid: str(req.query.ssid, 64),
      gateway_id: str(gateway_id, 64),
      gateway_address: str(gateway_address, 128),
    });

    res.redirect(`/portal?${qs.toString()}`);
  });

  // ── /auth: Token 验证 (网关轮询) ────────────────────────
  router.get("/auth", (req, res) => {
    const { token } = req.query;
    res.send(validateToken(token) ? "Auth: 1" : "Auth: 0");
  });

  // ── /ping: 网关心跳 ─────────────────────────────────────
  router.get("/ping", (req, res) => {
    const {
      gw_id,
      sys_uptime,
      sys_memfree,
      sys_load,
      num_users,
    } = req.query;

    if (gw_id) {
      upsertGateway(
        str(gw_id, 64),
        req.ip || "unknown",
        parseInt(sys_uptime || "0", 10) || 0,
        parseInt(sys_memfree || "0", 10) || 0,
        parseFloat(sys_load || "0") || 0,
        parseInt(num_users || "0", 10) || 0
      );
    }

    res.send("Pong");
  });

  // ── /msg: 消息页面 ──────────────────────────────────────
  router.get("/msg", (req, res) => {
    res.render("message", {
      message: str(req.query.msg, 512) || "操作成功",
    });
  });

  return router;
}

module.exports = wifidogRoutes;
