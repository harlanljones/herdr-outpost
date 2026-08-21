# herdr-outpost relay

Async daemon relay and gateway for `herdr-outpost`.

## Features
- WebSocket server for live bidirectional streaming with the `herdr-outpost` web dashboard.
- **Session Lifecycle & Reconciliation**: Authoritative per-host polling reconciles active sessions against `herdr agent list` with a 2-miss grace period for clean teardown. Hook- and UDP-reported sessions expire after a configurable time-to-live (`HERDR_OUTPOST_SESSION_TTL`, default 90s). Emits `agent_removed` broadcasts on session termination.
- **Liveness & Origin Metadata**: Every agent model carries observation provenance (`source`, e.g. `poll:local`, `hook`) and precise UTC timestamp (`last_seen_at`).
- **Live Pane Output Streaming**: Clients send `subscribe_output` with an `agent_id` (composite `host:workspace:pane` or a bare `pane_id`) and receive `pane_output` frames whenever the captured ANSI output changes (cadence via `HERDR_OUTPOST_OUTPUT_INTERVAL`, default 2s; `unsubscribe_output` stops the stream).
- **On-Demand Pane Capture**: Triggered via the `read_pane` action (`read_pane_result` response).
- **Interactive Agent Control**: Execute prompt inputs, approvals, rejections, key presses, and task interrupts directly through WebSocket commands.
- **Event Ingress**: HTTP (`/api/event`, `/event`) and UDP endpoints for `herdr` hooks and in-pane probe reporters.
- **Health & Diagnostics**: `/health` (also `/healthz`, `/api/health`) endpoint returning overall agent count, per-host breakdowns (`agents_by_host`), and last reconciliation timestamp (`last_reconcile_at`).
- **Token-Based Authentication & Strict Origin Validation**: Constant-time verification against `HERDR_OUTPOST_RELAY_TOKEN` and origin filtering via `HERDR_OUTPOST_TRUSTED_ORIGINS`.
- **Central Secret Scrubbing**: Redacts bearer tokens and sensitive credentials across all logging, exceptions, and audit trails.
- **Web Push Notifications (VAPID)**: Push alerts for blocked and completed agent tasks.
- **Optional Telegram Bot Integration**: Alert pipeline and two-way status notifications via Telegram.
- **Multi-Host Polling**: Polling across local and remote (SSH) `herdr` workspaces and panes.
