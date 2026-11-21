# 路由器安装与配置指南

本文覆盖：**适配哪些路由器 → 选哪种网络模式 → 怎么刷机 → 怎么装 wifidogx → LuCI 每一项怎么填 → 怎么验证 → 出问题怎么查**。
按顺序照做即可，不需要额外查其它资料。

---

## 0. 动手前的三个确认

| # | 确认项 | 怎么确认 |
|---|---|---|
| 1 | 服务端已部署且路由器能访问 | 在路由器 SSH 里执行 `wget -qO- http://你的域名/wifidog/ping?gw_id=test`，应返回 `Pong` |
| 2 | 路由器芯片架构 | SSH 执行 `uname -m`、`cat /etc/openwrt_release`，对照第 1 节表格 |
| 3 | 选哪种网络模式 | 见第 2 节。**90% 的场景选「主路由模式」**，最稳 |

> 服务器地址、端口、路径三件套必须完全一致：
> `portal.example.com` + `443`（或 80）+ `/wifidog/`（**结尾斜杠不能少**）。

---

## 1. 支持的固件与机型

### 1.1 本项目直接提供安装包的平台

| 项目 | 值 |
|---|---|
| 芯片平台 | Qualcomm `ipq60xx`（OpenWrt target `qualcommax`，subtarget `ipq60xx`） |
| CPU 架构 | `aarch64_cortex-a53` |
| 典型机型 | **京东云亚瑟 AX1800（JDCloud RE-SS-01）**、同方案 AX5/AX6 改机 |
| 固件 | ImmortalWrt 23.05 / 24.10（SNAPSHOT 亦可） |
| 安装包 | `router-packages/` 下的 apk、libs、LuCI 全量包 |
| 验证状态 | ✅ 已在真机跑通（生产环境同款） |

包清单：

| 文件 | 作用 | 是否必装 |
|---|---|---|
| `apfree-wifidog-9.11.2969-r1.apk` | wifidogx 主程序 | 必装 |
| `luci-app-apfree-wifidog-0.apk` | LuCI 配置界面 | 建议 |
| `luci-i18n-apfree-wifidog-zh-cn-0.apk` | 中文语言包 | 可选 |
| `apfree-wifidog-libs-aarch64.tar.gz` | 依赖库（libevent/libmosquitto/libnetfilter_queue 等） | 缺库时必装 |
| `apfree-wifidog-luci-full-aarch64.tar.gz` | 上述全部 + init.d + 配置模板 + 离线页，**一键手动安装** | 推荐 |

### 1.2 需要自行编译的常见平台

| 平台 | 架构 | 典型机型 | 建议 |
|---|---|---|---|
| `ipq807x` | `aarch64_cortex-a53` | 小米 AX9000、红米 AX6000 | 用 OpenWrt SDK 编译 |
| `mediatek/filogic` | `aarch64_cortex-a53` | MT7986（红米 AX6000、GL-MT6000） | 用 SDK 编译 |
| `ramips/mt7621` | `mipsel_24kc` | 新路由 3、K2P、小米 4A 千兆 | 用 SDK 编译（性能一般） |
| `ath79` | `mips_24kc` | 老款 TP-Link / 网件 | 建议先升级固件 |
| `x86/64` | `x86_64` | N100/J4125 软路由 | 用 SDK 编译，**性能最佳** |

编译方法见第 12 节。若不想编译，也可以直接安装 OpenWrt 官方源里的 `wifidog`（走标准协议，功能较少，见第 13 节）。

### 1.3 固件要求

- 内核 ≥ 4.14，推荐 5.4/6.6
- 必须有 `kmod-nfnetfilter` / `libnetfilter_queue` 支持（apfree 靠它做流量拦截）
- 剩余 flash ≥ 4MB（wifidogx + LuCI 约 1.5MB）
- 剩余内存 ≥ 30MB
- 系统时间必须准确（`ntpd` 或 `sysntpd` 正常），否则 TLS 与 token 校验会失败

---

## 2. 网络模式怎么选

### 模式 A：主路由 / NAT 模式（✅ 强烈推荐）

```
光猫（桥接）──▶ 本路由器（PPPoE 拨号 / DHCP 上网）
                   │
                   ├── LAN/WiFi ──▶ 手机、电脑（被认证）
                   └── 出口 wan/pppoe-wan
```

