"use strict";

const radius = require("radius");
const dgram = require("dgram");
const config = require("./config");
const logger = require("./logger");

function sendRequest(username, password, { gatewayId } = {}) {
  return new Promise((resolve, reject) => {
    const client = dgram.createSocket("udp4");
    let settled = false;

    const attributes = [
      ["User-Name", username],
      ["User-Password", password],
      ["NAS-IP-Address", config.radius.nasIp],
      ["NAS-Port", config.radius.nasPort],
    ];
    if (gatewayId) attributes.push(["NAS-Identifier", String(gatewayId).slice(0, 64)]);

    const packet = radius.encode({
      code: "Access-Request",
      secret: config.radius.secret,
      attributes,
    });

    const timeout = setTimeout(() => {
      if (settled) return;
      settled = true;
      client.close();
      reject(new Error("RADIUS timeout"));
    }, config.radius.timeoutMs);

    client.on("message", (resp) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      try {
        const decoded = radius.decode({ packet: resp, secret: config.radius.secret });
        client.close();
        resolve({ success: decoded.code === "Access-Accept", code: decoded.code });
      } catch (err) {
        client.close();
        reject(err);
      }
    });

    client.on("error", (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      client.close();
      reject(err);
    });

    client.send(packet, 0, packet.length, config.radius.port, config.radius.host, (err) => {
      if (err && !settled) {
        settled = true;
        clearTimeout(timeout);
        client.close();
        reject(err);
      }
    });
  });
}

/**
 * Authenticate a user against FreeRADIUS with retries.
 * @returns {Promise<{success: boolean, code?: string}>}
 */
async function authenticateUser(username, password, opts = {}) {
  const attempts = Math.max(0, Math.min(5, config.radius.retries));
  let lastError = null;
  for (let i = 0; i <= attempts; i++) {
    try {
      return await sendRequest(username, password, opts);
    } catch (err) {
      lastError = err;
      logger.warn("radius request failed, retrying", { attempt: i, error: err.message });
    }
  }
  throw lastError || new Error("RADIUS request failed");
}

module.exports = { authenticateUser };
