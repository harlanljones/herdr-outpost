# Herdr-Outpost on Cloudflare

Complete guide to host the **relay** and **web dashboard** on Cloudflare Workers and Tunnel under your own domain. Replace `example.com` below with your actual domain throughout.

> This is the reference Cloudflare deployment. If you'd rather use Vercel, Netlify, Fly.io, Render, a plain VPS, or another platform, see [Alternative Deployment Platforms](README.md#-alternative-deployment-platforms) in the main README — both `web/` and `relay/` run natively there too.

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
   │ /         │                     │ /relay     │
   │           │                     │            │
   └────┬─────┘                     └─────┬──────┘
        │                                 │
        │  Static HTML/JS/CSS             │  WebSocket
        │  (Built from /web)              │  localhost:8375
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
- **Local machine:** macOS, Linux, or Windows
- **Installed tools:**
  - `herdr` 0.7+ ([herdr.dev](https://herdr.dev))
  - `cloudflared` ([Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/downloads/))
  - `git` and `uv` (Python runner)
  - `herdr-push` plugin: `herdr plugin install dcolinmorgan/herdr-push`

## Quick Start (5 min)

```bash
# 1. Clone & setup
git clone https://github.com/harlanljones/herdr-outpost
cd herdr-outpost

# 2. Install dependencies
herdr plugin install dcolinmorgan/herdr-push
brew install cloudflared  # macOS

# 3. Authenticate Cloudflare
cloudflared tunnel login

# 4. Create tunnel & config
cloudflared tunnel create herdr-outpost
cloudflared tunnel route dns herdr-outpost relay.example.com
cloudflared tunnel route dns herdr-outpost herdr.example.com

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
# macOS
brew install cloudflared uv
herdr plugin install dcolinmorgan/herdr-push

# Linux
curl -fsSL https://sh.rustup.rs | sh  # uv needs rust
cargo install uv
sudo apt-get install cloudflared
herdr plugin install dcolinmorgan/herdr-push

# Windows (PowerShell)
choco install cloudflared uv  # or download from websites
herdr plugin install dcolinmorgan/herdr-push
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

**On Linux/Windows, use absolute paths:**
- Linux: `/home/YOUR_USERNAME/.cloudflared/TUNNEL_ID.json`
- Windows: `C:\Users\YOUR_USERNAME\.cloudflared\TUNNEL_ID.json`

#### 4B. Relay Environment

Create `~/.config/herdr-outpost/config.env`:

```bash
# Relay settings
HERDR_OUTPOST_RELAY_PORT=8375
HERDR_OUTPOST_RELAY_HOST=127.0.0.1
HERDR_OUTPOST_TUNNEL_MODE=named
HERDR_OUTPOST_TUNNEL_NAME=herdr-outpost

# Security (generate a new token)
HERDR_OUTPOST_RELAY_TOKEN="$(openssl rand -hex 32)"
HERDR_OUTPOST_TRUSTED_ORIGINS="https://herdr.example.com,https://relay.example.com"
```

**Generate & save your token:**

```bash
TOKEN=$(openssl rand -hex 32)
echo "HERDR_OUTPOST_RELAY_TOKEN=$TOKEN"  # Save this in your password manager!
```

### Step 5: Deploy Web App to Cloudflare Workers

`web/wrangler.toml` already configures a Workers Static Assets project (no Worker script, no build step):

```toml
name = "herdr-outpost"
compatibility_date = "2024-01-01"

[assets]
directory = "."
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

### Step 6: Configure Web App Relay URL

Edit `web/index.html` (or configuration) to specify your relay endpoint:

```javascript
const RELAY_URL = 'wss://relay.example.com';

// Or allow dynamic override:
const params = new URLSearchParams(window.location.search);
const RELAY_URL = params.get('relay') || 'wss://relay.example.com';
```

Redeploy after editing:
```bash
cd web && wrangler deploy
```

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
curl -v -H "Authorization: Bearer YOUR_TOKEN" \
  https://relay.example.com/

# Expected: 400 or 403 (GET without WebSocket is not valid)
# NOT: 502, 503, or timeout
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

# You should receive agent status
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

**Rule 2: Rate limit relay**
```
http.host eq "relay.example.com"
```
Action: **Rate limit** (100 requests per 10 seconds per IP)

#### Tunnel Access Control (Cloudflare Zero Trust)

1. Go to **Zero Trust** → **Access** → **Tunnel routes**
2. Find `relay.example.com` → **Edit**
3. Add policy:
   - Selector: **IP Ranges**
   - Value: `YOUR.IP.ADDRESS/32`
   - Decision: **Allow**
4. Default: **Deny**

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

# 2. Verify token matches
echo $HERDR_OUTPOST_RELAY_TOKEN

# 3. Check trusted origins
grep HERDR_OUTPOST_TRUSTED_ORIGINS ~/.config/herdr-outpost/config.env

# 4. Restart relay
pkill -f herdr_relay
source ~/.config/herdr-outpost/config.env
cd herdr-outpost/relay && ./start.sh
```

### "Untrusted WebSocket origin" error

**Cause:** Your domain isn't in `HERDR_OUTPOST_TRUSTED_ORIGINS`.

**Fix:**
```bash
# Update config
export HERDR_OUTPOST_TRUSTED_ORIGINS="https://herdr.example.com"

# Restart
pkill -f herdr_relay
cd herdr-outpost/relay && ./start.sh
```

### Tunnel keeps disconnecting

**Cause:** Network issues or invalid config.

**Fix:**
```bash
# Validate config
cloudflared tunnel validate --config ~/.cloudflared/config-herdr-outpost.yml

# Check logs
tail -f ~/.cloudflared/tunnel.log

# Restart tunnel
pkill -f cloudflared
cloudflared tunnel --config ~/.cloudflared/config-herdr-outpost.yml run herdr-outpost
```

### "HERDR_OUTPOST_RELAY_TOKEN is required" error

**Cause:** Your relay is not running on loopback (127.0.0.1) without a token.

**Fix:**
```bash
# Generate token
export HERDR_OUTPOST_RELAY_TOKEN="$(openssl rand -hex 32)"

# Update config file
echo "HERDR_OUTPOST_RELAY_TOKEN=$HERDR_OUTPOST_RELAY_TOKEN" >> ~/.config/herdr-outpost/config.env

# Restart
pkill -f herdr_relay
cd herdr-outpost/relay && ./start.sh
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
# Check if herdr agents are running locally
herdr pane list

# If running on remote hosts, verify SSH access
ssh user@remote-host herdr pane list

# Configure HERDR_OUTPOST_REMOTES in config.env
export HERDR_OUTPOST_REMOTES="user@remote1.com,user@remote2.com"

# Restart relay
pkill -f herdr_relay
cd herdr-outpost/relay && ./start.sh
```

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
  https://relay.example.com/ > /dev/null 2>&1; then
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

- **Herdr documentation:** [herdr.dev/docs](https://herdr.dev)
- **Cloudflare Tunnel guide:** [developers.cloudflare.com/cloudflare-one/connections/connect-apps](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/)
- **Repo issues:** [github.com/harlanljones/herdr-outpost/issues](https://github.com/harlanljones/herdr-outpost/issues)
- **Herdr Slack:** [community at herdr.dev](https://herdr.dev)