- 本机同时是**网关 + DHCP + DNS + 认证网关**，所有终端流量必然经过 `br-lan`，拦截 100% 生效。
- 适合：单店、单层、宿舍一层、独立宽带。
- 设置要点：
  1. WAN 正常拨号/获取 IP，能上网；
  2. LAN 网段自定义（如 `192.168.6.1/24`），开启 DHCP；
  3. `Gateway Name` 选 `br-lan`，`Gateway Channel` 填无线接口（如 `wlan0` / `phy1-ap0`）；
  4. `Gateway Subnetv4` 填 `192.168.6.0/24`（必须与 LAN 网段一致）。

### 模式 B：二级路由 / 旁路 DHCP 模式（✅ 推荐，需改造）

```
上游主路由（负责外网）──▶ 本路由器 LAN 口
                              │
                              └── WiFi/LAN ──▶ 终端（DHCP 必须由本机下发）
```

- 本机 **WAN 口不用**（或删除 WAN 接口），LAN 口接上游 LAN。
- **关键：终端默认网关必须是本机**。两种做法：
  - 做法 1（推荐）：本机开启 DHCP，上游关闭 DHCP（同一二层网络只能有一个 DHCP）；
  - 做法 2：上游 DHCP 下发时把 `option router` 指向本机 LAN IP。
- 本机 LAN IP 不能与上游同网段冲突（上游 `192.168.1.1`，本机用 `192.168.6.1`）。
- 适合：已有主路由不想动、房东/二房东场景。
- ⚠️ 若终端仍从上游拿网关，流量不经过本机，**认证不会生效**，表现为“连上就能上网”。

### 模式 C：纯 AP / 桥接模式（❌ 不推荐）

- 本机只做无线 AP，DHCP 与网关都在上游。
- wifidog 无法劫持流量（它只能拦截经过本机 forward 链的流量），**认证不生效**。
- 如果非要用：必须在上游把终端网关指向本机，等价于模式 B。
- 大覆盖场景的正确做法：**主路由跑认证（模式 A）+ 其它机器刷成纯 AP 只扩无线**，认证仍在主路由完成。

### 模式 D：多 SSID / 多网关（✅ 支持）

```
一台路由器
├── SSID: Shop-WiFi  → gateway 段 shop01 → 售卖顾客套餐
├── SSID: Staff-WiFi → gateway 段 staff01 → MAC 白名单免认证
└── LAN 有线        → wired_passed=1 或 IP 白名单
```

- 在 LuCI「Gateway Settings」里点“添加”，每个 SSID 建一个 `gateway` 段。
- 每个段有独立 `gateway_id`，服务端后台分别登记、分别看统计。
- 不同段的套餐可以用同一个服务端（用户套餐全局共享），也可以部署多套服务端。

### 模式 E：Bypass（旁路认证）

- apfree-wifidogx 自带 `Bypass Auth` 模式，用于“认证服务器不在本机链路上”的旁路部署。
- 本项目的计费依赖网关上报 counters 并下发断网指令，**Bypass 模式不适合做付费计费**，仅适合做访问审计/放行统计。
- 如确需旁路，请让旁路设备以网桥方式串在出口（等价模式 A/B）。

---

## 3. 刷机与基础网络（以 ImmortalWrt 为例）

1. 下载对应机型的 ImmortalWrt 固件（`qualcommax/ipq60xx` 选 factory/sysupgrade 镜像）。
2. 刷入后电脑网线接 LAN，浏览器打开 `http://192.168.1.1`。
3. 设置向导：
   - WAN：PPPoE（宽带账号密码）或 DHCP（光猫拨号）；
   - LAN：`192.168.6.1/24`，开启 DHCP，地址池 `192.168.6.100-200`；
   - WiFi：设置 SSID 与密码，**先不要开“访客网络/隔离”**（排查阶段减少变量）。
4. 确认能上网：`ping -c3 223.5.5.5`、`nslookup baidu.com`。
5. 设置时区与 NTP：系统 → 系统属性 → 时区 `Asia/Shanghai`，启用 NTP 客户端。
6. 改路由器管理密码，关闭 WAN 侧访问（防火墙 → 常规设置 → 丢弃 WAN 入站）。

> 建议先备份一次配置：系统 → 备份/升级 → 生成备份。

---

