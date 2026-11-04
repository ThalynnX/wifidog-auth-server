"use strict";

/**
 * WiFiDog Auth Server - 启动入口
 * 
 * 模块调用链:
 *   server.js (入口)
 *     → config/index.js (加载并验证配置)
 *     → db/index.js (初始化数据库)
 *     → core/app.js (创建 Express 应用)
 *     → core/lifecycle.js (生命周期管理)
 */

require("dotenv").config();

const config = require("./config");
const logger = require("./utils/logger");
const { initDatabase, closeDatabase } = require("./db");
const { createApp } = require("./core/app");
const { setupLifecycle } = require("./core/lifecycle");

async function main() {
  logger.info("starting wifidog auth server v3", {
    env: config.nodeEnv,
    host: config.host,
    port: config.port,
  });

  // 1. 初始化数据库
  await initDatabase();
  logger.info("database ready");

  // 2. 创建 Express 应用
  const app = createApp(config);

  // 3. 启动 HTTP 服务
  const server = app.listen(config.port, config.host, () => {
    logger.info("server listening", {
      url: `http://${config.host}:${config.port}`,
      authServerUrl: config.authServerUrl,
    });
  });

  // 4. 设置生命周期 (优雅关闭 / 定时清理)
  setupLifecycle({ server, closeDatabase, config });
}

main().catch((err) => {
  logger.error("fatal startup error", { error: err.message, stack: err.stack });
  process.exit(1);
});
