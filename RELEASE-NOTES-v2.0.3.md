# WiFiDog Auth Server v2.0.3

发布日期：2026-11-23

本版本新增用户级换绑额度和后台用户/设备集中控制，并已同步部署到生产服务器。

## 主要更新

- 用户新增 `device_change_limit`（默认 3）和 `device_change_window_start`；旧库启动时自动迁移，存量用户默认 3 次。
- 换绑额度按用户独立计算，30 天一个周期；后台支持 `+1`、`-1`、直接设置上限（0–100）和重置已用次数。
- 门户自助换绑按用户自己的上限拦截；额度为 0 时禁止自助换绑，管理员仍可直接改绑。
- 后台新增用户控制面板：账号、套餐、到期、余额、绑定 MAC、上次绑定、在线会话、额度周期。
- 支持解绑/改绑设备并踢掉旧会话；管理员直接操作不计入用户自助换绑次数。
- 支持删除用户：输入手机号/用户名/ID 确认，有生效套餐时需强制确认。
- 删除会清理订阅、会话、短信验证码和账号，保留历史订单用于财务审计；在线会话会先收到网关断网指令。
- 本次发布不包含尚未部署的授权模块。

## 新增接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/api/users/{uid}` | 用户详情、绑定设备、在线会话与额度 |
| POST | `/admin/api/users/{uid}/device-quota` | `delta` 增减上限、`limit` 设置上限、`reset_used` 重置已用 |
| POST | `/admin/api/users/{uid}/device` | 空 MAC 解绑；带 MAC 改绑；可踢线 |
| POST | `/admin/api/users/{uid}/delete` | 账号确认 + 强制删除确认 |

## 数据库

```sql
device_change_limit INTEGER NOT NULL DEFAULT 3
device_change_window_start DATETIME
```

启动时 `init_db()` 自动补列；生产升级前仍应备份 SQLite 数据库。

## 验收

- `tests/test_user_device_control.py`：5/5 通过。
- 用户/设备测试与授权测试合并运行：14/14 通过。
- 真实 Chromium：用户列表 → 控制弹窗 → 调整额度 → 解绑设备，页面无脚本错误。
- 生产部署：`wifidog.service` active，`/healthz` 200；14 个存量用户全部默认额度 3；用户/会话/订单数量部署前后保持 14/60/16。
- 生产新接口未登录访问返回 401，后台模板已加载用户控制入口。

## 升级

1. 备份 `data/wifidog.db` 和项目目录。
2. 解压 server/full 包或应用代码变更。
3. 重启服务；启动时自动迁移两个字段。
4. 登录后台「用户管理」→「控制」验证额度、解绑和删除入口。

## 下载资产

| 资产 | 用途 |
|---|---|
| `wifidog-portal-v2.0.3-full.tar.gz` | 服务端源码 + 文档 + 数据库结构 + 验收脚本 + 路由器包 |
| `wifidog-portal-v2.0.3-server.tar.gz` | 仅服务端源码、文档、数据库结构与验收脚本 |
| `wifidog-server-2.0.3-linux-x86_64.tar.gz` | Nuitka 编译交付包，不含 `.py` 源码 |
| `wifidog-portal-v2.0.3-offline-x86_64.tar.gz` | 离线部署包，内置 Python 运行时、wheels、Redis 与安装脚本 |
| `SHA256SUMS.txt` | 全部下载资产的 SHA256 校验和 |

## 已知边界

- 删除是硬删除，不可恢复；订单保留，认证日志当前也保留。
- 管理员调整的是每 30 天上限，不是直接修改剩余次数。
- 非会员没有生效套餐时，仍按短信重新认证后的原有改绑逻辑处理，不受额度限制。
- 真实短信、支付和网关联动仍需在目标环境配置后做小额实测。