## 4. 安装 wifidogx

### 4.1 下载安装包

从本仓库 Release 页下载（或直接用仓库内 `router-packages/`）：

```bash
# 在路由器上（或电脑下载后用 scp 传上去）
cd /tmp
wget https://github.com/your-account/wifidog-auth-server/releases/download/v1.1.2/apfree-wifidog-luci-full-aarch64.tar.gz
```

### 4.2 方式一：全量包手动安装（最稳，推荐）

优点：不依赖 apk/opkg 源，不怕依赖缺失。

```bash
cd /tmp
tar -xzf apfree-wifidog-luci-full-aarch64.tar.gz -C /tmp/wifidog-install
cd /tmp/wifidog-install
sh ./install.sh
```

脚本会做：

1. 备份原 `usr/bin/wifidogx`、`etc/config/wifidogx`、`etc/init.d/wifidogx` 到 `/tmp/wifidog-backup-时间戳`；
2. 复制 `usr/`、`etc/`、`www/` 到根目录；
3. `chmod 755` 并 `/etc/init.d/wifidogx enable`；
4. 清理 LuCI 缓存、重启 rpcd；
5. 检查依赖库是否齐全。

装完刷新 LuCI，左侧应出现 **服务 → apfree-wifidog**。

### 4.3 方式二：apk 安装（ImmortalWrt 24.10+，apk 包管理）

```bash
cd /tmp
apk add --allow-untrusted ./apfree-wifidog-9.11.2969-r1.apk
apk add --allow-untrusted ./luci-app-apfree-wifidog-0.apk
apk add --allow-untrusted ./luci-i18n-apfree-wifidog-zh-cn-0.apk
```

若提示缺库，再装 libs 包：

```bash
tar -xzf apfree-wifidog-libs-aarch64.tar.gz -C /
```

刷新 LuCI 页面（强刷 Ctrl+F5）。

### 4.4 方式三：opkg 安装（ImmortalWrt 23.05 及更早）

该固件使用 opkg，需使用 `.ipk` 格式的包（apk 不能直接给 opkg 用）。两条路：

- 用 SDK 编译出 ipk（见第 12 节）；
- 或直接走 4.2 的全量包手动安装（**最省事**）。

### 4.5 依赖库检查

```bash
for lib in libz.so.1 libjson-c.so.5 libssl.so.3 libcrypto.so.3 \
           libevent-2.1.so.7 libevent_openssl-2.1.so.7 \
           libnetfilter_queue.so.1 libmosquitto.so.1 libuci.so libnftnl.so.11 libmnl.so.0; do
  [ -e /usr/lib/$lib ] || [ -e /lib/$lib ] || echo "缺少: $lib"
done
```

- 有 `缺少:` 输出 → 装 `apfree-wifidog-libs-aarch64.tar.gz`；
- 仍缺 → 用 `opkg whatprovides` / `apk search` 找对应库，或换全量包版本。

### 4.6 启停与卸载

```bash
/etc/init.d/wifidogx start
/etc/init.d/wifidogx status
/etc/init.d/wifidogx stop
/etc/init.d/wifidogx disable     # 取消开机自启

# 卸载（手动安装的包）
rm -f /usr/bin/wifidogx /usr/bin/wdctlx /etc/init.d/wifidogx /etc/config/wifidogx
rm -rf /usr/lib/lua/luci/model/cbi/wifidogx /usr/lib/lua/luci/view/wifidogx* \
       /www/luci-static/resources/view/wifidogx*
```

---

## 5. LuCI 配置逐项说明

进入 **服务 → apfree-wifidog**，共 5 个标签页。

### 5.1 Basic Settings（基础设置）

| 字段 | 填什么 | 说明 |
|---|---|---|
| Enable | ✅ 勾选 | 不勾选服务不会启动（默认 `enabled=0`） |
| Auth Server Mode | **Cloud Auth** | 使用远程认证服务器；`Local Auth` 是本地门户模式，不适合本项目计费 |
| External Interface | `wan` 或 `pppoe-wan` | 出口接口，用于放行已认证用户的 NAT；旁路模式可不选 |
| Device ID | 同 `gateway_id` 或自定义 | 上报给服务端的设备标识，建议与网关 ID 一致 |
| Auth Server Hostname | `portal.example.com` | 你的服务端域名或公网 IP（**不要带 http://**） |
| Auth Server Port | `443` | HTTPS 用 443；HTTP 用 80 |
| Auth Server URI path | `/wifidog/` | **结尾必须有斜杠** |
| Local Portal | 留空 | 仅 Local Auth 模式使用 |
| Log Level | `Info(6)` 调试期用 `Debug(7)` | 排障时开 Debug，稳定后改回 Info |

