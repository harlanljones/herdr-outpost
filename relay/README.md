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
- **Alarm Dampening & Detection Health**: Poll-sourced `blocked` reports must persist across `HERDR_OUTPOST_BLOCKED_CONFIRM_POLLS` consecutive polls (default 2) before Web Push/Telegram alarms fire; hook/UDP reporters alarm immediately. The relay also records whether herdr has a lifecycle session registered for each pane (`agent_session_registered`) so the dashboard can flag unverified block signals (see Troubleshooting).
- **Multi-Host Polling**: Polling across local and remote (SSH) `herdr` workspaces and panes.

## Troubleshooting

### Agents incorrectly show as BLOCKED

`herd agent list` (verified up through herdr 0.8.2) can report a persistent false
`blocked` for a pane whose lifecycle-hook session registration was lost: those
entries carry no `agent_session`, and under herdr's
`full_lifecycle_hook_authority` model the pane then surfaces as `blocked` even
while the agent is actively working. Confirm with:

```bash
herdr agent explain <pane_id>   # manifest: none / rule: none = no detection data
herdr pane read <pane_id> --lines 40
```

The relay surfaces this as `agent_session_registered: false`; the dashboard
renders an `? UNVERIFIED` badge on such blocks. To clear it upstream, send any
new prompt in that pane or restart the agent session so the harness plugin
re-registers its session (`chat.message` re-attaches the root session id).
