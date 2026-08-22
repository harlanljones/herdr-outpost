# herdr-outpost 1.0.0 on Cloudflare

Complete guide to host the **relay** and **web dashboard** on Cloudflare Workers and Tunnel under your own domain. This guide targets herdr-outpost 1.0.0 and Herdr 0.8.2 or newer. Replace `example.com` below with your actual domain throughout.

> This is the reference Cloudflare deployment. If you'd rather use Vercel, Netlify, Fly.io, Render, a plain VPS, or another platform, see [Alternative Deployment Platforms](README.md#alternative-deployment-platforms) in the main README — both `web/` and `relay/` run natively there too.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                            example.com                           │
│                        (Cloudflare Domain)                       │
└──────────────────────┬──────────────────────────────────────────┘
                       │
        ┌──────────────┴──────────────────┐
        │                                 │
   ┌────▼─────┐                     ┌─────▼──────┐
   │ Workers   │                     │  Tunnel    │
   │ (Web App) │                     │  (Relay)   │
   │ herdr.*   │                     │ relay.*    │
   │           │                     │            │
   └────┬─────┘                     └─────┬──────┘
        │                                 │
        │  Static HTML/JS/CSS             │  WebSocket
        │  (Served from /web)             │  localhost:8375
        │                                 │
        └──────────────┬──────────────────┘
                       │
                ┌──────▼──────┐
                │   Browser   │
                │   wss://... │
                └─────────────┘
```

## Prerequisites

- **Domain:** example.com (with Cloudflare DNS)
- **Relay machine:** macOS, Linux, or Windows; native Windows Herdr support is currently preview
- **Installed tools:**
  - Herdr 0.8.2+ ([installation guide](https://herdr.dev/docs/install/))
  - `cloudflared` ([Cloudflare Tunnel download](https://developers.cloudflare.com/tunnel/downloads/))
  - `git` and `uv` (Python runner)
  - Node.js and Wrangler for CLI deployment

The relay uses Herdr's supported `agent` and `pane` CLI interfaces. It does not require a community push plugin. Install Herdr's official integration for each harness when available; this improves lifecycle and session identity according to Herdr's [status-authority model](https://herdr.dev/docs/agents/#status-authority).

## Quick Start (5 min)

```bash
# 1. Clone & setup
git clone https://github.com/harlanljones/herdr-outpost
cd herdr-outpost

# 2. Install platform tools (macOS example)
brew install cloudflared uv
npm install -g wrangler

# Optional but recommended: install the official integration for your harness
herdr integration install claude

# 3. Authenticate Cloudflare
cloudflared tunnel login

# 4. Create tunnel & config
cloudflared tunnel create herdr-outpost
cloudflared tunnel route dns herdr-outpost relay.example.com

# 5. Create config (see Step 4 below)
# Save ~/.cloudflared/config-herdr-outpost.yml and ~/.config/herdr-outpost/config.env

# 6. Deploy web app to Cloudflare Workers
cd web
wrangler deploy

# 7. Start relay
cd ../relay
./start.sh

# 8. Open in browser
# https://herdr.example.com
```

## Step-by-Step Setup

### Step 1: Clone & Prepare

```bash
git clone https://github.com/harlanljones/herdr-outpost
cd herdr-outpost
```

### Step 2: Install Requirements

```bash
# Install Herdr on Linux or macOS
curl -fsSL https://herdr.dev/install.sh | sh

# macOS package tools
brew install cloudflared uv

# Linux: install uv; install cloudflared using Cloudflare's package repository
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows PowerShell (Herdr is preview on Windows)
powershell -ExecutionPolicy Bypass -c "irm https://herdr.dev/install.ps1 | iex"
winget install --id Cloudflare.cloudflared
winget install --id astral-sh.uv

# Wrangler is only needed for CLI deployment
npm install -g wrangler
```

Install and verify the official integration for each agent harness you run. Replace `claude` with a name from Herdr's [integration list](https://herdr.dev/docs/integrations/):

```bash
herdr integration install claude
herdr integration status
herdr agent list
```

### Step 3: Authenticate Cloudflare Tunnel

```bash
# Opens browser → authenticate → saves credentials
cloudflared tunnel login