### 5.2 Gateway Settings（网关设置）

点“添加”建一个网关段，每行含义：

| 字段 | 填什么 | 说明 |
|---|---|---|
| Auth Enabled | ✅ | 该段是否启用认证 |
| Gateway Name | `br-lan` | 给终端发 IP 的**桥接口**（下拉里只列 bridge 类型设备） |
| Gateway Channel | `wlan0`（或 `phy1-ap0`、`wlan1`） | 无线接口名，`iwinfo` 或 `ls /sys/class/ieee80211/*/device/net` 可查 |
| Gateway ID | `shop01` | 全局唯一，`^[A-Za-z0-9_.-]{1,64}$`；**留空则自动取 br-lan 的 MAC（去冒号大写）**，不推荐 |
| Gateway Subnetv4 | `192.168.6.0/24` | **必填**，必须与 LAN 网段一致；填错会导致认证/放行异常 |

> 多 SSID：每个 SSID 建一个段，`Gateway Channel` 填各自无线接口，`Gateway ID` 各不相同。

### 5.3 Advanced Settings（高级设置）

| 字段 | 推荐值 | 说明 |
|---|---|---|
| Persistent Connection Mode | `WebSocket (ws)`；有 HTTPS 用 `wss` | 与本项目 `/ws/wifidogx` 长连接对应 |
| WebSocket Hostname | 留空（自动用认证服务器域名） | 也可显式填 `portal.example.com` |
| WebSocket Port | 留空（自动用认证端口） | 显式填 `443`/`80` |
| WebSocket URI path | `/ws/wifidogx` | **必须与服务端路由一致** |
| MQTT * | 不用 | 本项目未提供 MQTT 服务 |
| Enable Wildcard Domain | ✅ | 支持泛域名放行（微信/支付宝等） |
| Check Interval | `60` | 每 60 秒 ping 一次服务端 |
| Client Timeout | `5` | 单次请求超时（秒） |
| Wired Passed | 视场景 | 勾选后**有线口设备免认证**；做“有线也要认证”时不要勾 |
| Apple CNA | 勾选 | 让 iPhone/iPad 自动弹出认证页（Captive Network Assistant） |
| JS Filter | ✅ | JS 重定向，兼容更多手机浏览器 |
| Enable Anti NAT | 按需 | 防私接路由器/NAT 共享；开启后可配 TTL 与放行 MAC |
| TTL Value | `64,128` | Anti NAT 用，默认即可 |

### 5.4 Rule Settings（放行规则）

| 字段 | 用途 |
|---|---|
| Trusted Domains | 认证前就允许访问的域名（如 `www.baidu.com`），用于门户素材/客服页 |
| Trusted Wildcard Domains | 泛域名放行（`.weixin.qq.com`、`.alipay.com`），保证微信/支付宝能打开 |
| Trusted MACs | 免认证 MAC（收银机、打印机、摄像头） |
| App White List / MAC White List / Wildcard White List | 引用下方“Group Define”里的分组，批量管理 |

> **Group Define** 是分组表：`g_type` 1=域名、2=MAC、3=泛域名，可把一批域名/MAC 归组后整体引用。
> 默认配置已内置微信、钉钉、支付宝等常用放行分组，一般不用改。

### 5.5 QoS Settings（限速）

| 字段 | 说明 |
|---|---|
| Enable Global QoS | 全局限速开关 |
| Global QoS Up / Down | 全局上下行（Mbps），0=不限 |

> 套餐级的限速在**服务端后台 → 套餐管理 → 上行/下行限速（kbps）**配置，网关 QoS 只做整机兜底。

---

## 6. 配置文件参考

LuCI 保存后写入 `/etc/config/wifidogx`，运行时由 init 脚本生成 `/tmp/wifidogx.conf` 传给 wifidogx。
下面是**可直接改用的最小示例**：

