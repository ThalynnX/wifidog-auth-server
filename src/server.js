require('dotenv').config();
const express = require('express');
const bodyParser = require('body-parser');
const path = require('path');
const { authenticateUser } = require('./radius-client');
const dbModule = require('./db');

const app = express();
const PORT = process.env.PORT || 2060;
const HOST = process.env.HOST || '0.0.0.0';

app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, 'views'));
app.use(bodyParser.urlencoded({ extended: true }));
app.use(bodyParser.json());

app.get('/login', (req, res) => {
  const { ip, mac, token, gateway_id, gateway_address } = req.query;
  if (!ip || !mac || !token || !gateway_id) {
    return res.status(400).send('Missing required parameters');
  }
  const existingToken = dbModule.getToken(token);
  if (existingToken) {
    return res.redirect(`http://${gateway_address || '192.168.1.1'}`);
  }
  res.redirect(`/portal?ip=${encodeURIComponent(ip)}&mac=${encodeURIComponent(mac)}&token=${encodeURIComponent(token)}&ssid=${encodeURIComponent(req.query.ssid || '')}&gateway_id=${encodeURIComponent(gateway_id)}&gateway_address=${encodeURIComponent(gateway_address || '')}`);
});

app.get('/portal', (req, res) => {
  const { ip, mac, token, ssid, gateway_id, gateway_address } = req.query;
  res.render('portal', { ip, mac, token, ssid, gateway_id, gateway_address, error: null });
});

app.post('/portal', async (req, res) => {
  const { username, password, ip, mac, token, ssid, gateway_id, gateway_address } = req.body;
  if (!username || !password) {
    return res.render('portal', { ip, mac, token, ssid, gateway_id, gateway_address, error: '请输入用户名和密码' });
  }
  try {
    const authResult = await authenticateUser(username, password);
    if (authResult.success) {
      dbModule.createToken(ip, mac, gateway_id, username);
      dbModule.recordUser(username);
      res.redirect(`http://${gateway_address || '192.168.1.1'}`);
    } else {
      res.render('portal', { ip, mac, token, ssid, gateway_id, gateway_address, error: '认证失败' });
    }
  } catch (err) {
    console.error('RADIUS auth error:', err);
    res.render('portal', { ip, mac, token, ssid, gateway_id, gateway_address, error: '认证服务不可用' });
  }
});

app.get('/auth', (req, res) => {
  const { token } = req.query;
  if (!token) return res.send('Auth: 0');
  res.send(dbModule.validateToken(token) ? 'Auth: 1' : 'Auth: 0');
});

app.get('/ping', (req, res) => {
  const { gw_id, sys_uptime, sys_memfree, sys_load, num_users } = req.query;
  if (gw_id) {
    dbModule.upsertGateway(gw_id, req.ip, parseInt(sys_uptime||'0',10), parseInt(sys_memfree||'0',10), parseFloat(sys_load||'0'), parseInt(num_users||'0',10));
  }
  res.send('Pong');
});

app.get('/msg', (req, res) => {
  res.render('message', { message: req.query.msg || '操作成功' });
});

app.get('/status', (req, res) => {
  const db = dbModule.getDb();
  const t = db.exec('SELECT COUNT(*) FROM tokens WHERE active=1');
  const g = db.exec('SELECT COUNT(*) FROM gateways');
  const u = db.exec('SELECT COUNT(*) FROM users');
  res.json({ status:'ok', activeTokens: t[0]?.values[0]?.[0]||0, registeredGateways: g[0]?.values[0]?.[0]||0, registeredUsers: u[0]?.values[0]?.[0]||0 });
});

async function start() {
  await dbModule.initDatabase();
  app.listen(PORT, HOST, () => {
    console.log(`WiFiDog Auth Server on http://${HOST}:${PORT}`);
  });
}
start().catch(e => { console.error(e); process.exit(1); });