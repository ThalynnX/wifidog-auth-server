# WiFiDog Auth Server (FreeRADIUS Backend)

Based on [FreeRADIUS](https://github.com/FreeRADIUS/freeradius-server) - secondary development project.

## Overview

WiFiDog captive portal authentication server using Node.js + Express with FreeRADIUS as the RADIUS backend.

## Architecture

```
[WiFi Client] -> [WiFiDog Gateway] -> [Auth Server (Node.js)] -> [FreeRADIUS]
                                              |
                                        [SQLite DB]
```

## Quick Start

### Docker (Recommended)

```bash
git clone https://github.com/leoNEW915/wifidog-auth-server.git
cd wifidog-auth-server
docker-compose up -d
```

Server runs at `http://localhost:2060`.

### Local Development

```bash
npm install
cp .env.example .env
npm start
```

## Configuration

### Environment (.env)

```
PORT=2060
RADIUS_SERVER_HOST=127.0.0.1
RADIUS_SERVER_PORT=1812
RADIUS_SECRET=testing123
TOKEN_EXPIRY=3600
```

### WiFiDog Gateway (/etc/wifidog.conf)

```
AuthServerHostname your-server-ip
AuthServerPort 2060
AuthServerPath /
GatewayID my-gateway-001
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/login` | GET | Gateway redirect entry |
| `/portal` | GET/POST | User login page |
| `/auth` | GET | Token validation (gateway polling) |
| `/ping` | GET | Gateway heartbeat |
| `/msg` | GET | Display message |
| `/status` | GET | Server status (JSON) |

## Test Users

- `testuser` / `testpass`
- `admin` / `admin123`

## Project Structure

```
wifidog-auth-server/
├── src/
│   ├── server.js          # WiFiDog protocol (Express)
│   ├── db.js              # SQLite session store
│   ├── radius-client.js   # FreeRADIUS RADIUS client
│   └── views/
│       ├── portal.ejs     # Login page
│       └── message.ejs    # Message page
├── freeradius/
│   ├── radiusd.conf       # FreeRADIUS config
│   ├── clients.conf       # RADIUS clients
│   └── users              # User credentials
├── docker-compose.yml
├── Dockerfile
└── README.md
```

## Production Tips

1. Use Nginx reverse proxy for HTTPS
2. Change default RADIUS secret
3. Use MySQL/PostgreSQL for user storage
4. Enable logging and monitoring
5. Backup SQLite database regularly

## Based On

- [FreeRADIUS](https://github.com/FreeRADIUS/freeradius-server) - RADIUS server
- [WiFiDog](https://wifidog.github.io/) - Captive portal protocol

## License

MIT