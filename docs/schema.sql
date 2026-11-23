-- WiFiDog Auth Server — SQLite 数据库结构（仅 DDL，不含任何数据）
-- 15 张表；适用于空库手工初始化。正常部署由应用启动时自动建表并补字段。
PRAGMA foreign_keys=ON;

CREATE TABLE users (
	id INTEGER NOT NULL, 
	phone VARCHAR(20), 
	username VARCHAR(64), 
	password_hash VARCHAR(255), 
	nickname VARCHAR(64), 
	avatar VARCHAR(255), 
	status INTEGER NOT NULL, 
	balance INTEGER NOT NULL, 
	last_login_at DATETIME, 
	last_login_ip VARCHAR(64), 
	remark VARCHAR(255), 
	created_at DATETIME NOT NULL, mac VARCHAR(32) DEFAULT '', mac_updated_at DATETIME, mac_change_count INTEGER DEFAULT 0, device_change_limit INTEGER DEFAULT 3 NOT NULL, device_change_window_start DATETIME, alipay_user_id VARCHAR(64), alipay_nickname VARCHAR(128) DEFAULT '', alipay_avatar VARCHAR(255) DEFAULT '', alipay_bound_at DATETIME,
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_users_username ON users (username);
CREATE UNIQUE INDEX ix_users_phone ON users (phone);
CREATE TABLE devices (
	id INTEGER NOT NULL, 
	gw_id VARCHAR(64) NOT NULL, 
	name VARCHAR(128), 
	location VARCHAR(255), 
	secret VARCHAR(128), 
	status INTEGER NOT NULL, 
	online INTEGER NOT NULL, 
	last_seen_at DATETIME, 
	last_ip VARCHAR(64), 
	sys_uptime BIGINT, 
	sys_memfree BIGINT, 
	sys_load FLOAT, 
	wifidog_uptime BIGINT, 
	remark VARCHAR(255), 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_devices_gw_id ON devices (gw_id);
CREATE TABLE plans (
	id INTEGER NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	type VARCHAR(20) NOT NULL, 
	price INTEGER NOT NULL, 
	original_price INTEGER NOT NULL, 
	duration_minutes INTEGER, 
	traffic_mb INTEGER, 
	valid_days INTEGER, 
	speed_limit_up INTEGER, 
	speed_limit_down INTEGER, 
	device_limit INTEGER, 
	status INTEGER NOT NULL, 
	sort INTEGER, 
	description TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE orders (
	id INTEGER NOT NULL, 
	order_no VARCHAR(64) NOT NULL, 
	user_id INTEGER NOT NULL, 
	plan_id INTEGER NOT NULL, 
	plan_name VARCHAR(128), 
	amount INTEGER NOT NULL, 
	status INTEGER NOT NULL, 
	pay_type VARCHAR(20), 
	trade_no VARCHAR(128), 
	paid_at DATETIME, 
	expire_at DATETIME, 
	created_at DATETIME NOT NULL, refund_at DATETIME, refund_reason VARCHAR(255) DEFAULT '', refund_no VARCHAR(64) DEFAULT '', notify_id VARCHAR(128) DEFAULT '', alipay_trade_status VARCHAR(32) DEFAULT '', last_query_at DATETIME, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_orders_user ON orders (user_id);
CREATE UNIQUE INDEX ix_orders_order_no ON orders (order_no);
CREATE TABLE subscriptions (
	id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	plan_id INTEGER NOT NULL, 
	plan_name VARCHAR(128), 
	order_id INTEGER, 
	start_at DATETIME NOT NULL, 
	expire_at DATETIME, 
	traffic_total_mb INTEGER, 
	traffic_used_mb FLOAT, 
	speed_limit_up INTEGER, 
	speed_limit_down INTEGER, 
	device_limit INTEGER, 
	status INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_sub_user ON subscriptions (user_id);
CREATE TABLE sessions (
	id INTEGER NOT NULL, 
	token VARCHAR(64) NOT NULL, 
	user_id INTEGER NOT NULL, 
	gw_id VARCHAR(64), 
	mac VARCHAR(32), 
	ip VARCHAR(64), 
	status INTEGER NOT NULL, 
	start_at DATETIME NOT NULL, 
	stop_at DATETIME, 
	last_update_at DATETIME NOT NULL, 
	incoming_bytes BIGINT, 
	outgoing_bytes BIGINT, 
	user_agent VARCHAR(255), 
	created_at DATETIME NOT NULL, down_mbps FLOAT DEFAULT 0, up_mbps FLOAT DEFAULT 0, last_rate_at DATETIME, auth_logged INTEGER NOT NULL DEFAULT 0, revoked INTEGER NOT NULL DEFAULT 0, 
	PRIMARY KEY (id), 
	UNIQUE (token)
);
CREATE INDEX ix_sessions_gw_id ON sessions (gw_id);
CREATE INDEX ix_sess_user ON sessions (user_id);
CREATE INDEX ix_sess_token ON sessions (token);
CREATE INDEX ix_sess_mac ON sessions (mac);
CREATE TABLE sms_codes (
	id INTEGER NOT NULL, 
	phone VARCHAR(20) NOT NULL, 
	code VARCHAR(12) NOT NULL, 
	purpose VARCHAR(20), 
	ip VARCHAR(64), 
	expire_at DATETIME NOT NULL, 
	used BOOLEAN, 
	created_at DATETIME NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_sms_codes_phone ON sms_codes (phone);
CREATE TABLE auth_logs (
	id INTEGER NOT NULL, 
	user_id INTEGER, 
	phone VARCHAR(20), 
	gw_id VARCHAR(64), 
	mac VARCHAR(32), 
	ip VARCHAR(64), 
	action VARCHAR(20), 
	result VARCHAR(20), 
	reason VARCHAR(255), 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_log_created ON auth_logs (created_at);
CREATE INDEX ix_auth_logs_gw_id ON auth_logs (gw_id);
CREATE TABLE admins (
	id INTEGER NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	role VARCHAR(20), 
	status INTEGER NOT NULL, 
	last_login_at DATETIME, 
	last_login_ip VARCHAR(64), 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_admins_username ON admins (username);
CREATE TABLE settings (
	"key" VARCHAR(64) NOT NULL, 
	value TEXT, 
	updated_at DATETIME, 
	PRIMARY KEY ("key")
);
CREATE TABLE mac_whitelist (
	id INTEGER NOT NULL, 
	mac VARCHAR(32) NOT NULL, 
	note VARCHAR(255), 
	status INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_mac_whitelist_mac ON mac_whitelist (mac);
CREATE TABLE ip_whitelist (
	id INTEGER NOT NULL, 
	cidr VARCHAR(64) NOT NULL, 
	note VARCHAR(255), 
	status INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_ip_whitelist_cidr ON ip_whitelist (cidr);
CREATE TABLE mac_blacklist (
	id INTEGER NOT NULL, 
	mac VARCHAR(32) NOT NULL, 
	note VARCHAR(255), 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_mac_blacklist_mac ON mac_blacklist (mac);
CREATE TABLE alipay_oauth_states (
	id INTEGER NOT NULL, 
	state VARCHAR(96) NOT NULL, 
	payload TEXT, 
	used BOOLEAN NOT NULL, 
	expire_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_alipay_oauth_states_state ON alipay_oauth_states (state);
CREATE TABLE payment_events (
	id INTEGER NOT NULL, 
	channel VARCHAR(20) NOT NULL, 
	event_type VARCHAR(32) NOT NULL, 
	event_key VARCHAR(160) NOT NULL, 
	order_no VARCHAR(64), 
	trade_no VARCHAR(128), 
	payload_hash VARCHAR(64), 
	payload TEXT, 
	status VARCHAR(32), 
	note VARCHAR(255), 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_payment_events_event_key ON payment_events (event_key);
CREATE INDEX ix_payment_events_order_no ON payment_events (order_no);
CREATE UNIQUE INDEX ux_users_alipay_user_id ON users(alipay_user_id) WHERE alipay_user_id IS NOT NULL;
