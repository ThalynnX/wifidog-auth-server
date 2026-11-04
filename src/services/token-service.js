"use strict";

/**
 * Token 生命周期管理
 * 
 * 被 routes/wifidog.js 调用:
 *   - POST /portal 成功 → createToken()
 *   - GET /auth?token= → validateToken()
 *   - 定时清理 → pruneExpiredTokens()
 */

const crypto = require("crypto");

const config = require("../config");
const { getDb, scheduleFlush } = require("../db");

/**
 * 创建新 token
 * @returns {{token: string, expiresAt: number}}
 */
function createToken(ip, mac, gatewayId, username) {
  const db = getDb();
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

/**
 * 验证 token 是否有效
 */
function validateToken(token) {
  if (!token || typeof token !== "string") return false;
  const db = getDb();
  const now = Math.floor(Date.now() / 1000);

  const r = db.exec(
    "SELECT 1 FROM tokens WHERE token=? AND active=1 AND expires_at>?",
    [token, now]
  );

  return r.length > 0 && r[0].values.length > 0;
}

/**
 * 获取 token 详情
 */
function getToken(token) {
  if (!token || typeof token !== "string") return null;
  const db = getDb();
  const now = Math.floor(Date.now() / 1000);

  const r = db.exec(
    "SELECT * FROM tokens WHERE token=? AND active=1 AND expires_at>?",
    [token, now]
  );

  if (!r.length || !r[0].values.length) return null;

  const out = {};
  r[0].columns.forEach((c, i) => {
    out[c] = r[0].values[0][i];
  });
  return out;
}

/**
 * 停用 token
 */
function deactivateToken(token) {
  const db = getDb();
  db.run("UPDATE tokens SET active=0 WHERE token=?", [token]);
  scheduleFlush();
}

/**
 * 清理过期 / 已停用 token
 * @returns {number} 清理条数
 */
function pruneExpiredTokens() {
  const db = getDb();
  const now = Math.floor(Date.now() / 1000);

  const before = db.exec("SELECT COUNT(*) FROM tokens WHERE active=0 OR expires_at<=?", [now]);
  const count = before[0]?.values?.[0]?.[0] || 0;

  db.run("DELETE FROM tokens WHERE active=0 OR expires_at<=?", [now]);
  scheduleFlush();

  return count;
}

/**
 * 统计活跃 token 数
 */
function countActiveTokens() {
  const db = getDb();
  const r = db.exec("SELECT COUNT(*) FROM tokens WHERE active=1");
  return r[0]?.values?.[0]?.[0] || 0;
}

module.exports = {
  createToken,
  validateToken,
  getToken,
  deactivateToken,
  pruneExpiredTokens,
  countActiveTokens,
};
