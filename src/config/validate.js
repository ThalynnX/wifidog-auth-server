"use strict";

/**
 * 配置启动校验
 * 
 * 运行: node src/config/validate.js
 * 用途: 部署前检查环境变量是否正确
 */

const config = require("./index");

const errors = [];

// 生产环境必须检查的项
if (config.isProd) {
  if (config.radius.secret === "testing123") {
    errors.push("RADIUS_SECRET 必须修改默认值 (生产环境)");
  }
  if (config.authServerUrl.includes("localhost")) {
    errors.push("AUTH_SERVER_URL 必须设置为外部可访问地址");
  }
  if (config.port === 2060 && config.isProd) {
    // 警告: 默认端口
    console.warn("[warn] 使用默认端口 2060，建议修改");
  }
}

if (errors.length > 0) {
  console.error("配置校验失败:");
  errors.forEach((e) => console.error("  - " + e));
  process.exit(1);
}

console.log("配置校验通过");
console.log("  环境:", config.nodeEnv);
console.log("  端口:", config.port);
console.log("  RADIUS:", config.radius.host + ":" + config.radius.port);
console.log("  DB路径:", config.db.path);
