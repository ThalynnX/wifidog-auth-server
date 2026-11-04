"use strict";

/**
 * 网关心跳管理
 * 
 * 被 routes/wifidog.js 调用:
 *   - GET /ping → upsertGateway()
 */

const { getDb, scheduleFlush } = require("../db");

/**
 * 插入或更新网关心跳记录
 */
function upsertGateway(gatewayId, ip, uptime, memfree, load, wlCount) {
  const db = getDb();
  const now = Math.floor(Date.now() / 1000);

  db.run(
    `INSERT INTO gateways(gateway_id, ip, last_ping, sys_uptime, sys_memfree, sys_load, wl_count)
     VALUES(?,?,?,?,?,?,?)
     ON CONFLICT(gateway_id) DO UPDATE SET
       ip=excluded.ip,
       last_ping=excluded.last_ping,
       sys_uptime=excluded.sys_uptime,
       sys_memfree=excluded.sys_memfree,
       sys_load=excluded.sys_load,
       wl_count=excluded.wl_count`,
    [gatewayId, ip, now, uptime, memfree, load, wlCount]
  );

  scheduleFlush();
}

/**
 * 统计网关数
 */
function countGateways() {
  const db = getDb();
  const r = db.exec("SELECT COUNT(*) FROM gateways");
  return r[0]?.values?.[0]?.[0] || 0;
}

/**
 * 获取所有在线网关 (最近 5 分钟有心跳)
 */
function listOnlineGateways() {
  const db = getDb();
  const fiveMinAgo = Math.floor(Date.now() / 1000) - 300;

  const r = db.exec(
    "SELECT * FROM gateways WHERE last_ping > ? ORDER BY last_ping DESC",
    [fiveMinAgo]
  );

  if (!r.length) return [];

  return r[0].values.map((row) => {
    const obj = {};
    r[0].columns.forEach((c, i) => {
      obj[c] = row[i];
    });
    return obj;
  });
}

module.exports = {
  upsertGateway,
  countGateways,
  listOnlineGateways,
};
