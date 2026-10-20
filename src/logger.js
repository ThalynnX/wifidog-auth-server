"use strict";

const LEVELS = { debug: 10, info: 20, warn: 30, error: 40 };
const LEVEL_NAMES = { 10: "debug", 20: "info", 30: "warn", 40: "error" };

let threshold = LEVELS[process.env.LOG_LEVEL || "info"] ?? LEVELS.info;

function log(level, msg, meta) {
  const num = LEVELS[level];
  if (num === undefined || num < threshold) return;
  const entry = { ts: new Date().toISOString(), level: LEVEL_NAMES[num] || level, msg };
  if (meta && typeof meta === "object" && Object.keys(meta).length > 0) {
    Object.assign(entry, meta);
  }
  const line = JSON.stringify(entry);
  if (num >= LEVELS.error) console.error(line);
  else console.log(line);
}

module.exports = {
  debug: (msg, meta) => log("debug", msg, meta),
  info: (msg, meta) => log("info", msg, meta),
  warn: (msg, meta) => log("warn", msg, meta),
  error: (msg, meta) => log("error", msg, meta),
  setLevel: (level) => {
    const n = LEVELS[level];
    if (n !== undefined) threshold = n;
  },
};
