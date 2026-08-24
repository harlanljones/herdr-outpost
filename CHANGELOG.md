# Changelog

All notable changes to herdr-outpost are documented in this file.

## [Unreleased]

### Added

- Subagent tree view at `/tree` (header nav button, deep-linkable): each root
  agent renders its harness subagent sessions grouped by repo. Tap a node to
  expand inline activity detail; `Terminal →` opens the parent agent's session
  sheet. OpenCode trees come from its local session store (`session.parent_id`);
  Claude Code from transcript sidechains
  (`~/.claude/projects/<slug>/<sessionId>/subagents/`). cline and antigravity-cli
  expose no parent links and show childless.
- Relay schema additions in `relay/agent_state.py` (shared contract): `session_id`
  and nested `subagents[]` on every normalized agent; subagent `active` is a
  recent-writes activity signal, never a fabricated lifecycle status.

## [1.0.0] - 2026-08-22

First stable release of the remote dashboard and relay gateway for
[Herdr](https://herdr.dev).

### Added

- Zero-build, responsive dashboard with dark and light themes, path-based session
  deep links, ANSI terminal output, browser history navigation, and installable PWA
  support.
- Async relay for local and SSH-hosted Herdr workspaces with authenticated HTTP and
  WebSocket access, strict origin validation, audit logging, and secret scrubbing.
- Live agent status, lifecycle reconciliation, session controls, telemetry, and
  desktop, audio, haptic, Web Push, and optional Telegram notifications.
- Cloudflare Workers and Cloudflare Tunnel deployment support, alongside documented
  static-host and process-runner alternatives.
- Native Linux, macOS, and Windows relay launch paths plus service installation and
  health-check tooling.

### Changed

- Blocked-state notifications are confirmed and dampened to avoid transient alerts.
- Unregistered polls that skip screen detection are verified against Herdr's active
  screen manifest, correcting stale `blocked` results while preserving genuine
  permission prompts.
- Relay shutdown bounds long-lived front-end and WebSocket client closure so service
  restarts complete promptly during upgrades.
- Configuration, service names, and state paths use the `herdr-outpost` namespace;
  legacy Herdr environment variables and config paths remain accepted for backward
  compatibility.

[1.0.0]: https://github.com/harlanljones/herdr-outpost/releases/tag/v1.0.0
