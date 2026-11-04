"use strict";

/**
 * 数据库初始化与管理
 * 
 * 使用 sql.js (WASM SQLite)，内存数据库 + 防抖写盘。
 * 调用链: server.js → initDatabase() → db/schema.js → 建表
 */

const initSqlJs = require("sql.js");
const fs = require("fs");
const path = require("path");

const config = require("../config");
const logger = require("../utils/logger");
const { SCHEMA_SQL } = require("./schema");

let db = null;
let dirty = false;
let flushTimer = null;

function ensureDir() {
  const dir = path.dirname(config.db.path);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

/**
 * 标记脏数据并调度防抖写入
 */
function scheduleFlush() {
  dirty = true;
  if (flushTimer) return;

  flushTimer = setTimeout(() => {
    flushTimer = null;
    flushNow();
  }, config.db.flushIntervalMs);

  if (typeof flushTimer.unref === "function") {
    flushTimer.unref();
  }
}

function flushNow() {
  if (!db || !dirty) return;
  try {
    fs.writeFileSync(config.db.path, Buffer.from(db.export()));
    dirty = false;
  } catch (err) {
    logger.error("database flush failed", { error: err.message });
  }
}

/**
 * 初始化数据库: 加载已有文件或新建
 */
async function initDatabase() {
  ensureDir();

  const SQL = await initSqlJs();
  db = fs.existsSync(config.db.path)
    ? new SQL.Database(fs.readFileSync(config.db.path))
    : new SQL.Database();

  // 执行建表语句
  db.run(SCHEMA_SQL);

  logger.info("database initialized", { path: config.db.path });
  return db;
}

function closeDatabase() {
  if (flushTimer) clearTimeout(flushTimer);
  flushNow();
  db = null;
}

function getDb() {
  return db;
}

function isReady() {
  return db !== null;
}

module.exports = {
  initDatabase,
  closeDatabase,
  getDb,
  isReady,
  scheduleFlush,
};
