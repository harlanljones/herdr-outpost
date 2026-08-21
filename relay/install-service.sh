#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS="$(uname -s)"

echo "========================================="
echo "   herdr-outpost Service Installer"
echo "========================================="

# Determine runner
if command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
    EXEC_CMD="$UV_BIN run --directory $SCRIPT_DIR python3 $SCRIPT_DIR/herdr_relay.py"
elif command -v python3 >/dev/null 2>&1; then
    PY_BIN="$(command -v python3)"
    EXEC_CMD="$PY_BIN $SCRIPT_DIR/herdr_relay.py"
else
    echo "[ERROR] Python 3 or uv is required." >&2
    exit 1
fi

CONFIG_FILE="${HOME}/.config/herdr-outpost/config.env"
if [[ ! -f "$CONFIG_FILE" && -f "${HOME}/.config/herdr-remote/config.env" ]]; then
    CONFIG_FILE="${HOME}/.config/herdr-remote/config.env"
fi

if [[ "$OS" == "Linux" ]]; then
    SERVICE_DIR="${HOME}/.config/systemd/user"
    SERVICE_FILE="${SERVICE_DIR}/herdr-outpost-relay.service"
    mkdir -p "$SERVICE_DIR"

    echo "Generating systemd user service at $SERVICE_FILE..."
    {
        echo "[Unit]"
        echo "Description=herdr-outpost Relay Daemon"
        echo "After=network.target"
        echo ""
        echo "[Service]"
        echo "Type=simple"
        echo "WorkingDirectory=${SCRIPT_DIR}"
        if [[ -f "$CONFIG_FILE" ]]; then
            echo "EnvironmentFile=${CONFIG_FILE}"
        fi
        echo "ExecStart=${EXEC_CMD}"
        echo "Restart=always"
        echo "RestartSec=3s"
        echo "StandardOutput=journal"
        echo "StandardError=journal"
        echo ""
        echo "[Install]"
        echo "WantedBy=default.target"
    } > "$SERVICE_FILE"

    echo "Reloading systemd daemon..."
    systemctl --user daemon-reload || true
    echo "Enabling and starting service..."
    systemctl --user enable herdr-outpost-relay.service || true
    systemctl --user restart herdr-outpost-relay.service || true

    echo ""
    echo "[OK] Service installed!"
    echo "Status check: systemctl --user status herdr-outpost-relay"
    echo "View logs:    journalctl --user-unit herdr-outpost-relay -f"

elif [[ "$OS" == "Darwin" ]]; then
    AGENT_DIR="${HOME}/Library/LaunchAgents"
    PLIST_FILE="${AGENT_DIR}/com.herdr-outpost.relay.plist"
    LOG_DIR="${HOME}/Library/Logs/herdr-outpost"
    mkdir -p "$AGENT_DIR" "$LOG_DIR"

    echo "Generating launchd agent plist at $PLIST_FILE..."

    read -r -a CMD_ARRAY <<< "$EXEC_CMD"
    PLIST_ARGS=""
    for arg in "${CMD_ARRAY[@]}"; do
        PLIST_ARGS+="        <string>${arg}</string>\n"
    done

    {
        echo "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        echo "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">"
        echo "<plist version=\"1.0\">"
        echo "<dict>"
        echo "    <key>Label</key>"
        echo "    <string>com.herdr-outpost.relay</string>"
        echo "    <key>ProgramArguments</key>"
        echo "    <array>"
        printf '%b' "$PLIST_ARGS"
        echo "    </array>"
        echo "    <key>WorkingDirectory</key>"
        echo "    <string>${SCRIPT_DIR}</string>"
        echo "    <key>RunAtLoad</key>"
        echo "    <true/>"
        echo "    <key>KeepAlive</key>"
        echo "    <true/>"
        echo "    <key>StandardOutPath</key>"
        echo "    <string>${LOG_DIR}/service.log</string>"
        echo "    <key>StandardErrorPath</key>"
        echo "    <string>${LOG_DIR}/service.log</string>"
        echo "</dict>"
        echo "</plist>"
    } > "$PLIST_FILE"

    echo "Loading launchd agent..."
    launchctl unload "$PLIST_FILE" 2>/dev/null || true
    launchctl load "$PLIST_FILE" 2>/dev/null || true

    echo ""
    echo "[OK] Launchd agent installed and loaded!"
    echo "Status check: launchctl list | grep herdr"
    echo "View logs:    tail -f ${LOG_DIR}/service.log"

else
    echo "[ERROR] Unsupported operating system: $OS" >&2
    exit 1
fi