```uci
config wifidogx 'common'
	option enabled '1'
	option auth_server_mode 'cloud'
	option external_interface 'wan'
	option device_id 'shop01'
	option auth_server_hostname 'portal.example.com'
	option auth_server_port '443'
	option auth_server_path '/wifidog/'
	option check_interval '60'
	option client_timeout '5'
	option wired_passed '0'
	option apple_cna '1'
	option js_filter '1'
	option enable_dns_forward '1'
	option enable_anti_nat '0'
	option log_level '6'
	option long_conn_mode 'wss'

config gateway 'main'
	option gateway_auth_enabled '1'
	option gateway_name 'br-lan'
	option gateway_channel 'wlan0'
	option gateway_id 'shop01'
	option gateway_subnetv4 '192.168.6.0/24'
```

应用：

```bash
uci commit wifidogx
/etc/init.d/wifidogx restart
cat /tmp/wifidogx.conf          # 看生成结果是否正确
```

生成的 `/tmp/wifidogx.conf` 大致如下（可用来核对）：

```conf
DeviceID shop01
AuthServer {
    Hostname portal.example.com
    HTTPPort 443
    Path /wifidog/
}
WebSocket {
    WSServer portal.example.com
    WSServerPort 443
    WSServerPath /ws/wifidogx
    WSServerSSL 1
}
GatewaySetting {
    GatewayInterface br-lan
    GatewayChannel wlan0
    GatewayID shop01
}
ExternalInterface pppoe-wan
CheckInterval 60
ClientTimeout 5
JsFilter 1
WiredPassed 0
BypassAppleCNA 1
EnableDNSForward 1
```

---

## 7. 服务端要配合做的事

1. 后台 → 设备管理 → 新增网关：
   - 网关 ID：与路由器 `Gateway ID` **完全一致**（区分大小写）；
   - 名称/位置：便于识别（如“一号店-前台”）；
   - 共享密钥：可留空；填了则要求网关 `gwstats` 带 `x-gw-key`。
2. **运行模式**：
   - 首次联调保持 `test`（后台系统设置里可见），此时 `ping` 会自动登记新网关，方便快速跑通；
   - 切 `production` 后，**未登记的网关不会入库**，必须先登记。
3. 检查服务端时间与路由器时间误差 < 5 分钟（否则 token/TLS 异常）。
4. Nginx 必须放行 `/wifidog/` 与 `/ws/wifidogx`（WebSocket 需 `Upgrade` 头与长超时），见 `deploy/nginx_wifidog.conf`。

---

## 8. 安装后的验证清单

按顺序做，哪一步失败就停在哪查：

```bash
# ① 进程在跑
ps | grep wifidogx
/etc/init.d/wifidogx status

# ② 配置生成正确
cat /tmp/wifidogx.conf

# ③ 能解析并访问服务端
nslookup portal.example.com
wget -qO- "http://portal.example.com/wifidog/ping?gw_id=shop01&sys_uptime=100&sys_memfree=1000&sys_load=0.1&wifidog_uptime=100"
# 期望：Pong

# ④ 看实时日志
logread -f | grep -i -E "wifidog|auth"
```

手机端验证：

1. 连接该 SSID，等待 5~30 秒应自动弹出认证页（未弹出就手动打开浏览器访问任意 http 站点）。
2. 输入手机号 → 获取验证码（test 模式验证码会显示在页面/接口返回里）。
3. 登录 → 选择套餐 → 支付（test 模式走模拟支付）。
4. 支付成功 → 点“立即上网” → 应能打开网页。
5. 服务端后台：
   - 设备管理：该网关显示**在线**，最后上报时间刷新；
   - 在线会话：能看到该手机/设备，已用流量随上网增长；
   - 认证日志：出现 `login / counters` 记录。

命令行模拟一次网关认证（排查服务端问题时很有用）：

```bash
# 先拿到 token（把手机号换成测试号）
curl -s -X POST https://portal.example.com/portal/api/login \
  -H 'Content-Type: application/json' \
  -d '{"phone":"<your-phone>","code":"123456","mac":"aa:bb:cc:dd:ee:01","gw_id":"shop01","ip":"192.168.6.100"}'
# 再从返回里取 token
curl -s "https://portal.example.com/wifidog/auth?stage=login&gw_id=shop01&ip=192.168.6.100&mac=aa:bb:cc:dd:ee:01&token=<TOKEN>&incoming=0&outgoing=0"
# 期望：Auth: 1
```

