#!/usr/bin/env python3
"""herdr-outpost Relay Daemon.

Async daemon bridging the local `herdr` socket/CLI and optional remote SSH hosts
to WebSocket & HTTP clients. Includes token authentication, origin verification,
central secret scrubbing, audit logging, push event consumption, and Web Push notifications.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import hmac
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import platform
import re
import signal
import socket
import sys
import urllib.parse
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import websockets
from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import ServerConnection, serve as ws_serve

from agent_state import (
    agent_update_message,
    agents_snapshot_message,
    apply_agent_message,
    build_agent_id,
    complete_agent_update_message,
    normalize_agent_dict,
    parse_agent_id,
)
from probes import enrich as probe_enrich

# Optional Web Push imports
try:
    from pywebpush import WebPushException, webpush
except ImportError:
    webpush = None
    WebPushException = Exception

try:
    from py_vapid import Vapid
except ImportError:
    Vapid = None


# -----------------------------------------------------------------------------
# Configuration Helpers
# -----------------------------------------------------------------------------

def get_env(key: str, default: Optional[str] = None) -> Optional[str]:
    """Retrieve environment variable supporting HERDR_OUTPOST_* and fallback to HERDR_*."""
    outpost_key = f"HERDR_OUTPOST_{key}" if not key.startswith("HERDR_") else key
    legacy_key = key if key.startswith("HERDR_") else f"HERDR_{key}"
    
    if outpost_key in os.environ:
        return os.environ[outpost_key]
    if legacy_key in os.environ:
        return os.environ[legacy_key]
    if key in os.environ:
        return os.environ[key]
    return default


def get_default_log_dir() -> str:
    """Determine OS-standard log directory for herdr-outpost."""
    custom = get_env("LOG_DIR")
    if custom:
        return custom

    system = platform.system()
    if system == "Darwin":
        return os.path.expanduser("~/Library/Logs/herdr-outpost")
    if system == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local"))
        return os.path.join(local_app_data, "herdr-outpost", "logs")
    # Linux and other UNIX
    state_home = os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
    return os.path.join(state_home, "herdr-outpost", "log")


CONFIG = {
    "host": get_env("RELAY_HOST", "127.0.0.1"),
    "port": int(get_env("RELAY_PORT", "8375")),
    "token": get_env("RELAY_TOKEN", ""),
    "trusted_origins": [
        orig.strip()
        for orig in (get_env("TRUSTED_ORIGINS", "") or "").split(",")
        if orig.strip()
    ],
    "log_dir": get_default_log_dir(),
    "audit_log": get_env("AUDIT_LOG", ""),
    "remotes": [
        r.strip()
        for r in (get_env("REMOTES", "") or "").split(",")
        if r.strip()
    ],
    "poll_interval": float(get_env("POLL_INTERVAL", "3.0")),
    "output_interval": float(get_env("OUTPUT_INTERVAL", "2.0")),
    "output_lines": int(get_env("OUTPUT_LINES", "300")),
    "vapid_private_key": get_env("VAPID_PRIVATE_KEY", ""),
    "vapid_public_key": get_env("VAPID_PUBLIC_KEY", ""),
    "vapid_claims_email": get_env("VAPID_CLAIMS_EMAIL", "mailto:admin@example.com"),
    "telegram_token": get_env("TELEGRAM_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", ""),
    "telegram_chat_id": get_env("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", ""),
}


# -----------------------------------------------------------------------------
# Secret Scrubbing
# -----------------------------------------------------------------------------

SENSITIVE_KEYS = {
    "token",
    "relay_token",
    "auth",
    "authorization",
    "password",
    "secret",
    "private_key",
    "vapid_private_key",
    "telegram_token",
    "bot_token",
}

TOKEN_PATTERN = re.compile(r"([?&](?:token|auth|key|secret)=)[^&\s\"']+", re.IGNORECASE)
BEARER_PATTERN = re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]{8,}", re.IGNORECASE)
JSON_AUTH_PATTERN = re.compile(r'(?i)"(authorization|token|secret)":\s*"[^"]+"')


def scrub(text: Any, secrets: Optional[List[str]] = None) -> str:
    """Scrub sensitive bearer tokens, passwords, and secret strings from logs."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)

    # Scrub Bearer tokens and URL query tokens
    text = BEARER_PATTERN.sub(r"\1[REDACTED]", text)
    text = TOKEN_PATTERN.sub(r"\1[REDACTED]", text)
    text = JSON_AUTH_PATTERN.sub(r'"\1": "[REDACTED]"', text)

    # Scrub known configured secrets if present
    all_secrets = list(secrets or [])
    for sec in [CONFIG.get("token"), CONFIG.get("vapid_private_key"), CONFIG.get("telegram_token")]:
        if sec:
            all_secrets.append(sec)

    for secret in all_secrets:
        if secret and len(str(secret)) >= 4:
            text = text.replace(str(secret), "[REDACTED]")

    return text


