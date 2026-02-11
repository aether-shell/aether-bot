# Aether Bot Web（Web/PWA Channel）使用说明

本目录提供 **Web/PWA 渠道** 的实现与静态前端，用于在手机浏览器中与 nanobot/aether-bot 对话，支持 **SSE 流式回复、会话管理、附件上传、PWA 安装**。本 README 基于 `docs/web-channel-plan.md` 和当前实现编写，覆盖配置项、使用方式与 Cloudflare Tunnel 接入。

[English](README.md) | 中文

---

## 1. 快速开始

### 1.1 开启 Web Channel
Web 配置写在 **用户配置文件**：`~/.aether-bot/config.json`。

如果你还没有这个文件，先执行一次：

```bash
nanobot onboard
```

然后在 `~/.aether-bot/config.json` 中添加：

```json
{
  "channels": {
    "web": {
      "enabled": true,
      "host": "127.0.0.1",
      "port": 8080,
      "secret": "your-strong-invite-code"
    }
  },
  "agents": {
    "defaults": {
      "stream": true
    }
  }
}
```

说明：
- `secret` 是 **邀请码**，也是 JWT 签名密钥。务必设置强口令。
- `agents.defaults.stream: true` 可以开启流式输出体验（SSE `delta`）。
- `host` 推荐使用 `127.0.0.1`（配合 Cloudflare Tunnel 更安全）。

### 1.2 启动

```bash
nanobot gateway
```

启动后，Web Channel 会监听 `http://<host>:<port>`（默认 `http://127.0.0.1:8080`）。

### 1.3 访问
- 本机浏览器：`http://127.0.0.1:8080`
- 局域网手机：`http://<电脑局域网IP>:8080`
- 公网手机：配合 Cloudflare Tunnel（见下文）

---

## 2. 配置项详解（channels.web）

以下字段来自 `nanobot/config/schema.py` 的 `WebChannelConfig`：

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `enabled` | bool | `false` | 是否启用 Web Channel |
| `host` | string | `0.0.0.0` | 监听地址，建议配合隧道时设为 `127.0.0.1` |
| `port` | int | `8080` | 监听端口 |
| `secret` | string | `""` | 邀请码 + JWT 签名密钥（必须设置） |
| `token_expiry_days` | int | `30` | JWT 有效期（天） |
| `rate_limit_rpm` | int | `20` | 每用户每分钟请求上限 |
| `allow_from` | list | `[]` | 允许的 sender_id 列表（空 = 允许所有） |
| `show_context` | bool | `false` | 在 UI 顶部显示上下文/耗时信息 |
| `max_upload_mb` | int | `10` | 单次上传大小上限（MB） |

注意：
- `allow_from` 如果设置，需包含 `web_user`（Web 端默认 sender_id），否则会被拒绝。
- `secret` 为空时，**空邀请码即可登录**，存在安全风险。

---

## 3. 使用方式

### 3.1 登录
- 打开网页后输入邀请码（`secret`）登录。
- 登录成功后浏览器会保存 JWT（localStorage）。

### 3.2 流式聊天（SSE）
- 后端通过 `/api/messages/stream` 推送 SSE 事件。
- `delta` 事件为流式增量（实际是累计文本），`message` 事件为完整回复。

### 3.3 会话管理
- 历史会话存储在 `~/.aether-bot/sessions/`（JSONL）。
- UI 支持：会话列表 / 新建会话 / 切换历史会话。

### 3.4 附件上传
- UI 可上传文件，通过 `/api/upload` 获取文件路径。
- 发送消息时携带 `media` 字段，bot 可读取上传文件。
- 上传文件目录：`~/.aether-bot/web_uploads/`（24 小时自动清理）。

### 3.5 PWA 安装
- Android Chrome：菜单 → “添加到主屏幕”
- iOS Safari：分享 → “添加到主屏幕”

---

## 4. Cloudflare Tunnel 接入

适合 **无公网 IP** 的本地机器，手机通过公网访问。

### 4.1 临时域名（快速测试）

```bash
brew install cloudflared

cloudflared tunnel --url http://localhost:8080
```

终端会输出形如：
```
https://xxx-yyy-zzz.trycloudflare.com
```
用该地址在手机浏览器打开即可。

### 4.2 固定域名（长期使用）

要求域名已托管到 Cloudflare。

```bash
# 1. 登录授权
cloudflared tunnel login

# 2. 创建隧道
cloudflared tunnel create nanobot

# 3. 绑定子域名
cloudflared tunnel route dns nanobot chat.yourdomain.com

# 4. 写配置文件
cat > ~/.cloudflared/config.yml << 'EOT'
tunnel: <tunnel-id>
credentials-file: ~/.cloudflared/<tunnel-id>.json
ingress:
  - hostname: chat.yourdomain.com
    service: http://localhost:8080
    originRequest:
      connectTimeout: 30s
      keepAliveTimeout: 90s
  - service: http_status:404
EOT

# 5. 启动
cloudflared tunnel run nanobot
```

### 4.3 可选：Cloudflare Access 零信任
在 Cloudflare Dashboard → Zero Trust → Access
为 `chat.yourdomain.com` 添加访问策略（邮箱验证码、Google 登录等），
在邀请码之前加一道零信任门。

---

## 5. API 路由速览

- `POST /api/auth/login`：邀请码换 JWT
- `GET  /api/auth/check`：检查 JWT
- `POST /api/messages`：发送消息
- `GET  /api/messages/stream`：SSE 推送（token query 参数）
- `GET  /api/sessions`：会话列表
- `POST /api/sessions/new`：新建会话
- `POST /api/sessions/switch`：切换会话
- `GET  /api/sessions/{session_id}/messages`：拉取历史消息
- `POST /api/upload`：上传附件
- `GET  /api/media/{file_id}`：读取附件（bot 回复时的媒体）

---

## 6. UI 品牌定制（可选）

Web 前端支持读取 `~/.aether-bot/brand.json` 覆盖文案与主题。

### 6.1 brand.json 示例

```json
{
  "productName": "Aether Shell",
  "shortName": "Aether",
  "tagline": "Self-driven safe upgrade framework",
  "assistantName": "Aether",
  "loginButtonLabel": "Login",
  "loginSubtitle": "Enter invite code",
  "themeColor": "#0ea5e9",
  "backgroundColor": "#0b1220",
  "faviconUrl": "/icons/icon.svg",
  "appleTouchIconUrl": "/icons/icon-192.png"
}
```

### 6.2 自定义资源
- 资源目录：`~/.aether-bot/brand-assets/`
- 访问路径：`/brand-assets/<filename>`
- 可在 `brand.json` 中引用自定义图片 URL

---

## 7. 常见问题

**Q1: 为什么登录失败？**
- 确保 `secret` 设置正确。
- 若设置了 `allow_from`，必须包含 `web_user`。

**Q2: 手机访问不了？**
- 局域网：检查 `host` 是否是 `0.0.0.0` 或绑定正确 IP。
- 公网：请使用 Cloudflare Tunnel，确保 tunnel 正常运行。

**Q3: 流式回复不生效？**
- 设置 `agents.defaults.stream: true`。

---

## 8. 安全建议（强烈推荐）

- 使用强邀请码（`secret`）
- 使用 Cloudflare Access 或 Tailscale 增加额外认证层
- 仅在隧道/局域网环境绑定 `host=127.0.0.1`
- 保持 `rate_limit_rpm` 合理限制

---

如果你需要进一步扩展（OAuth 登录、WebSocket、企业内网部署等），可以在此基础上继续演进。
