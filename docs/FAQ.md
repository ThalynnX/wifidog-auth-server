# 常见问题（FAQ）

## 一、部署相关

### 1. 默认管理员账号是什么？怎么改？

`admin / admin123`。首次登录后请在「系统设置 → 修改密码」立即修改。
密码用 bcrypt 存储，**没有默认后门**。

### 2. 忘记管理员密码怎么办？

在服务器上直接改库（先停服务更稳妥）：

```bash
systemctl stop wifidog
cd /opt/wifidog
./venv/bin/python - <<'PY'
from passlib.hash import bcrypt
from app.database import SessionLocal
from app.models import Admin
db = SessionLocal()
a = db.query(Admin).filter(Admin.username == "admin").first()
a.password_hash = bcrypt.hash("NewPass@2026")
db.commit()
print("已重置")
PY
systemctl start wifidog
```

> 若模型类名/字段与你的版本不同，用 `grep -n "class Admin" app/models.py` 确认。

### 3. 应用端口为什么是 8000？能改成 8088 / 60081 吗？

可以。改 `.env`：

```ini
WIFIDOG_BIND=127.0.0.1:8088
```

同时把 `deploy/nginx_wifidog.conf` 的 `upstream` 改成同一端口，然后
`systemctl restart wifidog && nginx -t && systemctl reload nginx`。

> 旧资料里的 `8088`、`60080/60081` 是历史部署端口，本仓库默认 `8000`，不影响功能。

### 4. 必须用域名和 HTTPS 吗？

- 只做内网测试：IP + HTTP 即可；
- 正式收费：**必须**域名 + HTTPS。微信/支付宝回调、短信模板审核、部分手机的 captive portal
  检测都要求公网 HTTPS。

### 5. 能不能不用 Nginx，直接跑 Gunicorn 对外？

技术上可以（`WIFIDOG_BIND=0.0.0.0:8000`），但不建议：没有 TLS、没有静态优化、WebSocket
长连接与限流都更脆弱。生产请用 Nginx。

### 6. 支持 Docker 吗？

当前仓库未内置 Dockerfile（旧 Node 版有）。用 Docker 时把 `python:3.12-slim` 作为基础镜像，
`pip install -r requirements.txt`，挂载 `data/` 与 `.env`，启动命令与 systemd 的 `ExecStart` 相同即可。
注意 SQLite 文件必须在持久卷上。

---

## 二、认证与计费

### 7. 为什么网关一直返回 `Auth: 0`？

按后台「认证日志」的 `reason` 查，常见原因：

| reason | 含义 | 处理 |
|---|---|---|
| `no_token` / `bad_token` | token 缺失或不合法 | 重新从门户登录 |
| `token_expired` | 超过 24 小时 | 重新登录 |
| `user_disabled` | 用户被禁用 | 后台启用 |
| `no_plan` | 没有有效套餐 | 购买/后台开通 |
| `plan_expired` | 套餐到期 | 续费 |
| `traffic_exhausted` | 流量用完 | 续费/升级 |
| `device_limit` | 在线设备超限 | 换绑/升级 |
| `blacklist` | MAC 在黑名单 | 后台移除 |
| `gw_disabled` | 网关被禁用 | 后台启用 |

### 8. 免费体验怎么算？会不会被反复薅？

- 默认新设备 **10 分钟 / 100MB**，在后台「系统设置」可改，设 0 关闭；
- 免费体验按**设备（MAC）**判定，换 MAC 可重领，这是 captive portal 的固有边界；
- 防薅建议：调小免费额度、开启 MAC 黑名单、对异常高频设备人工封禁。

### 9. 一个账号能几台设备同时在线？

默认 3 台（`WIFIDOG_DEVICE_LIMIT` 或后台设置）。套餐里也可以单独设 `device_limit`。
超限时新设备认证会被拒，提示“设备数量已达上限”。

### 10. 用户换手机/换 WiFi 卡怎么办？

门户提供「换绑设备」，有次数限制（`mac_change_count`），防止一号多机。
管理员也可在后台用户详情里处理。

### 11. 流量统计准不准？

流量来自网关 `counters` 上报的累计字节数：

- apfree-wifidogx：支持 `counters_v2`，精度较好；
- 标准 wifidog：仅 `counters`，按周期累计，精度一般；
- 白名单设备不扣套餐流量；
- 网关重启会导致本轮累计清零，服务端以会话已记录值为准。

---

## 三、路由器相关

### 12. 手机连上 WiFi 不弹认证页？

按顺序排查：

