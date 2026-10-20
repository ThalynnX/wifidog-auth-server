"use strict";

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
  if (ip === "::1") return true; // loopback
  return /^fe[89ab][0-9a-f]:/i.test(ip) || /^fd/i.test(ip); // link-local / ULA
}

function isValidMac(mac) {
  return typeof mac === "string" && MAC_RE.test(mac.trim());
}

/**
 * Validate a WiFiDog gateway address used for post-auth redirects.
 * Only private IPv4 / loopback / ULA IPv6 literals are accepted,
 * which prevents open-redirect attacks via the `gateway_address` parameter.
 * Returns the normalized address if safe, otherwise null.
 */
function safeGatewayAddress(addr) {
  if (typeof addr !== "string") return null;
  const t = addr.trim();
  if (!t || t.length > 253) return null;
  if (t.includes("://")) return null;
  return isPrivateIpv4(t) || isPrivateIpv6(t) ? t : null;
}

function str(v, maxLen = 128) {
  if (typeof v !== "string") return "";
  const t = v.trim();
  return t.length > maxLen ? t.slice(0, maxLen) : t;
}

module.exports = { isPrivateIpv4, isPrivateIpv6, isValidMac, safeGatewayAddress, str };
