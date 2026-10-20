"use strict";

const test = require("node:test");
const assert = require("node:assert");
const dgram = require("dgram");
const radius = require("radius");

// Point the client at a mock RADIUS server on an isolated port.
process.env.RADIUS_SERVER_HOST = "127.0.0.1";
process.env.RADIUS_SERVER_PORT = "18120";
process.env.RADIUS_SECRET = "test-secret-123";
process.env.RADIUS_TIMEOUT_MS = "1500";
process.env.RADIUS_RETRIES = "1";

const { authenticateUser } = require("../src/radius-client");

function startMockServer(code, secret) {
  return new Promise((resolve) => {
    const srv = dgram.createSocket("udp4");
    srv.on("message", (msg, rinfo) => {
      const req = radius.decode({ packet: msg, secret });
      const res = radius.encode({
        code,
        secret,
        identifier: req.identifier,
        attributes: [["Reply-Message", "mock-reply"]],
      });
      srv.send(res, 0, res.length, rinfo.port, rinfo.address);
    });
    srv.bind(18120, "127.0.0.1", () => resolve(srv));
  });
}

test("Access-Accept yields success", async () => {
  const srv = await startMockServer("Access-Accept", "test-secret-123");
  try {
    const result = await authenticateUser("testuser", "testpass", { gatewayId: "gw-1" });
    assert.strictEqual(result.success, true);
    assert.strictEqual(result.code, "Access-Accept");
  } finally {
    srv.close();
  }
});

test("Access-Reject yields failure", async () => {
  const srv = await startMockServer("Access-Reject", "test-secret-123");
  try {
    const result = await authenticateUser("testuser", "wrongpass");
    assert.strictEqual(result.success, false);
    assert.strictEqual(result.code, "Access-Reject");
  } finally {
    srv.close();
  }
});

test("timeout is surfaced after retries", async () => {
  // Nothing listens on this port -> the client should time out and reject
  process.env.RADIUS_SERVER_PORT = "18129";
  const { authenticateUser: auth2 } = require("../src/radius-client");
  await assert.rejects(auth2("u", "p"), /RADIUS/);
});
