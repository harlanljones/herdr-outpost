#!/usr/bin/env python3
"""herdr-outpost Telegram Bot.

Provides remote monitoring, interactive approval buttons, status views,
and command execution for herdr-outpost agents via Telegram.
"""

from __future__ import annotations

import asyncio
import datetime
import html
import json
import logging
import os
import re
import sys
import urllib.request
from typing import Any, Dict, List, Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

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


BOT_TOKEN = get_env("TELEGRAM_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT_IDS = [
    c.strip()
    for c in (get_env("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")).split(",")
    if c.strip()
]
RELAY_HOST = get_env("RELAY_HOST", "127.0.0.1")
RELAY_PORT = int(get_env("RELAY_PORT", "8375"))
RELAY_TOKEN = get_env("RELAY_TOKEN", "")
RELAY_URL = f"http://{RELAY_HOST}:{RELAY_PORT}"

# -----------------------------------------------------------------------------
# Secret Scrubbing
# -----------------------------------------------------------------------------

def scrub(text: str) -> str:
    """Scrub tokens and secrets from text."""
    if not isinstance(text, str):
        text = str(text)
    if BOT_TOKEN:
        text = text.replace(BOT_TOKEN, "[REDACTED]")
    if RELAY_TOKEN:
        text = text.replace(RELAY_TOKEN, "[REDACTED]")
    text = re.sub(r"(token|bearer|auth)=([^\s&\"']+)", r"\1=[REDACTED]", text, flags=re.IGNORECASE)
    return text


# Strip ANSI escape sequences for Telegram plain text presentation
ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub("", text)


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------