# Create the tunnel (one-time)
cloudflared tunnel create herdr-outpost

# The output shows your Tunnel ID (save for config file)
# Tunnel UUID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
# Tunnel Name: herdr-outpost
# Credentials saved to ~/.cloudflared/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx.json
```

### Step 4: Create Configuration Files

#### 4A. Cloudflare Tunnel Config

Save to `~/.cloudflared/config-herdr-outpost.yml` (replace `YOUR_USERNAME` and `TUNNEL_ID`):

```yaml
tunnel: herdr-outpost
credentials-file: /Users/YOUR_USERNAME/.cloudflared/TUNNEL_ID.json

ingress:
  # Note: the web app (herdr.example.com) is served directly by Cloudflare
  # Workers via its own custom domain, NOT through this tunnel -- Workers
  # already terminates TLS and serves the static assets globally, so
  # proxying it through a laptop-hosted tunnel would just add a failure
  # point and take the dashboard down whenever this tunnel isn't running.

  # Relay (WebSocket + HTTP POST for events)
  - hostname: relay.example.com
    service: http://localhost:8375
    originRequest:
      http2Origin: false
      disableChunkedEncoding: false

  # Catch-all
  - service: http_status:404
```

**On Linux/Windows, use absolute credential paths:**
- Linux: `/home/YOUR_USERNAME/.cloudflared/TUNNEL_ID.json`
- Windows: `C:\Users\YOUR_USERNAME\.cloudflared\TUNNEL_ID.json`

On Windows, save the Tunnel configuration itself as `%USERPROFILE%\.cloudflared\config.yml`; `start.ps1` invokes `cloudflared tunnel run` using cloudflared's standard configuration location. The `config-herdr-outpost.yml` filename and `HERDR_OUTPOST_TUNNEL_CONFIG` setting are used by the POSIX launcher.

#### 4B. Relay Environment

Create `~/.config/herdr-outpost/config.env`:

```bash
# Relay settings
HERDR_OUTPOST_RELAY_PORT=8375
HERDR_OUTPOST_RELAY_HOST=127.0.0.1
HERDR_OUTPOST_TUNNEL_MODE=named
HERDR_OUTPOST_TUNNEL_NAME=herdr-outpost
HERDR_OUTPOST_TUNNEL_CONFIG="$HOME/.cloudflared/config-herdr-outpost.yml"

# Security (generate a new token)
HERDR_OUTPOST_RELAY_TOKEN=replace_with_the_generated_token
HERDR_OUTPOST_TRUSTED_ORIGINS="https://herdr.example.com"
```

**Generate & save your token:**

```bash
TOKEN=$(openssl rand -hex 32)
printf 'HERDR_OUTPOST_RELAY_TOKEN=%s\n' "$TOKEN"
```

Copy the printed assignment into `config.env` and save the token in your password manager. Do not leave command substitution in the file: `start.sh` sources it on every restart.

### Step 5: Deploy Web App to Cloudflare Workers

`web/wrangler.toml` already configures a Workers Static Assets project (no Worker script, no build step), with SPA fallback so `/session/{id}` deep links serve the app:

```toml
name = "herdr-outpost"
compatibility_date = "2024-01-01"

[assets]
directory = "."
html_handling = "auto-trailing-slash"
not_found_handling = "single-page-application"
```

#### Option A: Deploy with Wrangler CLI (Fastest)

```bash
npm install -g wrangler

cd web

# Deploy (requires Cloudflare account login)
wrangler deploy

# Output shows: Deployed to herdr-outpost.<your-subdomain>.workers.dev
```

#### Option B: Deploy via GitHub (Auto-deploy on push)

1. **Push repo to GitHub:**
   ```bash
   git remote set-url origin https://github.com/harlanljones/herdr-outpost
   git push -u origin main
   ```

2. **In Cloudflare Dashboard:**
   - Go to **Workers & Pages** → **Create application** → **Connect to Git**
   - Select your `herdr-outpost` repo
   - Build settings:
     - Framework preset: **None**
     - Build command: *(leave empty)*
     - Build output directory / assets directory: `web/`
   - Custom domain: `herdr.example.com` (under **Settings** → **Domains & Routes**)
   - Click **Save**

### Step 6: Connect the Dashboard

Open `https://herdr.example.com`, select **Settings**, and save:

