# AGENTS.md — herdr-outpost AI Agent Guidelines

Welcome to `herdr-outpost`. This document defines operating rules, component boundaries, and workflows for AI agents and subagents working within this repository.

---

## Project Overview

`herdr-outpost` is a lightweight, remote dashboard and relay gateway for [Herdr](https://herdr.dev) (the terminal workspace manager for AI coding agents). The reference deployment hosts it via Cloudflare Workers and Cloudflare Tunnel under custom domains (e.g. `herdr.example.com` and `relay.example.com`); other static hosts and process runners work natively too — see [Alternative Deployment Platforms](README.md#-alternative-deployment-platforms).

### Core Architecture

- **`relay/`**: Python async daemon bridging the local `herdr` socket/CLI and optional remote SSH hosts to WebSocket & HTTP clients. Includes authentication, origin checks, audit logging, push event consumption, and optional Telegram bot.
- **`web/`**: Modern, zero-build web dashboard deployable to Cloudflare Workers (or any static host). Streams live terminal outputs, agent status, and interactive controls (prompt, approve, reject, interrupt).
- **`config/`**: Example configurations for Cloudflare Tunnel ingress and environment secrets.
- **`tests/`**: Unit and integration tests for state management, relay protocols, and client payloads.

---

## Parallel Subagent Operating Rules

To maximize efficiency and eliminate merge conflicts when multiple agents work concurrently:

### 1. Workstream Decomposition
When executing non-trivial features or refactors, break work into independent, non-overlapping domains:
- **Backend / Relay Specialist**: Focuses strictly on `relay/` files, Python logic, socket IPC, daemon scripts.
- **Frontend / UI Specialist**: Focuses strictly on `web/` files, HTML/CSS/JS, responsive layouts, ANSI rendering, Web Push.
- **DevOps / Docs Specialist**: Focuses on `config/`, `README.md`, `SCAFFOLD.md`, and Cloudflare configurations.
- **QA / Test Specialist**: Focuses on `tests/`, test harness, and validation scripts.

### 2. Isolation & File Ownership
- Each subagent MUST only create or modify files in its designated area.
- Shared interfaces (e.g., WebSocket message schema in `relay/agent_state.py`) should be defined first before downstream implementation.

### 3. Verification Before Completion
- Every subagent must verify its own changes before reporting completion (e.g., running `uv run pytest`, validating shell syntax with `bash -n`, checking HTML/JS validity).

---

## Technical Standards & Conventions

### Branding & Naming
- Project name: **`herdr-outpost`** (never use `herdr-remote` in new docs, code, or configs).
- Environment variables: Use `HERDR_OUTPOST_*` with fallback to `HERDR_*` for backward compatibility.
- Log directories: `~/.local/state/herdr-outpost/log` (Linux), `~/Library/Logs/herdr-outpost` (macOS), `%LOCALAPPDATA%\herdr-outpost\logs` (Windows).

### Python & Relay Guidelines
- Python runner: `uv` (PEP 723 inline script metadata and `pyproject.toml`).
- Use standard asynchronous libraries (`websockets`, `asyncio`).
- **Security & Secret Scrubbing**: All logs, exceptions, and outbound messages MUST scrub bearer tokens and credentials using the central `scrub()` helper. Never log raw URLs containing query parameters with tokens.
- **Strict Origin Validation**: Relay WebSocket connections must validate `Origin` against `HERDR_TRUSTED_ORIGINS`.

### Web Frontend Guidelines
- Vanilla HTML/CSS/JavaScript with responsive layout for mobile and desktop.
- Dark & light mode support based on system preference and user toggle.
- ANSI terminal rendering for high fidelity output from `herdr pane read --format ansi`.
- Audio/haptic cues for state transitions (e.g., agent becomes `blocked` or `done`).

---

## Useful Commands

```bash
# Run tests
uv run --with pytest --with pytest-asyncio pytest tests/

# Start relay daemon locally
cd relay && ./start.sh

# Run health check
relay/health-check.sh

# Validate shell scripts
bash -n relay/*.sh tests/*.sh
```