def scrub_dict(data: Any) -> Any:
    """Recursively scrub sensitive keys and string values in dictionaries."""
    if isinstance(data, dict):
        cleaned = {}
        for k, v in data.items():
            if str(k).lower() in SENSITIVE_KEYS:
                cleaned[k] = "[REDACTED]"
            else:
                cleaned[k] = scrub_dict(v)
        return cleaned
    if isinstance(data, list):
        return [scrub_dict(item) for item in data]
    if isinstance(data, str):
        return scrub(data)
    return data


# -----------------------------------------------------------------------------
# Auth and Origin Helpers
# -----------------------------------------------------------------------------

def parse_auth_token(
    headers: Optional[Dict[str, str]] = None,
    query_string: Optional[str] = None,
) -> Optional[str]:
    """Extract token from Authorization header, X-Relay-Token, or URL query parameters."""
    if headers:
        for k, v in headers.items():
            k_lower = k.lower()
            if k_lower == "authorization":
                parts = str(v).strip().split()
                if len(parts) == 2 and parts[0].lower() == "bearer":
                    return parts[1]
                if len(parts) == 1:
                    return parts[0]
            elif k_lower == "x-relay-token":
                return str(v).strip()

    if query_string:
        match = re.search(r"[?&](?:token|auth)=([^&\s]+)", query_string)
        if match:
            return match.group(1)

    return None


def verify_token(provided_token: Optional[str], expected_token: Optional[str] = None) -> bool:
    """Constant-time token verification."""
    target = expected_token if expected_token is not None else CONFIG.get("token", "")
    if not target:
        return True  # No token configured (loopback mode)
    if not provided_token:
        return False
    return hmac.compare_digest(provided_token.strip(), target.strip())


def validate_origin(origin: Optional[str], trusted_origins: Union[str, List[str]]) -> bool:
    """Strict origin validation against allowed trusted origins list."""
    if not origin:
        return True  # Native CLI or non-browser client without Origin header

    if isinstance(trusted_origins, str):
        allowed = [o.strip().rstrip("/").lower() for o in trusted_origins.split(",") if o.strip()]
    else:
        allowed = [str(o).strip().rstrip("/").lower() for o in trusted_origins if o]

    if not allowed or "*" in allowed:
        return True

    cleaned_origin = str(origin).strip().rstrip("/").lower()
    if cleaned_origin in allowed:
        return True

    parsed = urllib.parse.urlparse(cleaned_origin)
    origin_host = parsed.netloc or parsed.path

    # Check wildcard domains e.g. *.example.com or https://*.example.com
    for entry in allowed:
        e_parsed = urllib.parse.urlparse(entry if "://" in entry else f"http://{entry}")
        e_host = e_parsed.netloc or e_parsed.path
        if e_host.startswith("*."):
            suffix = e_host[2:]
            hostname = parsed.hostname or ""
            if hostname == suffix or hostname.endswith("." + suffix):
                if not e_parsed.scheme or e_parsed.scheme == parsed.scheme:
                    return True

    return False


# -----------------------------------------------------------------------------
# Logging and Auditing Setup
# -----------------------------------------------------------------------------

os.makedirs(CONFIG["log_dir"], exist_ok=True)
log_file_path = os.path.join(CONFIG["log_dir"], "relay.log")
audit_file_path = CONFIG["audit_log"] or os.path.join(CONFIG["log_dir"], "audit.log")
sub_file_path = os.path.join(CONFIG["log_dir"], "subscriptions.json")

logger = logging.getLogger("herdr-outpost-relay")
logger.setLevel(logging.INFO)
logger.propagate = False

class ScrubbingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        orig = super().format(record)
        return scrub(orig)

formatter = ScrubbingFormatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

file_handler = RotatingFileHandler(log_file_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)


def audit_log(action: str, pane_id: str = "", ip: str = "", client: str = "", details: Optional[Dict[str, Any]] = None) -> None:
    """Record write action or security event to audit.log JSONL."""
    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "action": action,
        "pane_id": pane_id,
        "ip": ip,
        "client": client,
        "details": scrub_dict(details or {}),
    }
    try:
        with open(audit_file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as err:
        logger.error(f"Failed to write audit log: {err}")


# -----------------------------------------------------------------------------
# Push Subscriptions & Notifications
# -----------------------------------------------------------------------------

class PushNotificationManager:
    def __init__(self, storage_path: str):
        self.storage_path = storage_path
        self.subscriptions: List[Dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    self.subscriptions = json.load(f)
            except Exception as err:
                logger.warning(f"Could not load subscriptions file: {err}")
                self.subscriptions = []

    def save(self) -> None:
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self.subscriptions, f, indent=2)
        except Exception as err:
            logger.error(f"Could not save subscriptions file: {err}")

    def add_subscription(self, sub: Dict[str, Any]) -> bool:
        endpoint = sub.get("endpoint")
        if not endpoint:
            return False
        self.subscriptions = [s for s in self.subscriptions if s.get("endpoint") != endpoint]
        self.subscriptions.append(sub)
        self.save()
        return True

    def remove_subscription(self, endpoint: str) -> None:
        self.subscriptions = [s for s in self.subscriptions if s.get("endpoint") != endpoint]
        self.save()

    async def notify_all(self, title: str, body: str, pane_id: str = "", status: str = "") -> None:
        if not webpush or not CONFIG["vapid_private_key"]:
            return

        payload = json.dumps({
            "title": title,
            "body": body,
            "pane_id": pane_id,
            "status": status,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })

        vapid_claims = {
            "sub": CONFIG["vapid_claims_email"],
        }

        expired_endpoints = []
        for sub in list(self.subscriptions):
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None,
                    lambda s=sub: webpush(
                        subscription_info=s,
                        data=payload,
                        vapid_private_key=CONFIG["vapid_private_key"],
                        vapid_claims=vapid_claims,
                    ),
                )
            except WebPushException as ex:
                logger.warning(f"WebPush failed for subscriber: {ex}")
                if hasattr(ex, "response") and ex.response is not None and ex.response.status_code in (404, 410):
                    expired_endpoints.append(sub.get("endpoint"))
            except Exception as ex:
                logger.warning(f"Push dispatch error: {ex}")

        if expired_endpoints:
            self.subscriptions = [s for s in self.subscriptions if s.get("endpoint") not in expired_endpoints]
            self.save()


