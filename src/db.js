"use strict";

const initSqlJs = require("sql.js");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const config = require("./config");
const logger = require("./logger");

let db = null;
let dirty = false;
let flushTimer = null;

const DB_PATH = config.db.path;

function ensureDir() {
  const dir = path.dirname(DB_PATH);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
}

async function initDatabase() {
  ensureDir();
  const SQL = await initSqlJs();
  db = fs.existsSync(DB_PATH)
    ? new SQL.Database(fs.readFileSync(DB_PATH))
    : new SQL.Database();

  db.run(`
    CREATE TABLE IF NOT EXISTS tokens (
      token TEXT PRIMARY KEY,
      ip TEXT, mac TEXT, gateway_id TEXT, username TEXT,
      created_at INTEGER, expires_at INTEGER, active INTEGER DEFAULT 1
    );
    CREATE INDEX IF NOT EXISTS idx_tokens_active ON tokens(active, expires_at);
    CREATE TABLE IF NOT EXISTS gateways (
      gateway_id TEXT PRIMARY KEY, ip TEXT, last_ping INTEGER,
      sys_uptime INTEGER, sys_memfree INTEGER, sys_load REAL, wl_count INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_gateways_ping ON gateways(last_ping);
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE, email TEXT, created_at INTEGER
    );
  `);
  scheduleFlush();
  logger.info("database initialized", { path: DB_PATH });
  return db;
}

/**
 * Mark the DB as dirty and schedule a debounced flush.
 * Under captive-portal load this avoids a synchronous full-database
 * write on every single request (the main hot-path bottleneck of v1).
 */
function scheduleFlush() {
  dirty = true;
  if (flushTimer) return;
  flushTimer = setTimeout(() => {
    flushTimer = null;
    flushNow();
  }, config.db.flushIntervalMs);
  if (typeof flushTimer.unref === "function") flushTimer.unref();
}

function flushNow() {
  if (!db || !dirty) return;
  try {
    fs.writeFileSync(DB_PATH, Buffer.from(db.export()));
    dirty = false;
  } catch (err) {
    logger.error("database flush failed", { error: err.message });
  }
}

function close() {
  if (flushTimer) clearTimeout(flushTimer);
  flushNow();
}

function createToken(ip, mac, gatewayId, username) {
  const token = crypto.randomBytes(16).toString("hex");
  const now = Math.floor(Date.now() / 1000);
  const exp = now + config.tokenExpirySec;
  db.run(
    "INSERT INTO tokens(token, ip, mac, gateway_id, username, created_at, expires_at, active) VALUES(?,?,?,?,?,?,?,1)",
    [token, ip, mac, gatewayId, username, now, exp]
  );
  scheduleFlush();
  return { token, expiresAt: exp };
}

function validateToken(token) {
  if (!token || typeof token !== "string") return false;
  const r = db.exec(
    "SELECT 1 FROM tokens WHERE token=? AND active=1 AND expires_at>?",
    [token, Math.floor(Date.now() / 1000)]
  );
  return r.length > 0 && r[0].values.length > 0;
}

function getToken(token) {
  if (!token || typeof token !== "string") return null;
  const r = db.exec(
    "SELECT * FROM tokens WHERE token=? AND active=1 AND expires_at>?",
    [token, Math.floor(Date.now() / 1000)]
  );
  if (!r.length || !r[0].values.length) return null;
  const out = {};
  r[0].columns.forEach((c, i) => {
    out[c] = r[0].values[0][i];
  });
  return out;
}

function deactivateToken(token) {
  db.run("UPDATE tokens SET active=0 WHERE token=?", [token]);
  scheduleFlush();
}

function pruneExpiredTokens() {
  db.run("DELETE FROM tokens WHERE active=0 OR expires_at<=?", [Math.floor(Date.now() / 1000)]);
  scheduleFlush();
}

function upsertGateway(gatewayId, ip, uptime, memfree, load, wlCount) {
  const now = Math.floor(Date.now() / 1000);
  db.run(
    "INSERT INTO gateways(gateway_id, ip, last_ping, sys_uptime, sys_memfree, sys_load, wl_count) VALUES(?,?,?,?,?,?,?) " +
      "ON CONFLICT(gateway_id) DO UPDATE SET ip=excluded.ip, last_ping=excluded.last_ping, " +
      "sys_uptime=excluded.sys_uptime, sys_memfree=excluded.sys_memfree, sys_load=excluded.sys_load, wl_count=excluded.wl_count",
    [gatewayId, ip, now, uptime, memfree, load, wlCount]
  );
  scheduleFlush();
}

function recordUser(username) {
  db.run("INSERT OR IGNORE INTO users(username, created_at) VALUES(?,?)", [
    username,
    Math.floor(Date.now() / 1000),
  ]);
  scheduleFlush();
}

function countActiveTokens() {
  const r = db.exec("SELECT COUNT(*) FROM tokens WHERE active=1");
  return r[0]?.values?.[0]?.[0] || 0;
}

function countGateways() {
  const r = db.exec("SELECT COUNT(*) FROM gateways");
  return r[0]?.values?.[0]?.[0] || 0;
}

function countUsers() {
  const r = db.exec("SELECT COUNT(*) FROM users");
  return r[0]?.values?.[0]?.[0] || 0;
}

function isReady() {
  return db !== null;
}

function getDb() {
  return db;
}

module.exports = {
  initDatabase,
  close,
  createToken,
  validateToken,
  getToken,
  deactivateToken,
  pruneExpiredTokens,
  upsertGateway,
  recordUser,
  countActiveTokens,
  countGateways,
  countUsers,
  isReady,
  getDb,
};
