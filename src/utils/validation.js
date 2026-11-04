"use strict";

/**
 * 输入验证工具
 * 
 * - IP / MAC 格式校验
 * - 私有 IP 检测 (防开放重定向)
 * - 字符串截断清洗
 */

const IPV4_RE = /^(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$/;
const MAC_RE = /^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$/;

function isPrivateIpv4(ip) {
  if (!IPV4_RE.test(ip)) return false;
  const [a, b] = ip.split(".").map(Number);
  return (
    a === 10 ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168) ||
    a === 127 ||
    (a === 169 && b === 254)
  );
}

function isPrivateIpv6(ip) {
  if (ip === "::1") return true;
  return /^fe[89ab][0-9a-f]:/i.test(ip) || /^fd/i.test(ip);
}

function isValidMac(mac) {
  return typeof mac === "string" && MAC_RE.test(mac.trim());
}

/**
 * 校验网关地址是否安全 (仅允许内网地址，防开放重定向)
 * @param {string} addr
 * @returns {string|null} 安全地址或 null
 */
function safeGatewayAddress(addr) {
  if (typeof addr !== "string") return null;
  const t = addr.trim();
  if (!t || t.length > 253) return null;
  if (t.includes("://")) return null;
  return isPrivateIpv4(t) || isPrivateIpv6(t) ? t : null;
}

/**
 * 字符串清洗: trim + 截断
 */
function str(v, maxLen = 128) {
  if (typeof v !== "string") return "";
  const t = v.trim();
  return t.length > maxLen ? t.slice(0, maxLen) : t;
}

module.exports = {
  isPrivateIpv4,
  isPrivateIpv6,
  isValidMac,
  safeGatewayAddress,
  str,
};
