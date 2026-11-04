"use strict";

/**
 * 数据库表结构定义
 * 
 * 表:
 *   tokens   - 认证 token (IP/MAC/网关/用户/过期时间)
 *   gateways - 网关心跳记录
 *   users    - 已认证用户记录
 */

const SCHEMA_SQL = `
  CREATE TABLE IF NOT EXISTS tokens (
    token TEXT PRIMARY KEY,
    ip TEXT NOT NULL,
    mac TEXT,
    gateway_id TEXT,
    username TEXT,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    active INTEGER DEFAULT 1
  );
  CREATE INDEX IF NOT EXISTS idx_tokens_active ON tokens(active, expires_at);
  CREATE INDEX IF NOT EXISTS idx_tokens_ip ON tokens(ip);

  CREATE TABLE IF NOT EXISTS gateways (
    gateway_id TEXT PRIMARY KEY,
    ip TEXT,
    last_ping INTEGER,
    sys_uptime INTEGER,
    sys_memfree INTEGER,
    sys_load REAL,
    wl_count INTEGER
  );
  CREATE INDEX IF NOT EXISTS idx_gateways_ping ON gateways(last_ping);

  CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    email TEXT,
    created_at INTEGER
  );
`;

module.exports = { SCHEMA_SQL };
