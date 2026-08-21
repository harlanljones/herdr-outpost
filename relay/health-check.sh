#!/usr/bin/env bash
# health-check.sh — verify herdr-outpost relay and connectivity
set -uo pipefail

CONFIG_PATHS=(
    "${HERDR_OUTPOST_CONFIG:-}"
    "${XDG_CONFIG_HOME:-$HOME/.config}/herdr-outpost/config.env"
    "$HOME/.config/herdr-outpost/config.env"
    "$HOME/.config/herdr-remote/config.env"
)

for cfg in "${CONFIG_PATHS[@]}"; do
    if [[ -n "$cfg" && -f "$cfg" ]]; then
        # shellcheck disable=SC1090
        set -a
        source "$cfg"
        set +a
        break
    fi
done

RELAY_PORT="${HERDR_OUTPOST_RELAY_PORT:-${HERDR_RELAY_PORT:-8375}}"
RELAY_HOST="${HERDR_OUTPOST_RELAY_HOST:-${HERDR_RELAY_HOST:-127.0.0.1}}"
RELAY_TOKEN="${HERDR_OUTPOST_RELAY_TOKEN:-${HERDR_RELAY_TOKEN:-}}"
TUNNEL_MODE="${HERDR_OUTPOST_TUNNEL_MODE:-${HERDR_TUNNEL_MODE:-}}"
PUBLIC_RELAY="${HERDR_OUTPOST_PUBLIC_RELAY_URL:-${HERDR_PUBLIC_RELAY_URL:-https://relay.harlanljones.com}}"
PUBLIC_WEB="${HERDR_OUTPOST_PUBLIC_WEB_URL:-${HERDR_PUBLIC_WEB_URL:-https://herdr.harlanljones.com}}"

ERRORS=0

echo "Running herdr-outpost health checks..."

# 1. Check local relay process
if pgrep -f "herdr_relay" > /dev/null 2>&1; then
    echo "  [OK] Relay daemon process is running"
else
    echo "  [FAIL] Relay daemon process is NOT running"
    ERRORS=$((ERRORS + 1))
fi

# 2. Check local HTTP health endpoint
AUTH_HEADER=()
if [[ -n "$RELAY_TOKEN" ]]; then
    AUTH_HEADER=(-H "Authorization: Bearer $RELAY_TOKEN")
fi

HEALTH_RESP=$(curl -s -m 3 "${AUTH_HEADER[@]}" "http://${RELAY_HOST}:${RELAY_PORT}/health" 2>/dev/null || true)
if echo "$HEALTH_RESP" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
    echo "  [OK] Local relay endpoint http://${RELAY_HOST}:${RELAY_PORT}/health is healthy"
else
    echo "  [FAIL] Local relay endpoint http://${RELAY_HOST}:${RELAY_PORT}/health failed or unreachable"
    ERRORS=$((ERRORS + 1))
fi

# 3. Check cloudflared tunnel process if configured
if [[ "$TUNNEL_MODE" == "named" ]]; then
    if pgrep -f "cloudflared" > /dev/null 2>&1; then
        echo "  [OK] Cloudflare tunnel (cloudflared) process is running"
    else
        echo "  [WARN] Cloudflare tunnel process is NOT running"
    fi
fi

# 4. Check public relay endpoint if online
if [[ -n "$PUBLIC_RELAY" ]]; then
    PUB_HEALTH=$(curl -s -m 5 "${AUTH_HEADER[@]}" "${PUBLIC_RELAY}/health" 2>/dev/null || true)
    if echo "$PUB_HEALTH" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
        echo "  [OK] Public relay endpoint ${PUBLIC_RELAY} is reachable"
    else
        echo "  [INFO] Public relay endpoint ${PUBLIC_RELAY} not reachable (tunnel may be offline or restricted)"
    fi
fi

# 5. Check public web app
if [[ -n "$PUBLIC_WEB" ]]; then
    if curl -s -m 5 -o /dev/null -w "%{http_code}" "$PUBLIC_WEB" 2>/dev/null | grep -q '^[23]'; then
        echo "  [OK] Web dashboard ${PUBLIC_WEB} is reachable"
    else
        echo "  [INFO] Web dashboard ${PUBLIC_WEB} not reachable"
    fi
fi

echo ""
if [[ $ERRORS -eq 0 ]]; then
    echo "🎉 All local herdr-outpost checks passed!"
    exit 0
else
    echo "💥 $ERRORS health check(s) failed."
    exit 1
fi
