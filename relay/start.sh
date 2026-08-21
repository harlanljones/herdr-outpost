#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# -----------------------------------------------------------------------------
# Configuration Loading
# -----------------------------------------------------------------------------

CONFIG_PATHS=(
    "${HERDR_OUTPOST_CONFIG:-}"
    "${XDG_CONFIG_HOME:-$HOME/.config}/herdr-outpost/config.env"
    "$HOME/.config/herdr-outpost/config.env"
    "$HOME/.config/herdr-remote/config.env"
)

CONFIG_LOADED=""
for cfg in "${CONFIG_PATHS[@]}"; do
    if [[ -n "$cfg" && -f "$cfg" ]]; then
        # shellcheck disable=SC1090
        set -a
        source "$cfg"
        set +a
        CONFIG_LOADED="$cfg"
        break
    fi
done

echo "========================================="
echo "   herdr-outpost relay daemon"
echo "========================================="

if [[ -n "$CONFIG_LOADED" ]]; then
    echo "Loaded config: $CONFIG_LOADED"
else
    echo "No config.env found, using environment/defaults."
fi

# -----------------------------------------------------------------------------
# Command Runner Resolution (uv or python3)
# -----------------------------------------------------------------------------

if command -v uv >/dev/null 2>&1; then
    PY_RUN=(uv run --directory "$SCRIPT_DIR" python3)
elif command -v python3 >/dev/null 2>&1; then
    PY_RUN=(python3)
else
    echo "[ERROR] neither 'uv' nor 'python3' found in PATH." >&2
    exit 1
fi

RELAY_PORT="${HERDR_OUTPOST_RELAY_PORT:-${HERDR_RELAY_PORT:-8375}}"
echo "Starting relay on port :$RELAY_PORT..."

# -----------------------------------------------------------------------------
# Process Management & Traps
# -----------------------------------------------------------------------------

RELAY_PID=""
TUNNEL_PID=""

cleanup() {
    echo ""
    echo "Shutting down herdr-outpost services..."
    if [[ -n "$RELAY_PID" ]] && kill -0 "$RELAY_PID" 2>/dev/null; then
        kill "$RELAY_PID" 2>/dev/null || true
    fi
    if [[ -n "$TUNNEL_PID" ]] && kill -0 "$TUNNEL_PID" 2>/dev/null; then
        kill "$TUNNEL_PID" 2>/dev/null || true
    fi
    wait 2>/dev/null || true
    echo "herdr-outpost relay stopped."
}

trap cleanup SIGINT SIGTERM EXIT

# Start Relay Daemon in background
"${PY_RUN[@]}" "$SCRIPT_DIR/herdr_relay.py" &
RELAY_PID=$!
echo "Relay running (pid $RELAY_PID)"

# -----------------------------------------------------------------------------
# Optional Cloudflare Tunnel Integration
# -----------------------------------------------------------------------------

TUNNEL_MODE="${HERDR_OUTPOST_TUNNEL_MODE:-${HERDR_TUNNEL_MODE:-}}"
TUNNEL_NAME="${HERDR_OUTPOST_TUNNEL_NAME:-${HERDR_TUNNEL_NAME:-}}"
TUNNEL_CONFIG="${HERDR_OUTPOST_TUNNEL_CONFIG:-${HERDR_TUNNEL_CONFIG:-$HOME/.cloudflared/config-herdr.yml}}"

if [[ "$TUNNEL_MODE" == "named" && -n "$TUNNEL_NAME" ]]; then
    if command -v cloudflared >/dev/null 2>&1; then
        echo "Starting named Cloudflare tunnel ($TUNNEL_NAME)..."
        if [[ -f "$TUNNEL_CONFIG" ]]; then
            cloudflared tunnel --config "$TUNNEL_CONFIG" run "$TUNNEL_NAME" &
        else
            cloudflared tunnel run "$TUNNEL_NAME" &
        fi
        TUNNEL_PID=$!
        echo "Tunnel running (pid $TUNNEL_PID)"
    else
        echo "[WARN] 'cloudflared' command not found, skipping tunnel launch."
    fi
fi

echo "Ready. Press Ctrl+C to stop."

# Wait for relay process
wait "$RELAY_PID"