- Relay WebSocket URL: `wss://relay.example.com`
- Bearer token: the value of `HERDR_OUTPOST_RELAY_TOKEN`

The dashboard stores these values in the browser. For first-device bootstrap you can open:

```text
https://herdr.example.com/?relay=wss%3A%2F%2Frelay.example.com&token=YOUR_TOKEN
```

After importing the settings, the dashboard removes the credentials from the visible URL. Use **Pair a Device** in Settings for subsequent devices, and treat the generated QR code or link as a credential.

### Step 7: Start the Relay

```bash
# Load environment variables
source ~/.config/herdr-outpost/config.env

# Navigate to relay directory
cd herdr-outpost/relay

# Start relay + tunnel
./start.sh

# Expected output:
# herdr-outpost relay
# Starting relay on :8375...
# Relay running (pid XXXXX)
# Starting named tunnel...
# Ready. Press Ctrl+C to stop.
```

**On Windows PowerShell:**

```powershell
$Env:HERDR_OUTPOST_RELAY_TOKEN = "your-token-here"
cd herdr-outpost\relay
.\start.ps1
```

### Step 8: Verify It Works

#### Test 1: Access Web App
```bash
open https://herdr.example.com
# Or: curl https://herdr.example.com
```

#### Test 2: Check WebSocket Connection
1. Open `https://herdr.example.com` in browser
2. Open DevTools (F12) → **Network** tab
3. Look for a connection to `wss://relay.example.com`
4. Should show status `101 Switching Protocols`

#### Test 3: Direct Relay Connection
```bash
# Test relay endpoint
curl -fsS -H "Authorization: Bearer YOUR_TOKEN" \
  https://relay.example.com/health

# Expected: JSON with status "ok" and version "1.0.0"
# NOT: 401, 403, 502, 503, or timeout
```

#### Test 4: WebSocket CLI
```bash
# Install wscat
npm install -g wscat

# Connect to relay
wscat -c wss://relay.example.com \
  --header "Authorization: Bearer YOUR_TOKEN"

# In the wscat console, type:
# {"type":"agents"}

# You should receive agent status. Each agent object includes liveness fields
# ("source" e.g. poll:local/hook, and "last_seen_at"); when a session is pruned
# (closed after 2 missed polls, or expired past HERDR_OUTPOST_SESSION_TTL) a
# broadcast of type "agent_removed" is emitted with reason closed|expired.
```

### Step 9: Optional — Set Up Auto-Start

#### macOS/Linux

```bash
cd herdr-outpost/relay
./install-service.sh

# Follow prompts to set up launchd/systemd
# This creates automatic startup and restart on crash
```

Verify:
```bash
# macOS
launchctl list | grep herdr-outpost

# Linux
systemctl --user status herdr-outpost-relay
journalctl --user-unit herdr-outpost-relay -f
```

#### Windows Task Scheduler

1. Open Task Scheduler (`taskschd.msc`)
2. Create Basic Task:
   - Name: `herdr-outpost-relay`
   - Trigger: `At log on`
   - Action: Start program → `relay/start.ps1`
   - Settings: Enabled

### Step 10: Security Hardening

#### Cloudflare WAF Rules

In Cloudflare Dashboard → **Security** → **WAF** → **Custom rules**:

**Rule 1: Only allow relay from your IP**
```
(http.host eq "relay.example.com") and 
(cf.client_ip ne "YOUR.IP.ADDRESS.HERE")
```
Action: **Block** (HTTP status 403)

Find your IP: `curl ifconfig.me`

For rate limiting, create a separate rule under **Security** → **WAF** → **Rate limiting rules**, matching:

```
http.host eq "relay.example.com"
```

Choose a threshold appropriate for your polling and number of devices. Keep the relay's bearer-token authentication and strict Origin validation enabled even when an edge rule is present.

Cloudflare Access is optional. If you add it in front of `relay.example.com`, each browser must first establish an Access session for that hostname; otherwise the cross-origin WebSocket handshake cannot reach the relay. Service-token headers cannot be added by this zero-build browser client.