logging.basicConfig(
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("herdr-telegram")


# -----------------------------------------------------------------------------
# Relay API Client
# -----------------------------------------------------------------------------

def query_relay(endpoint: str, method: str = "GET", payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Synchronous HTTP call to the herdr-outpost relay with auth headers."""
    url = f"{RELAY_URL}{endpoint}"
    headers = {"Content-Type": "application/json"}
    if RELAY_TOKEN:
        headers["Authorization"] = f"Bearer {RELAY_TOKEN}"

    data_bytes = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body.strip() else {"status": "ok"}
    except Exception as err:
        return {"error": scrub(str(err))}


async def call_relay_async(endpoint: str, method: str = "GET", payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, query_relay, endpoint, method, payload)


# -----------------------------------------------------------------------------
# Authorization Check
# -----------------------------------------------------------------------------

def is_authorized(update: Update) -> bool:
    if not ALLOWED_CHAT_IDS:
        return True
    chat = update.effective_chat
    if not chat:
        return False
    return str(chat.id) in ALLOWED_CHAT_IDS


# -----------------------------------------------------------------------------
# Bot Command Handlers
# -----------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("Unauthorized.")
        return

    text = (
        "*herdr-outpost Relay Bot*\n\n"
        "Control and monitor your AI coding agents:\n\n"
        "• `/status` or `/agents` — View active agents and statuses\n"
        "• `/panes` — List all workspaces and terminal panes\n"
        "• `/read <pane_id>` — Read recent output from pane\n"
        "• `/prompt <pane_id> <message>` — Send instructions\n"
        "• `/approve <pane_id>` — Approve pending action\n"
        "• `/reject <pane_id>` — Reject pending action\n"
        "• `/interrupt <pane_id>` — Interrupt running agent\n"
        "• `/digest` — Daily summary of agent tasks\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("Unauthorized.")
        return

    res = await call_relay_async("/health")
    if "error" in res:
        await update.message.reply_text(f"[ERROR] Could not connect to relay: `{res['error']}`", parse_mode=ParseMode.MARKDOWN)
        return

    # Query panes from relay
    action_res = await call_relay_async("/api/action", method="POST", payload={"action": "list_panes"})
    output = action_res.get("output", "")

    agents_list = []
    if output:
        try:
            agents_list = json.loads(output)
        except Exception:
            pass

    if not agents_list:
        msg = f"*Relay Active*\nAgents count: `{res.get('agents_count', 0)}`\n\n_No active agents reported._"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        return

    msg = f"*Agent Status* ({len(agents_list)} active):\n\n"
    buttons = []

    for ag in agents_list:
        pane_id = ag.get("pane_id") or ag.get("id", "?")
        status = ag.get("status", "unknown").lower()
        reason = ag.get("status_reason") or ag.get("reason", "")
        name = ag.get("agent_name") or ag.get("name", "Agent")

        emoji = "⚪"
        if status == "blocked":
            emoji = "PENDING"
        elif status == "working":
            emoji = "🔵"
        elif status == "done":
            emoji = "OK"

        msg += f"{emoji} *Pane {pane_id}* ({name}): `{status.upper()}`\n"
        if reason:
            msg += f"   _{html.escape(reason[:80])}_\n"

        if status == "blocked":
            buttons.append([
                InlineKeyboardButton(f"Approve #{pane_id}", callback_data=f"app:{pane_id}"),
                InlineKeyboardButton(f"Reject #{pane_id}", callback_data=f"rej:{pane_id}"),
                InlineKeyboardButton(f"📄 Output #{pane_id}", callback_data=f"read:{pane_id}"),
            ])

    reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)


async def cmd_panes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("Unauthorized.")
        return

    res = await call_relay_async("/api/action", method="POST", payload={"action": "list_panes"})
    out = res.get("output") or res.get("error") or "No output."
    clean_out = strip_ansi(out)
    if len(clean_out) > 3000:
        clean_out = clean_out[:3000] + "\n... (truncated)"
    await update.message.reply_text(f"*Panes:*\n```\n{clean_out}\n```", parse_mode=ParseMode.MARKDOWN)


async def cmd_read(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("Unauthorized.")
        return

    if not context.args:
        await update.message.reply_text("Usage: `/read <pane_id> [lines]`", parse_mode=ParseMode.MARKDOWN)
        return

    pane_id = context.args[0]
    lines = context.args[1] if len(context.args) > 1 else "50"

    res = await call_relay_async("/api/action", method="POST", payload={"action": "read_pane", "pane_id": pane_id, "lines": lines, "format": "plain"})
    out = res.get("output", "")
    if not out:
        out = res.get("error") or "(Empty output)"

    clean_out = strip_ansi(out)
    if len(clean_out) > 3500:
        clean_out = clean_out[-3500:]

    buttons = [
        [
            InlineKeyboardButton("Approve", callback_data=f"app:{pane_id}"),
            InlineKeyboardButton("Reject", callback_data=f"rej:{pane_id}"),
            InlineKeyboardButton("🔄 Refresh", callback_data=f"read:{pane_id}"),
        ]
    ]
    await update.message.reply_text(
        f"📄 *Pane {pane_id} Output:*\n```\n{clean_out}\n```",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def cmd_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("Unauthorized.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: `/prompt <pane_id> <message text>`", parse_mode=ParseMode.MARKDOWN)
        return

    pane_id = context.args[0]
    prompt_text = " ".join(context.args[1:])

    res = await call_relay_async("/api/action", method="POST", payload={"action": "prompt", "pane_id": pane_id, "text": prompt_text})
    if res.get("success"):
        await update.message.reply_text(f"Prompt sent to pane *{pane_id}*.", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"Error sending prompt: `{res.get('error')}`", parse_mode=ParseMode.MARKDOWN)


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("Unauthorized.")
        return

    if not context.args:
        await update.message.reply_text("Usage: `/approve <pane_id>`", parse_mode=ParseMode.MARKDOWN)
        return

    pane_id = context.args[0]
    res = await call_relay_async("/api/action", method="POST", payload={"action": "approve", "pane_id": pane_id})
    if res.get("success"):
        await update.message.reply_text(f"Approval sent to pane *{pane_id}*.", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"Error approving: `{res.get('error')}`", parse_mode=ParseMode.MARKDOWN)


async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("Unauthorized.")
        return

    if not context.args:
        await update.message.reply_text("Usage: `/reject <pane_id>`", parse_mode=ParseMode.MARKDOWN)
        return

    pane_id = context.args[0]
    res = await call_relay_async("/api/action", method="POST", payload={"action": "reject", "pane_id": pane_id})
    if res.get("success"):
        await update.message.reply_text(f"Rejection sent to pane *{pane_id}*.", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"Error rejecting: `{res.get('error')}`", parse_mode=ParseMode.MARKDOWN)


async def cmd_interrupt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("Unauthorized.")
        return

    if not context.args:
        await update.message.reply_text("Usage: `/interrupt <pane_id>`", parse_mode=ParseMode.MARKDOWN)
        return

    pane_id = context.args[0]
    res = await call_relay_async("/api/action", method="POST", payload={"action": "interrupt", "pane_id": pane_id})
    if res.get("success"):
        await update.message.reply_text(f"🛑 Interrupt sent to pane *{pane_id}*.", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"Error interrupting: `{res.get('error')}`", parse_mode=ParseMode.MARKDOWN)


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("Unauthorized.")
        return

    res = await call_relay_async("/health")
    count = res.get("agents_count", 0)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    text = (
        f"📅 *herdr-outpost Daily Digest*\n\n"
        f"• Status: `OPERATIONAL`\n"
        f"• Active Agent Panes: `{count}`\n"
        f"• Time: `{now}`\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# -----------------------------------------------------------------------------
# Callback Query Handler (Interactive Buttons)
# -----------------------------------------------------------------------------

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    if not is_authorized(update):
        await query.edit_message_text("Unauthorized.")
        return

    data = query.data or ""
    parts = data.split(":", 1)
    if len(parts) != 2:
        return

    action_prefix, pane_id = parts

    if action_prefix == "app":
        res = await call_relay_async("/api/action", method="POST", payload={"action": "approve", "pane_id": pane_id})
        msg = f"Approved pane *{pane_id}*." if res.get("success") else f"Error: {res.get('error')}"
        await query.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    elif action_prefix == "rej":
        res = await call_relay_async("/api/action", method="POST", payload={"action": "reject", "pane_id": pane_id})
        msg = f"Rejected pane *{pane_id}*." if res.get("success") else f"Error: {res.get('error')}"
        await query.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    elif action_prefix == "read":
        res = await call_relay_async("/api/action", method="POST", payload={"action": "read_pane", "pane_id": pane_id, "lines": "30", "format": "plain"})
        out = strip_ansi(res.get("output") or "(No output)")
        if len(out) > 3000:
            out = out[-3000:]
        await query.message.reply_text(f"📄 *Pane {pane_id} Output:*\n```\n{out}\n```", parse_mode=ParseMode.MARKDOWN)


# -----------------------------------------------------------------------------
# Application Setup & Runner
# -----------------------------------------------------------------------------

def main() -> None:
    if not BOT_TOKEN:
        print("Error: HERDR_OUTPOST_TELEGRAM_TOKEN or TELEGRAM_BOT_TOKEN is required.", file=sys.stderr)
        sys.exit(1)

    print(f"Starting herdr-outpost Telegram bot connecting to {RELAY_URL}...")
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("agents", cmd_status))
    app.add_handler(CommandHandler("panes", cmd_panes))
    app.add_handler(CommandHandler("read", cmd_read))
    app.add_handler(CommandHandler("prompt", cmd_prompt))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("reject", cmd_reject))
    app.add_handler(CommandHandler("interrupt", cmd_interrupt))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CallbackQueryHandler(handle_callback_query))

    app.run_polling()


if __name__ == "__main__":
    main()