---

## 9. 特殊场景配置

### 9.1 多 SSID / 多网关

1. 网络 → 无线：为每个 SSID 建独立 AP（可绑不同 network）。
2. LuCI → wifidogx → Gateway Settings：每个 SSID 一段，`Gateway Channel` 填对应无线接口。
3. 服务端登记多个 `gw_id`，分别设套餐策略（套餐是全局的，如需隔离可部署多套服务端）。

### 9.2 有线免认证（办公网/收银网）

两种做法，二选一：

- **网关侧**：Advanced → `Wired Passed` 勾选 → 所有有线口免认证；
- **服务端侧**：后台 → 免认证 IP 白名单，填网段（如 `192.168.6.10-192.168.6.20` 或 `192.168.6.0/24` 的可用范围），更精细。

> 注意：`Wired Passed=1` 时，**接在该路由器有线口的私接路由器下所有设备**也免认证，慎用。

### 9.3 微信 / 支付宝 / 苹果弹窗

- Rule Settings 里确保 `.weixin.qq.com`、`.alipay.com`、`.alipayobjects.com` 等泛域名分组已启用（默认已配）；
- Advanced 里 `Apple CNA` 勾选，iPhone 才会自动弹门户；
- 门户必须能直连（不要在认证前要求 HTTPS 跳转失败），建议域名证书配置正确。

### 9.4 防私接 / 防 NAT 共享

- Advanced → `Enable Anti NAT` 勾选，`TTL Value` 保持 `64,128`；
- 把允许的二级路由 MAC 加到 `Anti NAT Permit MAC`；
- 服务端侧配合“同时在线设备数”限制与 MAC 黑名单。

### 9.5 门店限速

- 整机兜底：QoS 页 `Enable Global QoS`，填上下行 Mbps；
- 按套餐：服务端套餐里填 `speed_limit_up/down`（kbps），网关按服务端下发执行。

---

## 10. 防火墙与 DNS 要点

wifidogx 会自行插入 nftables/iptables 规则做重定向与放行，通常**不需要手工加规则**，但要保证：

1. `br-lan` 与无线接口在同一个 bridge 中；
2. LAN 区域允许 `input/forward`；
3. 若开了“访客网络隔离”，被隔离的客户端仍需能访问 `br-lan` 上的网关 IP（否则门户打不开）；
4. DNS 必须由路由器转发（`EnableDNSForward=1`），否则域名劫持失效、门户域名解析不到；
5. 不要安装会与 wifidogx 抢 nftables 规则的插件（如某些“上网行为管理/广告拦截”插件），冲突时先禁用排查。

查看 wifidogx 插入的规则：

```bash
nft list ruleset | grep -i -E "wifidog|nfqueue" 
iptables -t mangle -S | grep -i wifidog
```

---

## 11. 常见故障排查

| 现象 | 可能原因 | 处理 |
|---|---|---|
| LuCI 没有“apfree-wifidog”菜单 | 包没装全 / LuCI 缓存 | 重装 `luci-app` 包；`rm -rf /tmp/luci-*`；`/etc/init.d/rpcd restart`；强刷浏览器 |
| `wifidogx` 起不来 | 依赖库缺失 / 配置错误 | `logread -e wifidogx`；跑 4.5 检查库；`cat /tmp/wifidogx.conf` |
| 服务端看到 ping，但终端不弹门户 | DNS 转发关闭 / JS Filter 关闭 / HTTPS 证书错误 | 开 `EnableDNSForward`、`JS Filter`；用 http 测试；检查证书链 |
| 连上 WiFi 直接能上网（未认证） | 选错模式（纯 AP/旁路）/ `Wired Passed=1` / 上游 DHCP 下发网关不是本机 | 改模式 A/B；关 `Wired Passed`；修正 DHCP 网关 |
| 门户打开但登录后仍不能上网 | 服务端没返回 `Auth: 1` / token 过期 / 时间不同步 | 手工 curl `auth` 看返回；同步 NTP；看服务端 `auth_logs` |
| 一直重复弹认证页 | `Auth: 0`（无套餐/被拉黑/流量耗尽） | 后台看该用户套餐与黑名单；看认证日志原因 |
| 后台网关显示离线 | `check_interval` 太大 / 服务端不可达 / `gw_id` 不一致 | 调 `60`；`wget ping`；核对 ID 大小写 |
| 微信/支付宝打不开 | 泛域名放行分组未启用 | Rule Settings 启用对应分组；或手工加 `.weixin.qq.com` 等 |
| 认证后网速很慢 | QoS 限速设太小 / 网关性能不足 | 调 QoS；换 x86 软路由 |
| 多 SSID 只有一个生效 | `Gateway Channel` 填错 / 只建了一个段 | `iwinfo` 查接口名；每 SSID 建段 |
| 升级固件后配置丢失 | 未保留配置刷机 | 刷机时勾选“保留配置”；提前备份 `/etc/config/wifidogx` |
| `apk add` 报格式错误 | 固件是 opkg 系统（23.05 及更早） | 改用 4.2 全量包手动安装 |

