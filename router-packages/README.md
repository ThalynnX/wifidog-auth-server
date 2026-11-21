# 路由器安装包说明

本目录是随项目发布的 **路由器端** 安装包，均来自生产环境实际使用并验证的构建产物。

## 1. 包清单

| 文件 | 大小 | 作用 | 是否必装 |
|---|---|---|---|
| `apfree-wifidog-9.11.2969-r1.apk` | ~175 KB | wifidogx 主程序（认证网关守护进程） | 必装 |
| `luci-app-apfree-wifidog-0.apk` | ~442 KB | LuCI 配置界面（服务 → apfree-wifidog） | 建议 |
| `luci-i18n-apfree-wifidog-zh-cn-0.apk` | ~4 KB | 中文语言包 | 可选 |
| `apfree-wifidog-libs-aarch64.tar.gz` | ~165 KB | 依赖库（libevent / libmosquitto / libnetfilter_queue 等） | 缺库时必装 |
| `apfree-wifidog-luci-full-aarch64.tar.gz` | ~626 KB | 以上全部 + init 脚本 + 配置模板 + 离线页，**一键安装** | 推荐 |

## 2. 适用平台

| 项 | 值 |
|---|---|
| OpenWrt target | `qualcommax` |
| subtarget | `ipq60xx` |
| 架构 | `aarch64_cortex-a53` |
| 典型机型 | 京东云亚瑟 AX1800（JDCloud RE-SS-01）及同方案机型 |
| 固件 | ImmortalWrt 23.05 / 24.10（SNAPSHOT 亦可） |

> **不适用于** `mipsel`、`mips`、`x86_64`、`armv7` 等架构。其它架构请按
> [`docs/ROUTER_SETUP.md`](../docs/ROUTER_SETUP.md) 第 12 节用 OpenWrt SDK 自行编译。

## 3. 安装（推荐：全量包）

```bash
cd /tmp
tar -xzf apfree-wifidog-luci-full-aarch64.tar.gz -C /tmp/wifidog-install
cd /tmp/wifidog-install
sh ./install.sh
```

脚本会备份原文件、复制到根目录、设置可执行权限、注册开机自启、清理 LuCI 缓存、检查依赖库。

## 4. 安装（apk 系统，ImmortalWrt 24.10+）

```bash
cd /tmp
apk add --allow-untrusted ./apfree-wifidog-9.11.2969-r1.apk
apk add --allow-untrusted ./luci-app-apfree-wifidog-0.apk
apk add --allow-untrusted ./luci-i18n-apfree-wifidog-zh-cn-0.apk
```

缺库时：

```bash
tar -xzf apfree-wifidog-libs-aarch64.tar.gz -C /
```

## 5. 安装（opkg 系统，ImmortalWrt 23.05 及更早）

opkg 不能直接安装 apk 格式，二选一：

- 用 SDK 编译 `.ipk`（见 `docs/ROUTER_SETUP.md` 第 12 节）；
- 或使用上面的全量包手动安装（最省事）。

## 6. 安装后检查

```bash
/etc/init.d/wifidogx status
cat /tmp/wifidogx.conf
logread -e wifidogx | tail -20
```

LuCI 刷新后应出现 **服务 → apfree-wifidog**。

## 7. 配置

**不要用默认配置直接上线**，默认 `enabled=0` 且认证服务器是 `wifidogx.online`。
必须改成自己的域名/IP，完整字段说明见 [`docs/ROUTER_SETUP.md`](../docs/ROUTER_SETUP.md) 第 5 节。

最小改动：

```bash
uci set wifidogx.common.enabled=1
uci set wifidogx.common.auth_server_hostname='portal.example.com'
uci set wifidogx.common.auth_server_port='443'
uci set wifidogx.common.auth_server_path='/wifidog/'
uci set wifidogx.common.long_conn_mode='wss'
uci set wifidogx.main.gateway_id='shop01'
uci set wifidogx.main.gateway_subnetv4='192.168.6.0/24'
uci commit wifidogx
/etc/init.d/wifidogx restart
```

## 8. 校验

发布时同目录会附带 `SHA256SUMS.txt`：

```bash
sha256sum -c SHA256SUMS.txt
```

## 9. 卸载

```bash
/etc/init.d/wifidogx stop
/etc/init.d/wifidogx disable
rm -f /usr/bin/wifidogx /usr/bin/wdctlx /etc/init.d/wifidogx /etc/config/wifidogx
rm -rf /usr/lib/lua/luci/model/cbi/wifidogx /usr/lib/lua/luci/view/wifidogx* \
       /www/luci-static/resources/view/wifidogx*
```

## 10. 来源与许可

- 上游项目：[liudf0716/apfree-wifidog](https://github.com/liudf0716/apfree-wifidog)（GPL-2.0）
- 本目录仅 redistributing 构建产物，版权归上游作者；二次分发请遵守 GPL-2.0。
