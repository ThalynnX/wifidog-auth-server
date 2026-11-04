"use strict";

/**
 * 认证门户路由
 * 
 *   GET  /portal → 显示登录页面
 *   POST /portal → 提交认证 (限流)
 * 
 * 调用链:
 *   routes/portal.js
 *     → services/radius-service.js (authenticateUser)
 *     → services/token-service.js (createToken)
 *     → services/user-service.js (recordUser)
 *     → utils/validation.js (safeGatewayAddress)
 */

const express = require("express");
const { authenticateUser } = require("../services/radius-service");
const { createToken } = require("../services/token-service");
const { recordUser } = require("../services/user-service");
const { safeGatewayAddress, str } = require("../utils/validation");
const logger = require("../utils/logger");

function portalRoutes({ config, portalLimiter }) {
  const router = express.Router();

  // ── GET /portal: 登录页面 ─────────────────────────────────
  router.get("/", (req, res) => {
    const { ip, mac, token, ssid, gateway_id, gateway_address } = req.query;
    res.render("portal", {
      ip: str(ip, 64),
      mac: str(mac, 64),
      token: str(token, 64),
      ssid: str(ssid, 64),
      gateway_id: str(gateway_id, 64),
      gateway_address: str(gateway_address, 128),
      error: null,
    });
  });

  // ── POST /portal: 提交认证 ───────────────────────────────
  router.post("/", portalLimiter, async (req, res) => {
    const {
      username,
      password,
      ip,
      mac,
      token,
      ssid,
      gateway_id,
      gateway_address,
    } = req.body;

    const view = {
      ip: str(ip, 64),
      mac: str(mac, 64),
      token: str(token, 64),
      ssid: str(ssid, 64),
      gateway_id: str(gateway_id, 64),
      gateway_address: str(gateway_address, 128),
    };

    // 参数校验
    if (!str(username) || !password) {
      return res.status(400).render("portal", {
        ...view,
        error: "请输入用户名和密码",
      });
    }

    try {
      // RADIUS 认证
      const result = await authenticateUser(str(username), password, {
        gatewayId: gateway_id,
      });

      if (result.success) {
        // 创建 token
        createToken(
          str(ip, 64),
          str(mac, 64),
          str(gateway_id, 64),
          str(username, 128)
        );

        // 记录用户
        recordUser(str(username, 128));

        logger.info("login success", {
          username: str(username, 128),
          gatewayId: str(gateway_id, 64),
        });

        // 重定向回网关
        const gwAddr = safeGatewayAddress(gateway_address) || config.gateway.defaultAddress;
        return res.redirect(`http://${gwAddr}`);
      }

      // 认证失败
      logger.warn("login failed", {
        username: str(username, 128),
      });

      return res.status(401).render("portal", {
        ...view,
        error: "认证失败，请检查用户名或密码",
      });
    } catch (err) {
      logger.error("radius auth error", { error: err.message });
      return res.status(503).render("portal", {
        ...view,
        error: "认证服务暂时不可用，请稍后重试",
      });
    }
  });

  return router;
}

module.exports = portalRoutes;