抓包定位（可选）：

```bash
tcpdump -i br-lan -n port 80 or port 443 -c 20
```

---

## 12. 其它架构自行编译（OpenWrt SDK）

### 12.1 准备 SDK

```bash
# 以 aarch64_cortex-a53 (qualcommax/ipq60xx) 为例
wget https://downloads.openwrt.org/releases/23.05.5/targets/qualcommax/ipq60xx/openwrt-sdk-23.05.5-qualcommax-ipq60xx_gcc-12.3.0_musl.Linux-x86_64.tar.xz
tar -xf openwrt-sdk-*.tar.xz
cd openwrt-sdk-*
```

> 其它平台：到 OpenWrt 下载页选对应 target/subtarget 的 SDK；版本必须与路由器固件**大版本一致**（23.05 对 23.05、24.10 对 24.10）。

### 12.2 加入 apfree-wifidog 源码

```bash
# 方式一：官方仓库
git clone https://github.com/liudf0716/apfree-wifidog.git package/apfree-wifidog

# 方式二：使用本仓库已验证的 9.11.2969 版本
git clone https://github.com/liudf0716/apfree-wifidog.git package/apfree-wifidog
cd package/apfree-wifidog && git checkout <对应 tag/commit>
```

LuCI 插件与中文包在 apfree 的 feed 或 OpenWrt packages 中：

```bash
./scripts/feeds update -a
./scripts/feeds install -a
```

### 12.3 配置与编译

```bash
make menuconfig
# Network → Captive Portal → <*> apfree-wifidog
# LuCI → Applications → <*> luci-app-apfree-wifidog
# LuCI → Translations → Chinese → <*> luci-i18n-apfree-wifidog-zh-cn
make -j$(nproc) V=s
```

产物在 `bin/packages/<arch>/base/` 下：

- OpenWrt 23.05 及更早：`.ipk`
- ImmortalWrt 24.10 / OpenWrt main：`.apk`

### 12.4 安装到路由器

```bash
scp bin/packages/*/base/apfree-wifidog*.ipk root@192.168.6.1:/tmp/
ssh root@192.168.6.1
opkg install /tmp/apfree-wifidog*.ipk        # ipk 系统
apk add --allow-untrusted /tmp/*.apk        # apk 系统
```

---

## 13. 使用 OpenWrt 官方 `wifidog`（不推荐但可用）

适合不想编译、路由器架构又不在支持列表的情况。

```bash
opkg update
opkg install wifidog luci-app-wifidog
```

配置 `/etc/wifidog.conf`（见 README 第 8.3 节），注意：

- 官方 wifidog **不支持 HTTPS**，需服务端在 80 端口暴露 `/wifidog/`（可只对该路径放行，其余 301 到 HTTPS）；
- 不支持 `counters_v2` 批量上报，流量统计精度较低；
- 不支持 WebSocket 长连接、远程指令、Anti NAT。

对功能要求不高的小规模场景可以接受；要做付费计费建议还是用 apfree-wifidogx。

---

## 14. 运维建议

- 固件与插件版本记录在案（`cat /etc/openwrt_release`、`apk info apfree-wifidog`）；
- 每月检查一次 `logread` 中的认证失败与异常 MAC；
- 门店路由器配置备份一份到本地（`/etc/config/wifidogx`、`/etc/config/network`、`/etc/config/wireless`）；
- 远程升级前先在测试机验证，避免门店断网；
- 路由器管理口不要暴露公网，远程运维走 VPN/内网穿透。
