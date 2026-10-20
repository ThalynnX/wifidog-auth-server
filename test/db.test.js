"use strict";

const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

// Isolate this test run from any real database
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "wifidog-db-"));
process.env.DB_PATH = path.join(tmpDir, "test.db");
process.env.DB_FLUSH_INTERVAL_MS = "50";

const db = require("../src/db");

test.before(async () => {
  await db.initDatabase();
});

test.after(() => {
  db.close();
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("token lifecycle: create / validate / read / deactivate", () => {
  const { token, expiresAt } = db.createToken("10.0.0.5", "aa:bb:cc:dd:ee:ff", "gw-1", "alice");
  assert.strictEqual(token.length, 32);
  assert.ok(expiresAt > Math.floor(Date.now() / 1000));
  assert.strictEqual(db.validateToken(token), true);

  const row = db.getToken(token);
  assert.strictEqual(row.username, "alice");
  assert.strictEqual(row.gateway_id, "gw-1");
  assert.strictEqual(row.mac, "aa:bb:cc:dd:ee:ff");

  db.deactivateToken(token);
  assert.strictEqual(db.validateToken(token), false);
  assert.strictEqual(db.getToken(token), null);
});

test("expired tokens are rejected", () => {
  const { token } = db.createToken("10.0.0.6", "aa:bb:cc:dd:ee:01", "gw-1", "bob");
  db.getDb().run("UPDATE tokens SET expires_at=? WHERE token=?", [
    Math.floor(Date.now() / 1000) - 10,
    token,
  ]);
  assert.strictEqual(db.validateToken(token), false);
  assert.strictEqual(db.getToken(token), null);
});

test("invalid or missing tokens are rejected", () => {
  assert.strictEqual(db.validateToken(undefined), false);
  assert.strictEqual(db.validateToken(null), false);
  assert.strictEqual(db.validateToken(""), false);
  assert.strictEqual(db.validateToken("does-not-exist"), false);
});

test("gateway upsert inserts then updates", () => {
  db.upsertGateway("gw-1", "10.0.0.1", 100, 51200, 0.5, 4);
  assert.strictEqual(db.countGateways(), 1);
  // second ping updates the same row instead of duplicating
  db.upsertGateway("gw-1", "10.0.0.2", 200, 65536, 0.8, 9);
  assert.strictEqual(db.countGateways(), 1);
  const rows = db.getDb().exec("SELECT ip, wl_count FROM gateways WHERE gateway_id='gw-1'");
  assert.strictEqual(rows[0].values[0][0], "10.0.0.2");
  assert.strictEqual(rows[0].values[0][1], 9);
});

test("users are recorded once (unique)", () => {
  db.recordUser("alice");
  db.recordUser("alice");
  const rows = db.getDb().exec("SELECT COUNT(*) FROM users WHERE username='alice'");
  assert.strictEqual(rows[0].values[0][0], 1);
});

test("pruneExpiredTokens removes stale rows", () => {
  db.getDb().run("UPDATE tokens SET expires_at=? WHERE token IS NOT NULL", [
    Math.floor(Date.now() / 1000) - 100,
  ]);
  db.pruneExpiredTokens();
  assert.strictEqual(db.countActiveTokens(), 0);
});
