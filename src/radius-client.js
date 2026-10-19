const radius = require('radius');
const dgram = require('dgram');
const HOST = process.env.RADIUS_SERVER_HOST || '127.0.0.1';
const PORT = parseInt(process.env.RADIUS_SERVER_PORT || '1812', 10);
const SECRET = process.env.RADIUS_SECRET || 'testing123';
function authenticateUser(username, password) {
  return new Promise((resolve, reject) => {
    const client = dgram.createSocket('udp4');
    const packet = radius.encode({ code: 'Access-Request', secret: SECRET, attributes: [['User-Name', username], ['User-Password', password], ['NAS-IP-Address', '127.0.0.1'], ['NAS-Port', 0]] });
    const timeout = setTimeout(() => { client.close(); reject(new Error('RADIUS timeout')); }, 5000);
    client.on('message', (resp) => { clearTimeout(timeout); try { const d = radius.decode({ packet: resp, secret: SECRET }); client.close(); resolve(d.code === 'Access-Accept' ? { success: true } : { success: false }); } catch (e) { reject(e); } });
    client.on('error', (e) => { clearTimeout(timeout); client.close(); reject(e); });
    client.send(packet, 0, packet.length, PORT, HOST);
  });
}
module.exports = { authenticateUser };