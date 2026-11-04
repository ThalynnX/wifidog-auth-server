"use strict";

/**
 * 请求日志中间件
 * 
 * 记录每个请求的方法、URL、状态码、耗时
 */

const logger = require("../utils/logger");

function requestLogger(req, res, next) {
  const start = Date.now();

  res.on("finish", () => {
    logger.debug("http request", {
      method: req.method,
      url: req.originalUrl,
      status: res.statusCode,
      ms: Date.now() - start,
    });
  });

  next();
}

module.exports = { requestLogger };
