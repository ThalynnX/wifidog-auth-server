"use strict";

/**
 * 统一错误处理
 */

const logger = require("../utils/logger");

function notFoundHandler(req, res) {
  res.status(404).send("Not Found");
}

// eslint-disable-next-line no-unused-vars
function errorHandler(err, req, res, next) {
  logger.error("unhandled error", {
    error: err.message,
    url: req.originalUrl,
    method: req.method,
  });
  res.status(500).send("Internal Server Error");
}

module.exports = { notFoundHandler, errorHandler };