#### Always Use HTTPS

In Cloudflare Dashboard → **SSL/TLS**:
- Enable **Always Use HTTPS**
- Enable **Automatic HTTPS Rewrites**

---

## Troubleshooting

### WebSocket connection fails with "403 Forbidden"

**Cause:** Origin validation failed or token is invalid.

**Fix:**
```bash
# 1. Check relay logs
tail -f ~/.local/state/herdr-outpost/log/relay.log

# 2. Confirm a token is configured without printing it
test -n "$HERDR_OUTPOST_RELAY_TOKEN" && echo "Relay token is set"

# 3. Check trusted origins
grep HERDR_OUTPOST_TRUSTED_ORIGINS ~/.config/herdr-outpost/config.env

# 4. Restart the installed relay service
systemctl --user restart herdr-outpost-relay.service
```

### "Untrusted WebSocket origin" error

**Cause:** Your domain isn't in `HERDR_OUTPOST_TRUSTED_ORIGINS`.

**Fix:**
```bash
# Update HERDR_OUTPOST_TRUSTED_ORIGINS in config.env, then restart
systemctl --user restart herdr-outpost-relay.service
```

### Tunnel keeps disconnecting

**Cause:** Network issues or invalid config.

**Fix:**
```bash
# Validate config
cloudflared tunnel --config ~/.cloudflared/config-herdr-outpost.yml ingress validate

# Check logs
tail -f ~/.cloudflared/tunnel.log

# Run the tunnel in the foreground after stopping its existing service cleanly
cloudflared tunnel --config ~/.cloudflared/config-herdr-outpost.yml run herdr-outpost
```

### "HERDR_OUTPOST_RELAY_TOKEN is required" error

**Cause:** Your relay is not running on loopback (127.0.0.1) without a token.

**Fix:**
```bash
# Generate a token, save it in your password manager, and replace the token
# assignment in ~/.config/herdr-outpost/config.env.
openssl rand -hex 32

# Restart the installed relay service
systemctl --user restart herdr-outpost-relay.service
```

### Cloudflare Workers shows 404

**Cause:** Web app not deployed or custom domain misconfigured.

**Fix:**
```bash
# Verify deployment
wrangler deployments list

# Redeploy
cd web && wrangler deploy

# Verify custom domain
# Cloudflare Dashboard → Workers & Pages → herdr-outpost → Settings → Domains & Routes
# Should be: herdr.example.com
```

### Agents not appearing in dashboard

**Cause:** Relay not polling herdr or no agents are running.

**Fix:**
```bash
# Check if Herdr detects agents locally
herdr agent list

# If running on remote hosts, verify SSH access
ssh user@remote-host herdr agent list

# Configure HERDR_OUTPOST_REMOTES in config.env
export HERDR_OUTPOST_REMOTES="user@remote1.com,user@remote2.com"

# Restart the installed relay service after editing config.env
systemctl --user restart herdr-outpost-relay.service
```

### Agents disappearing shortly after they stop

**Cause:** Session reconciliation. An agent missing from two consecutive `herdr agent list` polls is pruned as `closed`; hook/UDP-only reporters (never listed by `herdr agent list`) expire after `HERDR_OUTPOST_SESSION_TTL` seconds (default 90, legacy fallback `HERDR_SESSION_TTL`). Pruned sessions broadcast an `agent_removed` message.

**Fix:** This is expected lifecycle cleanup, not an error. To confirm reconciliation is running, check `/health` for `agents_by_host` and `last_reconcile_at`. To keep hook-reported sessions visible longer, raise `HERDR_OUTPOST_SESSION_TTL` in `config.env`.

### Agent incorrectly appears blocked

Herdr integrations can retain lifecycle authority after their native session registration is lost. The characteristic unreliable result is `blocked` with `screen_detection_skipped: true` and no `agent_session`.

herdr-outpost detects only this exact signature and asks Herdr to re-evaluate a recent plain-text detection snapshot with the active screen manifest. `working` or `idle` replaces the stale block, a manifest-confirmed `blocked` remains blocked, and any read, explain, or parsing failure safely retains the original unverified block. Registered sessions, ordinary screen-detected states, and hook/UDP events bypass the fallback.

