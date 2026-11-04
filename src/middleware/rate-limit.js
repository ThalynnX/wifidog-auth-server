"use strict";

/**
 * 登录限流中间件
 * 
 * 防止暴力破解: 每个 IP 每分钟最多 N 次尝试
 */

const rateLimit = require("express-rate-limit");
const config = require("../config");

const portalLimiter = rateLimit({
  windowMs: config.rateLimit.windowMs,
  max: config.rateLimit.max,
  standardHeaders: true,
  legacyHeaders: false,
  handler: (req, res) => {
    res.status(429).render("portal", {
      ip: req.body.ip || "",
      mac: req.body.mac || "",
      token: req.body.token || "",
      ssid: req.body.ssid || "",
      gateway_id: req.body.gateway_id || "",
      gateway_address: req.body.gateway_address || "",
      error: "尝试次数过多，请稍后再试",
    });
  },
});

module.exports = { portalLimiter };
