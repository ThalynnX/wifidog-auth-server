"use strict";

/**
 * 安全中间件
 * 
 * - Helmet HTTP 安全头
 * - Content-Security-Policy
 */

const helmet = require("helmet");

function securityMiddleware() {
  return helmet({
    contentSecurityPolicy: {
      directives: {
        defaultSrc: ["'self'"],
        scriptSrc: ["'self'"],
        styleSrc: ["'self'"],
        imgSrc: ["'self'", "data:"],
        objectSrc: ["'none'"],
        baseUri: ["'self'"],
        formAction: ["'self'"],
        frameAncestors: ["'none'"],
      },
    },
    crossOriginEmbedderPolicy: false,
  });
}

module.exports = { securityMiddleware };