push_manager = PushNotificationManager(sub_file_path)


# -----------------------------------------------------------------------------
# Telegram Notification Helper
# -----------------------------------------------------------------------------

async def notify_telegram(title: str, text: str, pane_id: str = "") -> None:
    """Send immediate notification to configured Telegram chat."""
    token = CONFIG["telegram_token"]
    chat_id = CONFIG["telegram_chat_id"]
    if not token or not chat_id:
        return

    message = f"🚨 *{title}*\n\n{text}"
    if pane_id:
        message += f"\n\n*Pane:* `{pane_id}`"

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
    }).encode("utf-8")

    try:
        loop = asyncio.get_running_loop()
        def send_req():
            import urllib.request
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.read()
        await loop.run_in_executor(None, send_req)
    except Exception as err:
        logger.warning(f"Telegram notification error: {err}")


# -----------------------------------------------------------------------------
# Herdr Relay Daemon Core
# -----------------------------------------------------------------------------

class HerdrRelayDaemon:
    PID_CACHE_TTL = 10.0

    def __init__(self):
        self.agents_state: Dict[str, Dict[str, Any]] = {}
        self._pid_cache: Dict[str, Tuple[float, Optional[int]]] = {}
        self.ws_clients: Set[ServerConnection] = set()
        self.authenticated_clients: Set[ServerConnection] = set()
        # Output streaming: client -> set of subscribed composite agent_ids,
        # and agent_id -> hash of last pushed output (for change detection)
        self.output_subs: Dict[ServerConnection, Set[str]] = {}
        self.output_cursors: Dict[str, str] = {}
        self.output_locks: Dict[str, asyncio.Lock] = {}
        self.lock = asyncio.Lock()
        self.internal_ws_port = CONFIG["port"] + 100
        self.running = False
        self.front_server: Optional[asyncio.Server] = None
        self.ws_server = None
        self.udp_transport = None

    def is_origin_trusted(self, origin: Optional[str]) -> bool:
        """Validate client Origin header against trusted origins."""
        return validate_origin(origin, CONFIG["trusted_origins"])

    def is_token_valid(self, provided_token: Optional[str]) -> bool:
        """Verify token against configured secret."""
        return verify_token(provided_token, CONFIG["token"])

    def check_auth(self, headers: Dict[str, str], query_params: Dict[str, List[str]]) -> bool:
        """Check authentication from headers or query parameters."""
        if not CONFIG["token"]:
            return True

        token = parse_auth_token(headers=headers)
        if token and self.is_token_valid(token):
            return True

        if "token" in query_params:
            for t in query_params["token"]:
                if self.is_token_valid(t):
                    return True

        return False

    async def broadcast_state(self, message: Dict[str, Any]) -> None:
        """Broadcast state update to all authenticated connected WebSocket clients."""
        if not self.ws_clients:
            return
        payload = json.dumps(scrub_dict(message))
        for client in list(self.authenticated_clients):
            try:
                await client.send(payload)
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # Pane Output Streaming
    # -------------------------------------------------------------------------

    def resolve_agent_id(self, raw_id: str) -> str:
        """Resolve a bare pane_id to its composite agent_id when it uniquely
        matches a known agent; pass composite ids through unchanged."""
        raw_id = str(raw_id or "").strip()
        if not raw_id or ":" in raw_id:
            return raw_id
        matches = [
            aid for aid, agent in self.agents_state.items()
            if str(agent.get("pane_id")) == raw_id or aid.endswith(":" + raw_id)
        ]
        if len(matches) == 1:
            return matches[0]
        return build_agent_id("local", "default", raw_id)

    async def push_pane_output(self, agent_id: str, force: bool = False) -> None:
        """Capture a pane's output and push it to subscribed clients when changed."""
        # Serialize concurrent captures per pane (forced subscribe push vs.
        # stream loop tick) so clients never receive duplicate frames.
        lock = self.output_locks.setdefault(agent_id, asyncio.Lock())
        async with lock:
            await self._capture_and_push(agent_id, force=force)

    async def _capture_and_push(self, agent_id: str, force: bool = False) -> None:
        subscribers = [
            client for client, ids in list(self.output_subs.items())
            if agent_id in ids and client in self.authenticated_clients
        ]
        if not subscribers:
            return

        host, _workspace, pane_id = parse_agent_id(agent_id)
        code, out, err = await self.execute_herdr_cmd(
            ["pane", "read", pane_id, "--lines", str(CONFIG["output_lines"]), "--format", "ansi"],
            host=host,
        )

        if code != 0:
            # Only surface capture errors on explicit subscription, not every tick
            if force:
                err_msg = json.dumps(scrub_dict({
                    "type": "pane_output",
                    "agent_id": agent_id,
                    "host": host,
                    "pane_id": pane_id,
                    "format": "ansi",
                    "data": "",
                    "full": True,
                    "error": err or f"herdr exited with code {code}",
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                }))
                for client in subscribers:
                    try:
                        await client.send(err_msg)
                    except Exception:
                        pass
            return

        digest = hashlib.sha1(out.encode("utf-8", errors="replace")).hexdigest()
        if not force and self.output_cursors.get(agent_id) == digest:
            return  # No change since last push
        self.output_cursors[agent_id] = digest

        payload = json.dumps(scrub_dict({
            "type": "pane_output",
            "agent_id": agent_id,
            "host": host,
            "pane_id": pane_id,
            "format": "ansi",
            "data": out,
            "full": True,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }))
        for client in subscribers:
            try:
                await client.send(payload)
            except Exception:
                pass

    async def stream_pane_outputs(self) -> None:
        """Background loop pushing fresh terminal output to subscribed clients.

        herdr exposes output via CLI capture rather than a stream API, so the
        relay polls subscribed panes at OUTPUT_INTERVAL and only transmits when
        the captured content hash changes.
        """
        while self.running:
            try:
                targets: Set[str] = set()
                for client, ids in list(self.output_subs.items()):
                    if client in self.authenticated_clients:
                        targets.update(ids)
                for agent_id in targets:
                    await self.push_pane_output(agent_id)
            except Exception as err:
                logger.debug(f"Output stream loop error: {err}")
            await asyncio.sleep(CONFIG["output_interval"])

    async def _resolve_pane_pid(self, pane_id: str) -> Optional[int]:
        """Resolve the innermost foreground process pid for a pane, cached
        for PID_CACHE_TTL so this doesn't add a `herdr` shell-out per poll
        cycle. Used to disambiguate multiple sessions sharing one cwd."""
        now = asyncio.get_event_loop().time()
        cached = self._pid_cache.get(pane_id)
        if cached and cached[0] > now:
            return cached[1]

        pid: Optional[int] = None
        try:
            code, out, _ = await self.execute_herdr_cmd(["pane", "process-info", "--pane", pane_id], host="local")
            if code == 0 and out.strip():
                envelope = json.loads(out)
                procs = envelope.get("result", {}).get("process_info", {}).get("foreground_processes", [])
                if procs and isinstance(procs, list):
                    pid = procs[-1].get("pid")  # innermost/most-specific process
        except Exception as err:
            logger.debug(f"pane process-info lookup failed for {pane_id}: {err}")

        self._pid_cache[pane_id] = (now + self.PID_CACHE_TTL, pid)
        return pid

    async def _enrich_local_agent(self, p: Dict[str, Any]) -> None:
        """Mutate a raw `herdr agent list` entry in-place with locally-probed
        identity/runway fields (git, harness session files). Reporter-posted
        fields (via /event or UDP) always win on merge -- this only fills what
        the zero-config polling path can see on its own. Never raises: a probe
        failure just leaves those fields absent for this poll cycle."""
        cwd = p.get("cwd") or p.get("foreground_cwd") or ""
        pane_id = p.get("pane_id") or p.get("paneId") or p.get("pane") or "0"
        workspace_id = p.get("workspace_id") or p.get("workspace") or "default"
        agent_id = build_agent_id("local", str(workspace_id), str(pane_id))
        harness = p.get("harness") or (p.get("agent") if isinstance(p.get("agent"), str) else "") or ""
        pid = p.get("pid") or await self._resolve_pane_pid(str(pane_id))

        try:
            found = probe_enrich(agent_id, cwd, pid, harness)
        except Exception as err:
            logger.debug(f"enrichment failed for {agent_id}: {err}")
            return

        for k, v in found.items():
            p.setdefault(k, v)

    async def update_agent_status(self, raw_event: Dict[str, Any], source: str = "hook") -> Dict[str, Any]:
        """Process an agent event, update state, trigger alerts if status changed, and broadcast."""
        async with self.lock:
            msg = complete_agent_update_message(raw_event, current=self.agents_state)
            agent = msg["agent"]
            agent_id = agent["id"]
            prev_status = self.agents_state.get(agent_id, {}).get("status", "unknown")
            new_status = agent.get("status", "unknown")

            apply_agent_message(self.agents_state, msg)

        # Broadcast to all live UI dashboard clients
        await self.broadcast_state(msg)

        # Trigger notification if agent transitions to blocked or done
        if prev_status != new_status:
            logger.info(f"Agent state changed: {agent_id} -> {new_status} ({agent.get('status_reason', '')})")
            audit_log(
                action="agent_state_transition",
                pane_id=agent.get("pane_id", ""),
                client=source,
                details={"agent_id": agent_id, "prev": prev_status, "new": new_status, "reason": agent.get("status_reason", "")},
            )

            if new_status == "blocked":
                title = f"Agent Blocked: {agent.get('agent_name', 'Agent')}"
                body = agent.get("status_reason") or f"Agent in pane {agent.get('pane_id')} requires user action."
                await push_manager.notify_all(title=title, body=body, pane_id=agent.get("pane_id", ""), status=new_status)
                await notify_telegram(title=title, text=body, pane_id=agent.get("pane_id", ""))
            elif new_status == "done":
                title = f"Task Done: {agent.get('agent_name', 'Agent')}"
                body = agent.get("last_message") or f"Agent in pane {agent.get('pane_id')} has completed its task."
                await push_manager.notify_all(title=title, body=body, pane_id=agent.get("pane_id", ""), status=new_status)

        return msg

    # -------------------------------------------------------------------------
    # CLI and SSH Execution
    # -------------------------------------------------------------------------

    async def execute_herdr_cmd(self, args: List[str], host: str = "local") -> Tuple[int, str, str]:
        """Execute a herdr CLI command locally or over SSH."""
        if not host or host == "local":
            cmd = ["herdr"] + args
        else:
            cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, "herdr"] + args

        try:
            proc = await asyncio.create_subprocess_exec(
                cmd[0],
                *cmd[1:],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15.0)
            return proc.returncode or 0, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")
        except FileNotFoundError:
            return 127, "", "herdr binary not found in PATH"
        except asyncio.TimeoutError:
            return 124, "", "Command execution timed out after 15s"
        except Exception as err:
            return 1, "", str(err)

    async def handle_client_action(self, action: str, params: Dict[str, Any], client_ip: str = "") -> Dict[str, Any]:
        """Process actions requested by web clients or Telegram (prompt, approve, reject, read, etc.)."""
        pane_id = str(params.get("pane_id") or params.get("pane") or "")
        host = str(params.get("host") or "local")

        # Resolve composite agent_id ("host:workspace:pane_id") when explicit
        # pane_id/host fields are absent. This is the schema used by the web
        # dashboard and documented client command payloads.
        agent_id = params.get("agent_id") or params.get("id")
        if agent_id and not pane_id:
            parsed_host, _workspace, parsed_pane = parse_agent_id(str(agent_id))
            pane_id = parsed_pane
            if not params.get("host"):
                host = parsed_host

        audit_log(action=action, pane_id=pane_id, ip=client_ip, details=params)

        if action == "prompt":
            text = str(params.get("text") or params.get("prompt") or "")
            code, out, err = await self.execute_herdr_cmd(["agent", "prompt", pane_id, text], host=host)
            if code != 0:
                code, out, err = await self.execute_herdr_cmd(["pane", "run", pane_id, text], host=host)
            return {"success": code == 0, "output": out, "error": err}

        elif action in ("send_keys", "send_text", "respond"):
            keys = str(params.get("keys") or params.get("text") or "")
            code, out, err = await self.execute_herdr_cmd(["agent", "send-keys", pane_id, keys], host=host)
            if code != 0:
                code, out, err = await self.execute_herdr_cmd(["pane", "send-keys", pane_id, keys], host=host)
            return {"success": code == 0, "output": out, "error": err}

        elif action == "approve":
            code, out, err = await self.execute_herdr_cmd(["agent", "approve", pane_id], host=host)
            if code != 0:
                code, out, err = await self.execute_herdr_cmd(["pane", "send-keys", pane_id, "y\n"], host=host)
            return {"success": code == 0, "output": out, "error": err}

        elif action == "reject":
            code, out, err = await self.execute_herdr_cmd(["agent", "reject", pane_id], host=host)
            if code != 0:
                code, out, err = await self.execute_herdr_cmd(["pane", "send-keys", pane_id, "\x1b"], host=host)
            return {"success": code == 0, "output": out, "error": err}

        elif action == "interrupt":
            code, out, err = await self.execute_herdr_cmd(["pane", "send-keys", pane_id, "\x03"], host=host)
            return {"success": code == 0, "output": out, "error": err}

        elif action in ("read_pane", "read_output"):
            fmt = str(params.get("format") or "ansi")
            lines = str(params.get("lines") or "150")
            cmd = ["pane", "read", pane_id, "--lines", lines]
            if fmt == "ansi":
                cmd.extend(["--format", "ansi"])
            code, out, err = await self.execute_herdr_cmd(cmd, host=host)
            return {"success": code == 0, "output": out, "error": err, "pane_id": pane_id}

        elif action == "focus_pane":
            code, out, err = await self.execute_herdr_cmd(["pane", "focus", pane_id], host=host)
            return {"success": code == 0, "output": out, "error": err}

        elif action == "list_workspaces":
            code, out, err = await self.execute_herdr_cmd(["workspace", "list"], host=host)
            return {"success": code == 0, "output": out, "error": err}

        elif action == "list_panes":
            code, out, err = await self.execute_herdr_cmd(["pane", "list"], host=host)
            return {"success": code == 0, "output": out, "error": err}

        return {"success": False, "error": f"Unknown action: {action}"}

    # -------------------------------------------------------------------------
    # WebSocket Connection Handler
    # -------------------------------------------------------------------------

    async def ws_handler(self, websocket: ServerConnection) -> None:
        """Handle active WebSocket connection from web dashboard."""
        self.ws_clients.add(websocket)
        peer = websocket.remote_address
        client_ip = peer[0] if peer else "unknown"

        is_authed = not bool(CONFIG["token"])
        if is_authed:
            self.authenticated_clients.add(websocket)

        try:
            if is_authed:
                async with self.lock:
                    snapshot = agents_snapshot_message(self.agents_state)
                await websocket.send(json.dumps(scrub_dict(snapshot)))

            async for message_raw in websocket:
                try:
                    data = json.loads(message_raw)
                except Exception:
                    continue

                msg_type = data.get("type", "")

                if msg_type == "auth":
                    token = data.get("token", "")
                    if self.is_token_valid(token):
                        is_authed = True
                        self.authenticated_clients.add(websocket)
                        audit_log(action="auth_success", ip=client_ip, client="websocket")
                        await websocket.send(json.dumps({"type": "auth_ok"}))
                        async with self.lock:
                            snapshot = agents_snapshot_message(self.agents_state)
                        await websocket.send(json.dumps(scrub_dict(snapshot)))
                    else:
                        audit_log(action="auth_failed", ip=client_ip, client="websocket")
                        await websocket.send(json.dumps({"type": "auth_error", "message": "Invalid token"}))
                    continue

                if not is_authed:
                    await websocket.send(json.dumps({"type": "auth_required", "message": "Authentication required"}))
                    continue

                if msg_type == "ping":
                    resp = {"type": "pong"}
                    if "id" in data:
                        resp["id"] = data["id"]
                    await websocket.send(json.dumps(resp))

                elif msg_type in ("get_agents", "agents", "subscribe"):
                    async with self.lock:
                        snapshot = agents_snapshot_message(self.agents_state)
                    await websocket.send(json.dumps(scrub_dict(snapshot)))

                elif msg_type == "subscribe_push":
                    sub = data.get("subscription")
                    if isinstance(sub, dict):
                        ok = push_manager.add_subscription(sub)
                        await websocket.send(json.dumps({"type": "push_subscribed", "success": ok}))

                elif msg_type == "unsubscribe_push":
                    endpoint = data.get("endpoint")
                    if endpoint:
                        push_manager.remove_subscription(str(endpoint))
                        await websocket.send(json.dumps({"type": "push_unsubscribed", "success": True}))

                elif msg_type == "subscribe_output":
                    raw_id = str(data.get("agent_id") or data.get("pane_id") or data.get("id") or "")
                    if raw_id:
                        agent_id = self.resolve_agent_id(raw_id)
                        self.output_subs.setdefault(websocket, set()).add(agent_id)
                        await websocket.send(json.dumps({
                            "type": "output_subscribed",
                            "agent_id": agent_id,
                            "interval": CONFIG["output_interval"],
                        }))
                        # Immediate capture so the client doesn't wait a cycle
                        asyncio.create_task(self.push_pane_output(agent_id, force=True))

                elif msg_type == "unsubscribe_output":
                    raw_id = str(data.get("agent_id") or data.get("pane_id") or data.get("id") or "")
                    subs = self.output_subs.get(websocket)
                    if subs is not None:
                        subs.discard(self.resolve_agent_id(raw_id) if raw_id else "")
                        if not raw_id:
                            subs.clear()
                    await websocket.send(json.dumps({"type": "output_unsubscribed", "agent_id": raw_id}))

                elif msg_type in ("prompt", "send_keys", "send_text", "respond", "approve", "reject", "interrupt", "read_pane", "read_output", "focus_pane", "list_workspaces", "list_panes"):
                    result = await self.handle_client_action(msg_type, data, client_ip=client_ip)
                    resp = {"type": f"{msg_type}_result", **result}
                    await websocket.send(json.dumps(resp))

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.ws_clients.discard(websocket)
            self.authenticated_clients.discard(websocket)
            self.output_subs.pop(websocket, None)

    # -------------------------------------------------------------------------
    # Front Multiplexer (HTTP + WebSocket Routing on same port)
    # -------------------------------------------------------------------------

    async def handle_front_connection(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        """Multiplex HTTP endpoints and WebSocket upgrades on the public relay port."""
        peer = client_writer.get_extra_info("peername")
        client_ip = peer[0] if peer else "127.0.0.1"

        buffer = b""
        while b"\r\n\r\n" not in buffer:
            chunk = await client_reader.read(1024)
            if not chunk:
                break
            buffer += chunk
            if len(buffer) > 65536:
                break

        if not buffer:
            client_writer.close()
            return

        header_part, _, body_initial = buffer.partition(b"\r\n\r\n")
        header_lines = header_part.decode("utf-8", errors="ignore").split("\r\n")
        req_line = header_lines[0] if header_lines else ""

        parts = req_line.split(" ")
        method = parts[0].upper() if len(parts) > 0 else "GET"
        raw_path = parts[1] if len(parts) > 1 else "/"

        parsed_url = urllib.parse.urlparse(raw_path)
        path = parsed_url.path
        query_params = urllib.parse.parse_qs(parsed_url.query)

        headers: Dict[str, str] = {}
        for line in header_lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        origin = headers.get("origin")
        if not self.is_origin_trusted(origin):
            logger.warning(f"Untrusted origin rejected: {origin} from {client_ip}")
            audit_log(action="untrusted_origin_rejected", ip=client_ip, details={"origin": origin})
            resp = b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nContent-Length: 26\r\n\r\nForbidden: Untrusted Origin"
            client_writer.write(resp)
            await client_writer.drain()
            client_writer.close()
            return

        # CORS Preflight
        if method == "OPTIONS":
            resp_headers = [
                b"HTTP/1.1 204 No Content",
                f"Access-Control-Allow-Origin: {origin or '*'}".encode("utf-8"),
                b"Access-Control-Allow-Methods: GET, POST, OPTIONS",
                b"Access-Control-Allow-Headers: Authorization, Content-Type, X-Relay-Token",
                b"Access-Control-Max-Age: 86400",
                b"Content-Length: 0",
                b"",
                b"",
            ]
            client_writer.write(b"\r\n".join(resp_headers))
            await client_writer.drain()
            client_writer.close()
            return

        # WebSocket Upgrade request
        if headers.get("upgrade", "").lower() == "websocket":
            try:
                ws_reader, ws_writer = await asyncio.open_connection("127.0.0.1", self.internal_ws_port)
                ws_writer.write(buffer)
                await ws_writer.drain()

                async def pipe(src, dst):
                    try:
                        while True:
                            data = await src.read(4096)
                            if not data:
                                break
                            dst.write(data)
                            await dst.drain()
                    except Exception:
                        pass
                    finally:
                        try:
                            dst.close()
                        except Exception:
                            pass

                t1 = asyncio.create_task(pipe(client_reader, ws_writer))
                t2 = asyncio.create_task(pipe(ws_reader, client_writer))
                await asyncio.gather(t1, t2)
            except Exception as err:
                logger.error(f"WebSocket forward error: {err}")
                client_writer.close()
            return

        # Standard HTTP Endpoints
        content_length = int(headers.get("content-length", 0))
        body = body_initial
        if len(body) < content_length:
            try:
                body += await asyncio.wait_for(client_reader.readexactly(content_length - len(body)), timeout=5.0)
            except Exception:
                pass

        authed = self.check_auth(headers, query_params)

        status_code = 200
        content_type = "application/json"
        resp_data: Dict[str, Any] = {}

        if method == "GET" and path in ("/health", "/healthz", "/api/health"):
            resp_data = {
                "status": "ok",
                "service": "herdr-outpost-relay",
                "version": "0.1.0",
                "agents_count": len(self.agents_state),
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }

        elif method == "GET" and path == "/":
            resp_data = {
                "service": "herdr-outpost-relay",
                "status": "running",
                "authenticated": authed,
                "endpoints": ["/health", "/event", "/api/action", "/push/subscribe", "/push/vapid-key"],
            }

        elif method == "GET" and path == "/push/vapid-key":
            resp_data = {"publicKey": CONFIG["vapid_public_key"]}

        elif method == "POST" and path in ("/event", "/api/event"):
            if not authed:
                status_code = 401
                resp_data = {"error": "Unauthorized"}
            else:
                try:
                    payload = json.loads(body.decode("utf-8")) if body else {}
                    updated = await self.update_agent_status(payload, source=f"http:{client_ip}")
                    resp_data = {"status": "accepted", "agent": updated.get("agent")}
                except Exception as ex:
                    status_code = 400
                    resp_data = {"error": f"Invalid JSON payload: {ex}"}

        elif method == "POST" and path in ("/api/action", "/action"):
            if not authed:
                status_code = 401
                resp_data = {"error": "Unauthorized"}
            else:
                try:
                    payload = json.loads(body.decode("utf-8")) if body else {}
                    action = payload.get("action") or payload.get("type", "")
                    resp_data = await self.handle_client_action(action, payload, client_ip=client_ip)
                except Exception as ex:
                    status_code = 400
                    resp_data = {"error": str(ex)}

        elif method == "POST" and path == "/push/subscribe":
            try:
                sub_payload = json.loads(body.decode("utf-8")) if body else {}
                ok = push_manager.add_subscription(sub_payload)
                resp_data = {"success": ok}
            except Exception as ex:
                status_code = 400
                resp_data = {"error": str(ex)}

        elif method == "POST" and path == "/push/unsubscribe":
            try:
                sub_payload = json.loads(body.decode("utf-8")) if body else {}
                endpoint = sub_payload.get("endpoint", "")
                push_manager.remove_subscription(endpoint)
                resp_data = {"success": True}
            except Exception as ex:
                status_code = 400
                resp_data = {"error": str(ex)}

        else:
            status_code = 404
            resp_data = {"error": "Not Found"}

        resp_bytes = json.dumps(resp_data).encode("utf-8")
        status_text = "OK" if status_code == 200 else ("Created" if status_code == 201 else ("Forbidden" if status_code == 403 else ("Unauthorized" if status_code == 401 else "Not Found")))

        resp_lines = [
            f"HTTP/1.1 {status_code} {status_text}".encode("utf-8"),
            f"Content-Type: {content_type}".encode("utf-8"),
            f"Content-Length: {len(resp_bytes)}".encode("utf-8"),
            f"Access-Control-Allow-Origin: {origin or '*'}".encode("utf-8"),
            b"Access-Control-Allow-Headers: Authorization, Content-Type, X-Relay-Token",
            b"Connection: close",
            b"",
            resp_bytes,
        ]
        # resp_lines ends with [b"", b"", resp_bytes]; join only the header
        # lines, then terminate with a single blank line before the body.
        resp_head = b"\r\n".join(resp_lines[:-3])
        client_writer.write(resp_head + b"\r\n\r\n" + resp_bytes)
        await client_writer.drain()
        client_writer.close()
        await client_writer.wait_closed()

    # -------------------------------------------------------------------------
    # UDP Event Listener Protocol
    # -------------------------------------------------------------------------

    class UDPProtocol(asyncio.DatagramProtocol):
        def __init__(self, daemon: HerdrRelayDaemon):
            self.daemon = daemon

        def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
            try:
                payload = json.loads(data.decode("utf-8"))
                asyncio.create_task(self.daemon.update_agent_status(payload, source=f"udp:{addr[0]}"))
            except Exception as err:
                logger.debug(f"UDP datagram parse error: {err}")

    # -------------------------------------------------------------------------
    # Background Herdr Polling Loop
    # -------------------------------------------------------------------------

    async def poll_herdr_agents(self) -> None:
        """Periodically query herdr agent list locally and across configured SSH remotes."""
        hosts = ["local"] + CONFIG["remotes"]

        while self.running:
            try:
                for host in hosts:
                    code, out, _ = await self.execute_herdr_cmd(["agent", "list"], host=host)
                    if code == 0 and out.strip():
                        try:
                            envelope = json.loads(out)
                            agents = envelope.get("result", {}).get("agents", []) if isinstance(envelope, dict) else []
                            for p in agents:
                                if isinstance(p, dict):
                                    p["host"] = host
                                    if host == "local":
                                        await self._enrich_local_agent(p)
                                    await self.update_agent_status(p, source=f"poll:{host}")
                        except json.JSONDecodeError:
                            pass
            except Exception as err:
                logger.debug(f"Poll loop error: {err}")

            await asyncio.sleep(CONFIG["poll_interval"])

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def start(self) -> None:
        """Start internal WS server, public front multiplexer, and background tasks."""
        self.running = True
        logger.info(f"Starting herdr-outpost relay on {CONFIG['host']}:{CONFIG['port']}...")
        logger.info(f"Log directory: {CONFIG['log_dir']}")
        if CONFIG["token"]:
            logger.info("Token authentication enabled.")
        if CONFIG["trusted_origins"]:
            logger.info(f"Trusted origins: {CONFIG['trusted_origins']}")

        self.ws_server = await ws_serve(self.ws_handler, "127.0.0.1", self.internal_ws_port)
        self.front_server = await asyncio.start_server(
            self.handle_front_connection,
            CONFIG["host"],
            CONFIG["port"],
        )

        loop = asyncio.get_running_loop()
        try:
            self.udp_transport, _ = await loop.create_datagram_endpoint(
                lambda: self.UDPProtocol(self),
                local_addr=(CONFIG["host"], CONFIG["port"]),
            )
            logger.info(f"UDP event listener active on {CONFIG['host']}:{CONFIG['port']}")
        except Exception as err:
            logger.warning(f"Could not bind UDP listener: {err}")

        asyncio.create_task(self.poll_herdr_agents())
        asyncio.create_task(self.stream_pane_outputs())
        logger.info(f"herdr-outpost relay successfully listening on {CONFIG['host']}:{CONFIG['port']}")

    async def stop(self) -> None:
        """Stop all servers and gracefully terminate."""
        self.running = False
        logger.info("Stopping herdr-outpost relay...")
        if self.front_server:
            self.front_server.close()
            await self.front_server.wait_closed()
        if self.ws_server:
            self.ws_server.close()
            await self.ws_server.wait_closed()
        if self.udp_transport:
            self.udp_transport.close()
        logger.info("herdr-outpost relay stopped.")


async def main() -> None:
    daemon = HerdrRelayDaemon()
    await daemon.start()

    stop_event = asyncio.Event()

    def handle_signal():
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            pass

    await stop_event.wait()
    await daemon.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
