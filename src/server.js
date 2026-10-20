"use strict";

require("dotenv").config();

const express = require("express");
const path = require("path");
const helmet = require("helmet");
const rateLimit = require("express-rate-limit");

const config = require("./config");
const logger = require("./logger");
const db = require("./db");
const { authenticateUser } = require("./radius-client");
const { safeGatewayAddress, str } = require("./validation");

const app = express();

app.set("view engine", "ejs");
app.set("views", path.join(__dirname, "views"));

// ── Security headers ──────────────────────────────────────────────
app.use(
  helmet({
    contentSecurityPolicy: {
      directives: {
        defaultSrc: ["'self'"],
        scriptSrc: ["'self'"],
        styleSrc: ["'self'"],
        imgSrc: ["'self'", "data:"],
        objectSrc: ["'none'"],
        baseUri: ["'self'"],
        formAction: ["'self'"],
        frameAncestors: ["'none'"],
      },
    },
    crossOriginEmbedderPolicy: false,
  })
);

app.use(express.urlencoded({ extended: true, limit: "16kb" }));
app.use(express.json({ limit: "16kb" }));
app.use("/static", express.static(path.join(__dirname, "public"), { maxAge: "7d", immutable: true }));

// ── Request logging ───────────────────────────────────────────────
app.use((req, res, next) => {
  const start = Date.now();
  res.on("finish", () => {
    logger.debug("http request", {
      method: req.method,
      url: req.originalUrl,
      status: res.statusCode,
      ms: Date.now() - start,
    });
  });
  next();
});

// ── Brute-force protection for the login endpoint ─────────────────
const portalLimiter = rateLimit({
  windowMs: config.rateLimit.windowMs,
  max: config.rateLimit.max,
  standardHeaders: true,
  legacyHeaders: false,
  handler: (req, res) =>
    res.status(429).render("portal", { ...req.body, error: "尝试次数过多，请稍后再试" }),
});

const DEFAULT_GATEWAY = "192.168.1.1";

function pickGateway(address) {
  return safeGatewayAddress(address) || DEFAULT_GATEWAY;
}

// ── WiFiDog gateway redirect entry: /login?... ────────────────────
app.get("/login", (req, res) => {
  const { ip, mac, token, gateway_id, gateway_address } = req.query;
  if (!ip || !mac || !token || !gateway_id) {
    return res.status(400).send("Missing required parameters");
  }
  if (db.validateToken(token)) {
    return res.redirect(`http://${pickGateway(gateway_address)}`);
  }
  const qs = new URLSearchParams({
    ip,
    mac,
    token,
    ssid: str(req.query.ssid, 64),
    gateway_id,
    gateway_address: str(gateway_address, 128),
  });
  res.redirect(`/portal?${qs.toString()}`);
});

// ── Login page ─────────────────────────────────────────────────────
app.get("/portal", (req, res) => {
  const { ip, mac, token, ssid, gateway_id, gateway_address } = req.query;
  res.render("portal", { ip, mac, token, ssid, gateway_id, gateway_address, error: null });
});

// ── Login submit (rate-limited) ────────────────────────────────────
app.post("/portal", portalLimiter, async (req, res) => {
  const { username, password, ip, mac, token, ssid, gateway_id, gateway_address } = req.body;
  const view = { ip, mac, token, ssid, gateway_id, gateway_address };

  if (!str(username) || !password) {
    return res.status(400).render("portal", { ...view, error: "请输入用户名和密码" });
  }
  try {
    const result = await authenticateUser(str(username), password, { gatewayId: gateway_id });
    if (result.success) {
      db.createToken(str(ip, 64), str(mac, 64), str(gateway_id, 64), str(username, 128));
      db.recordUser(str(username, 128));
      logger.info("login success", { username: str(username, 128), gatewayId: str(gateway_id, 64) });
      return res.redirect(`http://${pickGateway(gateway_address)}`);
    }
    logger.warn("login failed", { username: str(username, 128) });
    return res.status(401).render("portal", { ...view, error: "认证失败，请检查用户名或密码" });
  } catch (err) {
    logger.error("radius auth error", { error: err.message });
    return res.status(503).render("portal", { ...view, error: "认证服务暂时不可用，请稍后重试" });
  }
});

// ── WiFiDog token validation: /auth?token=... → "Auth: 1" | "Auth: 0" ──
app.get("/auth", (req, res) => {
  const { token } = req.query;
  res.send(db.validateToken(token) ? "Auth: 1" : "Auth: 0");
});

// ── WiFiDog gateway heartbeat ──────────────────────────────────────
app.get("/ping", (req, res) => {
  const { gw_id, sys_uptime, sys_memfree, sys_load, num_users } = req.query;
  if (gw_id) {
    db.upsertGateway(
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

// ── Display message ────────────────────────────────────────────────
app.get("/msg", (req, res) => {
  res.render("message", { message: str(req.query.msg, 512) || "操作成功" });
});

// ── Operational status ─────────────────────────────────────────────
app.get("/status", (req, res) => {
  res.json({
    status: "ok",
    version: require("../package.json").version,
    uptimeSec: Math.floor(process.uptime()),
    activeTokens: db.countActiveTokens(),
    registeredGateways: db.countGateways(),
    registeredUsers: db.countUsers(),
  });
});

// ── Container health check ─────────────────────────────────────────
app.get("/health", (req, res) => {
  res.json({ status: db.isReady() ? "ok" : "starting" });
});

// ── 404 ────────────────────────────────────────────────────────────
app.use((req, res) => res.status(404).send("Not Found"));

// ── Central error handler ──────────────────────────────────────────
// eslint-disable-next-line no-unused-vars
app.use((err, req, res, next) => {
  logger.error("unhandled error", { error: err.message, url: req.originalUrl });
  res.status(500).send("Internal Server Error");
});

let server = null;

async function startServer() {
  await db.initDatabase();

  server = app.listen(config.port, config.host, () => {
    logger.info("wifidog auth server started", {
      host: config.host,
      port: config.port,
      nodeEnv: config.nodeEnv,
    });
  });

  // Periodic cleanup of expired / deactivated tokens
  const interval = setInterval(() => db.pruneExpiredTokens(), config.gwCheckIntervalSec * 1000);
  if (typeof interval.unref === "function") interval.unref();

  const shutdown = (signal) => {
    logger.info("shutting down", { signal });
    clearInterval(interval);
    if (server) {
      server.close(() => {
        db.close();
        process.exit(0);
      });
    } else {
      db.close();
      process.exit(0);
    }
    setTimeout(() => process.exit(0), 5000).unref();
  };
  process.on("SIGTERM", () => shutdown("SIGTERM"));
  process.on("SIGINT", () => shutdown("SIGINT"));
  process.on("unhandledRejection", (reason) =>
    logger.error("unhandledRejection", { error: String(reason) })
  );
}

module.exports = { app, startServer };

if (require.main === module) {
  startServer().catch((err) => {
    logger.error("startup failed", { error: err.message });
    process.exit(1);
  });
}
