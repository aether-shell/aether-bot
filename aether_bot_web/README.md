# Aether Bot Web (Web/PWA Channel)

This directory contains the **Web/PWA channel** implementation and static frontend.
It lets you chat with nanobot/aether-bot in a mobile browser with **SSE streaming, session management, file upload, and PWA install**.
This README is based on `docs/web-channel-plan.md` and the current implementation.

English (default) | [中文](README.zh-CN.md)

---

## 1) Quick Start

### 1.1 Configure Web Channel
Web settings live in the **user config file**: `~/.aether-bot/config.json`.
If you don't have one yet, run:

```bash
nanobot onboard
```

Then add:

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

Notes:
- `secret` is the **invite code** and the JWT signing key. Use a strong value.
- `agents.defaults.stream: true` enables streaming responses (SSE `delta`).
- For tunnel setups, prefer `host: 127.0.0.1` to avoid LAN exposure.

### 1.2 Start

```bash
nanobot gateway
```

The Web channel will listen on `http://<host>:<port>` (default `http://127.0.0.1:8080`).

### 1.3 Access
- Local browser: `http://127.0.0.1:8080`
- LAN phone: `http://<LAN-IP>:8080`
- Public phone: use Cloudflare Tunnel (see below)

---

## 2) Configuration Reference (channels.web)

Defined in `nanobot/config/schema.py` as `WebChannelConfig`:

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Enable Web channel |
| `host` | string | `0.0.0.0` | Bind address (use `127.0.0.1` with tunnels) |
| `port` | int | `8080` | Listen port |
| `secret` | string | `""` | Invite code + JWT signing key (required) |
| `token_expiry_days` | int | `30` | JWT expiration in days |
| `rate_limit_rpm` | int | `20` | Requests per minute per user |
| `allow_from` | list | `[]` | Allowed sender IDs (empty = allow all) |
| `show_context` | bool | `false` | Show context/latency info in UI |
| `max_upload_mb` | int | `10` | Max upload size (MB) |

Important:
- If `allow_from` is set, it must include `web_user` (the default Web sender ID), or requests will be blocked.
- If `secret` is empty, an **empty invite code** can log in (not secure).

---

## 3) How It Works

### 3.1 Login
- Enter the invite code (`secret`) on the login screen.
- A JWT is stored in localStorage.

### 3.2 Streaming (SSE)
- Server pushes events via `/api/messages/stream`.
- `delta` = streaming updates; `message` = final response.

### 3.3 Sessions
- Sessions are stored at `~/.aether-bot/sessions/` (JSONL).
- UI supports list / new / switch / load history.

### 3.4 File Upload
- Upload via `/api/upload` and send message with `media` paths.
- Uploads are stored at `~/.aether-bot/web_uploads/` and cleaned after 24 hours.

### 3.5 PWA Install
- Android Chrome: menu -> "Add to Home screen"
- iOS Safari: share -> "Add to Home screen"

---

## 4) Cloudflare Tunnel

For machines without a public IP, use Cloudflare Tunnel to expose the local web server.

### 4.1 Temporary URL (quick test)

```bash
brew install cloudflared

cloudflared tunnel --url http://localhost:8080
```

You will get a URL like:
```
https://xxx-yyy-zzz.trycloudflare.com
```

### 4.2 Custom Domain (long-term)

Your domain must be hosted on Cloudflare.

```bash
# 1) Login
cloudflared tunnel login

# 2) Create a named tunnel
cloudflared tunnel create nanobot

# 3) Map a subdomain
cloudflared tunnel route dns nanobot chat.yourdomain.com

# 4) Write config
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

# 5) Run
cloudflared tunnel run nanobot
```

### 4.3 Optional: Cloudflare Access (Zero Trust)
In the Cloudflare Dashboard -> Zero Trust -> Access, add an access policy
(email OTP, Google login, etc.) to gate `chat.yourdomain.com` **before** the invite code.

---

## 5) API Endpoints

- `POST /api/auth/login` - exchange invite code for JWT
- `GET  /api/auth/check` - verify JWT
- `POST /api/messages` - send message
- `GET  /api/messages/stream` - SSE stream (token in query)
- `GET  /api/sessions` - list sessions
- `POST /api/sessions/new` - create new session
- `POST /api/sessions/switch` - switch active session
- `GET  /api/sessions/{session_id}/messages` - load history
- `POST /api/upload` - upload attachments
- `GET  /api/media/{file_id}` - fetch bot-sent media

---

## 6) Branding (Optional)

The web UI can be customized via `~/.aether-bot/brand.json`.

### 6.1 brand.json Example

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

### 6.2 Custom Assets
- Asset directory: `~/.aether-bot/brand-assets/`
- Served as: `/brand-assets/<filename>`
- Reference these URLs in `brand.json`

---

## 7) FAQ

**Q1: Login fails**
- Check `secret` in `~/.aether-bot/config.json`.
- If `allow_from` is set, include `web_user`.

**Q2: Phone cannot access**
- LAN: bind `host` to `0.0.0.0` or your LAN IP.
- Public: use Cloudflare Tunnel and keep it running.

**Q3: No streaming**
- Set `agents.defaults.stream: true`.

---

## 8) Security Tips

- Use a strong invite code (`secret`)
- Add Cloudflare Access or Tailscale as an extra gate
- Bind `host=127.0.0.1` when using tunnels
- Keep `rate_limit_rpm` reasonable

---

Need OAuth, WebSocket, or enterprise deployment? Build on top of this baseline.
