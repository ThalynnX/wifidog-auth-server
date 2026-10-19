const radius = require('radius');
const dgram = require('dgram');
const RADIUS_HOST = process.env.RADIUS_SERVER_HOST || '127.0.0.1';
const RADIUS_PORT = parseInt(process.env.RADIUS_SERVER_PORT || '1812', 10);
const RADIUS_SECRET = process.env.RADIUS_SECRET || 'testing123';
function authenticateUser(username, password) {
  return new Promise((resolve, reject) => {
    const client = dgram.createSocket('udp4');
    const packet = radius.encode({ code:'Access-Request', secret:RADIUS_SECRET, attributes:[['User-Name',username],['User-Password',password],['NAS-IP-Address','127.0.0.1'],['NAS-Port',0]] });
    const timeout = setTimeout(() => { client.close(); reject(new Error('RADIUS timeout')); }, 5000);
    client.on('message', (resp) => { clearTimeout(timeout); try { const d=radius.decode({packet:resp,secret:RADIUS_SECRET}); client.close(); resolve(d.code==='Access-Accept'?{success:true,username}:{success:false}); } catch(e){ reject(e); } });
    client.on('error', (e) => { clearTimeout(timeout); client.close(); reject(e); });
    client.send(packet, 0, packet.length, RADIUS_PORT, RADIUS_HOST);
  });
}
module.exports = { authenticateUser };