1. 路由器 `wifidogx` 是否在跑：`/etc/init.d/wifidogx status`；
2. 是否选错模式（纯 AP/旁路导致流量不经过本机）；
3. `EnableDNSForward`、`JS Filter` 是否开启；
4. `Apple CNA` 是否开启（iOS 需要）；
5. 手动打开 `http://neverssl.com` 试试（HTTPS 站点不会触发劫持）；
6. 手机曾点过“取消”，系统会记住；忘记该 WiFi 重连。

### 13. 路由器装完 LuCI 没有菜单？

- 只装了主程序没装 `luci-app`；
- LuCI 缓存：`rm -rf /tmp/luci-* && /etc/init.d/rpcd restart`；
- 浏览器强刷（Ctrl+F5）；
- 确认固件版本与包格式匹配（apk vs opkg）。

### 14. 哪些路由器可以直接用仓库里的包？

`router-packages/` 里的 aarch64 包对应 **qualcommax / ipq60xx / aarch64_cortex-a53**，
典型机型京东云亚瑟 AX1800。其它架构需自行编译，见
[ROUTER_SETUP.md](ROUTER_SETUP.md) 第 12 节。

### 15. 旁路/二级路由模式要注意什么？

终端默认网关必须是本机。要么本机开 DHCP、上游关 DHCP；要么上游 DHCP 把网关指向本机。
否则终端直接走上游，认证不生效。

### 16. 有线口也要认证吗？

默认不强制。`Wired Passed=1` 时所有有线口免认证；要做“有线也认证”，关掉它，并把需要免认证的
设备加到后台 IP/MAC 白名单。

---

## 四、支付与短信

### 17. 测试模式怎么模拟支付？

门户选套餐 → 进入 `/portal/pay/mock` → 点“确认支付”。也可直接调
`POST /portal/api/pay/confirm {"order_no": "..."}`。

### 18. 支付成功但套餐没开通？

1. 看订单状态是否变为“已支付”；
2. 看回调是否到达：Nginx 日志里搜 `/portal/api/pay/notify/`；
3. 常见原因：回调 URL 不是公网 HTTPS、验签失败、金额不一致、`WIFIDOG_PAY_PROVIDER` 配错；
4. 临时处理：后台订单详情手动开通/补单。

### 19. 微信支付返回的是二维码不是跳转链接？

微信 Native 下单返回 `code_url`，服务端标记 `qr=true`，前端需把 `pay_url` 渲染成二维码让用户扫码。
支付宝返回的是可直接跳转的收银台链接。

### 20. 短信收不到？

- 测试模式不会真发短信，验证码在页面/接口 `dev_code` 里；
- 正式模式检查：AccessKey/Secret 是否正确、签名与模板是否审核通过、模板变量个数是否匹配、
  手机号是否在黑名单/限流；
- 阿里云控制台可查发送记录与失败原因。

---

## 五、升级与数据

### 21. 升级会不会丢数据？

不会。升级只替换代码，`data/wifidog.db`、`.env`、`logs/` 保持不动；启动时自动补表字段。
仍建议升级前备份（见 [DEPLOYMENT.md](DEPLOYMENT.md) 第 15 节）。

### 22. 能从 SQLite 换成 MySQL 吗？

可以。改 `WIFIDOG_DB_URL` 为 `mysql+pymysql://...`，启动会自动建表；历史数据需另写脚本迁移
（按表逐条复制，金额/时间字段注意类型）。

### 23. 旧的 Node/RADIUS 版本还能用吗？

代码保留在 tag `node-v3.0.0`，但**未在生产验证**，且功能远少于当前版本。新部署请用 `main`。

### 24. 支持多少网关/多少终端？

认证服务本身很轻：2C2G 支撑数十网关、数千终端没问题。真正瓶颈是出口带宽与路由器性能。
SQLite 在高频并发写入下会成为瓶颈，规模上来后换 MySQL。

---

## 六、合规与商用

### 25. 可以用来做收费 WiFi 吗？

可以，本项目就是为此设计。但请自行确认当地对公共 WiFi 的实名/日志留存/网络安全要求
（例如日志保存期限、用户告知、ICP/公安备案），本项目只提供技术能力，不替代合规义务。

### 26. 许可证是什么？

MIT，可商用、可闭源二次开发，保留版权声明即可。见 [LICENSE](../LICENSE)。

### 27. 二次开发后能去掉版权吗？

MIT 允许修改与再发布，但需保留原始版权与许可声明。建议在 README 中注明基于本项目。
