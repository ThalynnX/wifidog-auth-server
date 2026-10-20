"use strict";

const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const dgram = require("dgram");
const radius = require("radius");
const request = require("supertest");

// Isolated DB + mock RADIUS backend before loading the app
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "wifidog-srv-"));
process.env.DB_PATH = path.join(tmpDir, "test.db");
process.env.DB_FLUSH_INTERVAL_MS = "50";
process.env.RADIUS_SERVER_HOST = "127.0.0.1";
process.env.RADIUS_SERVER_PORT = "18121";
process.env.RADIUS_SECRET = "test-secret-123";
process.env.RADIUS_TIMEOUT_MS = "1500";
process.env.RADIUS_RETRIES = "1";
process.env.RATE_LIMIT_MAX = "1000";

const db = require("../src/db");
const { app } = require("../src/server");

function startMockRadius(code, secret) {
  return new Promise((resolve) => {
    const srv = dgram.createSocket("udp4");
    srv.on("message", (msg, rinfo) => {
      const req = radius.decode({ packet: msg, secret });
      const res = radius.encode({
        code,
        secret,
        identifier: req.identifier,
        attributes: [["Reply-Message", "mock"]],
      });
      srv.send(res, 0, res.length, rinfo.port, rinfo.address);
    });
    srv.bind(18121, "127.0.0.1", () => resolve(srv));
  });
}

test.before(async () => {
  await db.initDatabase();
});

test.after(() => {
  db.close();
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("GET /login without parameters returns 400", async () => {
  const res = await request(app).get("/login").query({});
  assert.strictEqual(res.status, 400);
});

test("GET /login redirects an unknown token to the portal", async () => {
  const res = await request(app)
    .get("/login")
    .query({ ip: "10.0.0.5", mac: "aa:bb:cc:dd:ee:ff", token: "nope", gateway_id: "gw-1" });
  assert.strictEqual(res.status, 302);
  assert.match(res.headers.location, /^\/portal\?/);
});

test("GET /login blocks open redirect via evil gateway_address", async () => {
  // create a valid token first
  const { token } = db.createToken("10.0.0.9", "aa:bb:cc:dd:ee:09", "gw-1", "alice");
  const res = await request(app)
    .get("/login")
    .query({ ip: "10.0.0.9", mac: "aa:bb:cc:dd:ee:09", token, gateway_id: "gw-1", gateway_address: "evil.com" });
  assert.strictEqual(res.status, 302);
  assert.strictEqual(res.headers.location, "http://192.168.1.1"); // safe default, not evil.com
});

test("GET /login with valid token redirects to a private gateway", async () => {
  const { token } = db.createToken("10.0.0.10", "aa:bb:cc:dd:ee:0a", "gw-1", "alice");
  const res = await request(app)
    .get("/login")
    .query({ ip: "10.0.0.10", mac: "aa:bb:cc:dd:ee:0a", token, gateway_id: "gw-1", gateway_address: "192.168.8.1" });
  assert.strictEqual(res.status, 302);
  assert.strictEqual(res.headers.location, "http://192.168.8.1");
});

test("GET /auth returns Auth:0 for missing/unknown token and Auth:1 for valid", async () => {
  const res0 = await request(app).get("/auth");
  assert.strictEqual(res0.text, "Auth: 0");

  const { token } = db.createToken("10.0.0.11", "aa:bb:cc:dd:ee:0b", "gw-1", "alice");
  const res1 = await request(app).get("/auth").query({ token });
  assert.strictEqual(res1.text, "Auth: 1");
});

test("GET /ping upserts the gateway and answers Pong", async () => {
  const res = await request(app)
    .get("/ping")
    .query({ gw_id: "gw-test", sys_uptime: "123", sys_memfree: "456", sys_load: "0.3", num_users: "7" });
  assert.strictEqual(res.text, "Pong");
  assert.strictEqual(db.countGateways(), 1);
});

test("POST /portal authenticates against mock RADIUS and issues a token", async () => {
  const srv = await startMockRadius("Access-Accept", "test-secret-123");
  try {
    const before = db.countActiveTokens();
    const res = await request(app)
      .post("/portal")
      .type("form")
      .send({
        username: "testuser",
        password: "testpass",
        ip: "10.0.0.12",
        mac: "aa:bb:cc:dd:ee:0c",
        token: "reqtoken",
        ssid: "TestNet",
        gateway_id: "gw-test",
        gateway_address: "192.168.8.1",
      });
    assert.strictEqual(res.status, 302);
    assert.strictEqual(res.headers.location, "http://192.168.8.1");
    assert.strictEqual(db.countActiveTokens(), before + 1);
    assert.strictEqual(db.countUsers(), 1);
  } finally {
    srv.close();
  }
});

test("POST /portal with bad credentials stays on the portal", async () => {
  const srv = await startMockRadius("Access-Reject", "test-secret-123");
  try {
    const res = await request(app)
      .post("/portal")
      .type("form")
      .send({ username: "testuser", password: "wrong", gateway_id: "gw-test" });
    assert.strictEqual(res.status, 401);
  } finally {
    srv.close();
  }
});

test("GET /status reports counters and version", async () => {
  const res = await request(app).get("/status");
  assert.strictEqual(res.status, 200);
  assert.strictEqual(res.body.status, "ok");
  assert.strictEqual(typeof res.body.version, "string");
  assert.ok(res.body.activeTokens >= 0);
  assert.ok(res.body.registeredGateways >= 1);
});

test("GET /health is ok", async () => {
  const res = await request(app).get("/health");
  assert.strictEqual(res.status, 200);
  assert.strictEqual(res.body.status, "ok");
});

test("unknown route returns 404", async () => {
  const res = await request(app).get("/definitely-not-a-route");
  assert.strictEqual(res.status, 404);
});
