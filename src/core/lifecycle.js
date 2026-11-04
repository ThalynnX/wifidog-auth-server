"use strict";

/**
 * 进程生命周期管理
 * 
 * - 优雅关闭 (SIGTERM / SIGINT)
 * - 定时清理过期 token
 * - 未捕获异常处理
 */

const logger = require("../utils/logger");
const { pruneExpiredTokens } = require("../services/token-service");

function setupLifecycle({ server, closeDatabase, config }) {
  // 定时清理过期 token
  const cleanupInterval = setInterval(() => {
    try {
      const removed = pruneExpiredTokens();
      if (removed > 0) {
        logger.debug("pruned expired tokens", { count: removed });
      }
    } catch (err) {
      logger.error("token cleanup failed", { error: err.message });
    }
  }, config.gwCheckIntervalSec * 1000);

  if (typeof cleanupInterval.unref === "function") {
    cleanupInterval.unref();
  }

  // 优雅关闭
  const shutdown = (signal) => {
    logger.info("shutting down", { signal });
    clearInterval(cleanupInterval);

    server.close(() => {
      closeDatabase();
      logger.info("shutdown complete");
      process.exit(0);
    });

    // 强制退出兜底
    setTimeout(() => process.exit(1), 5000).unref();
  };

  process.on("SIGTERM", () => shutdown("SIGTERM"));
  process.on("SIGINT", () => shutdown("SIGINT"));

  // 未捕获异常
  process.on("unhandledRejection", (reason) => {
    logger.error("unhandledRejection", { error: String(reason) });
  });

  process.on("uncaughtException", (err) => {
    logger.error("uncaughtException", { error: err.message, stack: err.stack });
    shutdown("uncaughtException");
  });
}

module.exports = { setupLifecycle };