To reproduce the classification manually:

```bash
herdr pane read <pane_id> --source detection --format text > screen.txt
herdr agent explain --file screen.txt --agent <agent> --json
```

See the relay's [detailed troubleshooting notes](relay/README.md#agents-incorrectly-show-as-blocked) and Herdr's [`agent explain` reference](https://herdr.dev/docs/cli-reference/#agents).

---

## Monitoring & Maintenance

### Relay Logs

```bash
# Linux
tail -f ~/.local/state/herdr-outpost/log/relay.log

# macOS
tail -f ~/Library/Logs/herdr-outpost/relay.log

# Windows
Get-Content "$Env:LOCALAPPDATA\herdr-outpost\logs\relay.log" -Tail 20 -Wait
```

### Audit Log (All Write Actions)

```bash
# Linux
tail -f ~/.local/state/herdr-outpost/log/audit.log

# macOS
tail -f ~/Library/Logs/herdr-outpost/audit.log

# Example entries:
# {"ts":"2024-08-21T12:34:56Z","action":"respond","paneId":"local:1","ip":"203.0.113.42","device":"iOS"}
# {"ts":"2024-08-21T12:35:12Z","action":"send_text","paneId":"local:2","ip":"203.0.113.42","device":"macOS"}
```

### Cloudflare Analytics

- **Dashboard** → **Analytics & Logs** → **Traffic**
- Metrics: Requests, Cache ratio, Status codes
- Real-time logs: **Logs** → **Real-time Logs**

### Health Check

The relay's own `/health` endpoint reports live session counts per host (`agents_by_host`) and the timestamp of the last reconciliation pass (`last_reconcile_at`):

```bash
curl -s -H "Authorization: Bearer $HERDR_OUTPOST_RELAY_TOKEN" \
  https://relay.example.com/health | python3 -m json.tool
```

```bash
#!/bin/bash
# health-check.sh — verify relay + tunnel are running

# 1. Check relay process
if ! pgrep -f herdr_relay > /dev/null; then
  echo "[FAIL] Relay not running"
  exit 1
fi

# 2. Check tunnel process
if ! pgrep -f cloudflared > /dev/null; then
  echo "[FAIL] Tunnel not running"
  exit 1
fi

# 3. Test relay endpoint
TOKEN="${HERDR_OUTPOST_RELAY_TOKEN:-$HERDR_RELAY_TOKEN}"
if ! curl -s -H "Authorization: Bearer $TOKEN" \
  https://relay.example.com/health > /dev/null 2>&1; then
  echo "[FAIL] Relay unreachable"
  exit 1
fi

# 4. Test web app
if ! curl -s https://herdr.example.com/ > /dev/null 2>&1; then
  echo "[FAIL] Web app unreachable"
  exit 1
fi

echo "[OK] All systems operational"
```

---

## Next Steps

1. **Add Telegram Bot** (optional)
   ```bash
   cd relay && ./install-service.sh
   # Select "Setup Telegram bot"
   ```

2. **Enable Web Push Notifications**
   - Open dashboard → Settings
   - Enable "Web Push"
   - Browser will prompt for permission

3. **Monitor Remote SSH Hosts**
   ```bash
   export HERDR_OUTPOST_REMOTES="user@dev-server.com,user@prod-server.com"
   # Agents on those hosts will now appear in dashboard
   ```

4. **Set Up Daily Digest**
   - Telegram: `/digest` command shows working time + block count

---

## Support & Resources

- **Herdr documentation:** [herdr.dev/docs](https://herdr.dev/docs/)
- **Herdr agents and status authority:** [herdr.dev/docs/agents](https://herdr.dev/docs/agents/)
- **Herdr CLI reference:** [herdr.dev/docs/cli-reference](https://herdr.dev/docs/cli-reference/)
- **Cloudflare Tunnel guide:** [developers.cloudflare.com/tunnel](https://developers.cloudflare.com/tunnel/)
- **Repo issues:** [github.com/harlanljones/herdr-outpost/issues](https://github.com/harlanljones/herdr-outpost/issues)
