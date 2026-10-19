const initSqlJs = require('sql.js');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const dbPath = process.env.DB_PATH || './data/wifidog.db';
const dbDir = path.dirname(dbPath);
if (!fs.existsSync(dbDir)) fs.mkdirSync(dbDir, { recursive: true });
let db = null;
async function initDatabase() {
  const SQL = await initSqlJs();
  db = fs.existsSync(dbPath) ? new SQL.Database(fs.readFileSync(dbPath)) : new SQL.Database();
  db.run('CREATE TABLE IF NOT EXISTS tokens (token TEXT PRIMARY KEY, ip TEXT, mac TEXT, gateway_id TEXT, username TEXT, created_at INTEGER, expires_at INTEGER, active INTEGER DEFAULT 1)');
  db.run('CREATE TABLE IF NOT EXISTS gateways (gateway_id TEXT PRIMARY KEY, ip TEXT, last_ping INTEGER, sys_uptime INTEGER, sys_memfree INTEGER, sys_load REAL, wl_count INTEGER)');
  db.run('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, email TEXT, created_at INTEGER)');
  saveDatabase(); return db;
}
function saveDatabase() { if(db){ fs.writeFileSync(dbPath, Buffer.from(db.export())); } }
function createToken(ip,mac,gw,user) {
  const token=crypto.randomBytes(16).toString('hex'), now=Math.floor(Date.now()/1000), exp=parseInt(process.env.TOKEN_EXPIRY||'3600',10);
  db.run('INSERT INTO tokens VALUES(?,?,?,?,?,?,?,1)',[token,ip,mac,gw,user,now,now+exp]); saveDatabase(); return {token,expiresAt:now+exp};
}
function validateToken(t) { const r=db.exec('SELECT 1 FROM tokens WHERE token=? AND active=1 AND expires_at>?',[t,Math.floor(Date.now()/1000)]); return r.length>0&&r[0].values.length>0; }
function getToken(t) { const r=db.exec('SELECT * FROM tokens WHERE token=? AND active=1 AND expires_at>?',[t,Math.floor(Date.now()/1000)]); if(!r.length||!r[0].values.length)return null; const o={}; r[0].columns.forEach((c,i)=>o[c]=r[0].values[0][i]); return o; }
function deactivateToken(t) { db.run('UPDATE tokens SET active=0 WHERE token=?',[t]); saveDatabase(); }
function upsertGateway(gid,ip,up,mf,sl,wc) {
  const now=Math.floor(Date.now()/1000), ex=db.exec('SELECT 1 FROM gateways WHERE gateway_id=?',[gid]);
  if(ex.length&&ex[0].values.length) db.run('UPDATE gateways SET ip=?,last_ping=?,sys_uptime=?,sys_memfree=?,sys_load=?,wl_count=? WHERE gateway_id=?',[ip,now,up,mf,sl,wc,gid]);
  else db.run('INSERT INTO gateways VALUES(?,?,?,?,?,?,?)',[gid,ip,now,up,mf,sl,wc]);
  saveDatabase();
}
function recordUser(u) { try{ db.run('INSERT INTO users(username,created_at) VALUES(?,?)',[u,Math.floor(Date.now()/1000)]); saveDatabase(); }catch(e){} }
function getDb() { return db; }
module.exports = { initDatabase, createToken, validateToken, getToken, deactivateToken, upsertGateway, recordUser, getDb };