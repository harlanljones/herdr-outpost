#!/usr/bin/env python3
"""herdr-outpost Event Hook.

Invoked by herdr plugin hooks (e.g., pane.agent_status_changed, pane.created).
Sends event data to the local running herdr-outpost relay via UDP and HTTP POST.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import urllib.request
from typing import Any, Dict, Optional


def get_env(key: str, default: Optional[str] = None) -> Optional[str]:
    outpost_key = f"HERDR_OUTPOST_{key}" if not key.startswith("HERDR_") else key
    legacy_key = key if key.startswith("HERDR_") else f"HERDR_{key}"
    if outpost_key in os.environ:
        return os.environ[outpost_key]
    if legacy_key in os.environ:
        return os.environ[legacy_key]
    if key in os.environ:
        return os.environ[key]
    return default


RELAY_HOST = get_env("RELAY_HOST", "127.0.0.1")
RELAY_PORT = int(get_env("RELAY_PORT", "8375"))
RELAY_TOKEN = get_env("RELAY_TOKEN", "")


def parse_input() -> Dict[str, Any]:
    """Parse JSON event from stdin or CLI arguments."""
    payload: Dict[str, Any] = {}

    # 1. Try command line arguments
    if len(sys.argv) > 1:
        raw = " ".join(sys.argv[1:]).strip()
        if raw.startswith("{") and raw.endswith("}"):
            try:
                return json.loads(raw)
            except Exception:
                pass
        # Key-value arguments: e.g. event=pane.agent_status_changed pane_id=1 status=blocked
        for arg in sys.argv[1:]:
            if "=" in arg:
                k, v = arg.split("=", 1)
                payload[k.strip()] = v.strip()
        if payload:
            return payload

    # 2. Try stdin if not a tty
    if not sys.stdin.isatty():
        try:
            stdin_data = sys.stdin.read().strip()
            if stdin_data:
                return json.loads(stdin_data)
        except Exception:
            pass

    return payload


def send_udp(payload_bytes: bytes) -> bool:
    """Send fast datagram to local relay UDP socket."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.5)
        sock.sendto(payload_bytes, (RELAY_HOST, RELAY_PORT))
        sock.close()
        return True
    except Exception:
        return False


def send_http(payload_bytes: bytes) -> bool:
    """Send HTTP POST /event to relay."""
    url = f"http://{RELAY_HOST}:{RELAY_PORT}/event"
    headers = {"Content-Type": "application/json"}
    if RELAY_TOKEN:
        headers["Authorization"] = f"Bearer {RELAY_TOKEN}"

    req = urllib.request.Request(url, data=payload_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return resp.status in (200, 201, 202)
    except Exception:
        return False


def main() -> None:
    event_data = parse_input()
    if not event_data:
        sys.exit(0)

    payload_bytes = json.dumps(event_data).encode("utf-8")

    # Send via UDP first, fallback to HTTP POST if needed
    if not send_udp(payload_bytes):
        send_http(payload_bytes)

    # Always exit 0 to not interrupt herdr execution
    sys.exit(0)


if __name__ == "__main__":
    main()
