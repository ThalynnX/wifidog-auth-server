"use strict";

/**
 * 用户记录管理
 * 
 * 被 routes/portal.js 调用:
 *   - POST /portal 成功 → recordUser()
 */

const { getDb, scheduleFlush } = require("../db");

/**
 * 记录已认证用户 (去重)
 */
function recordUser(username) {
  const db = getDb();
  const now = Math.floor(Date.now() / 1000);

  db.run(
    "INSERT OR IGNORE INTO users(username, created_at) VALUES(?,?)",
    [username, now]
  );

  scheduleFlush();
}

/**
 * 统计用户数
 */
function countUsers() {
  const db = getDb();
  const r = db.exec("SELECT COUNT(*) FROM users");
  return r[0]?.values?.[0]?.[0] || 0;
}

module.exports = {
  recordUser,
  countUsers,
};
