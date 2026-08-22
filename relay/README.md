# herdr-outpost relay

Async daemon relay and gateway for `herdr-outpost` 1.0.0. The tested baseline is Herdr 0.8.2 and Python 3.10+.

## Run

Install Herdr on every local or SSH host the relay will poll. The relay needs no custom Herdr plugin; official harness integrations are recommended because they improve lifecycle and native session identity:

```bash
herdr integration install claude   # or codex, opencode, pi, etc.
herdr integration status
herdr agent list
```

Copy [`../config/config.env.example`](../config/config.env.example) to `~/.config/herdr-outpost/config.env`, set a strong `HERDR_OUTPOST_RELAY_TOKEN`, and restrict `HERDR_OUTPOST_TRUSTED_ORIGINS` to the dashboard origins. Then start or install the service:

```bash
./start.sh
# or
./install-service.sh
```

See the main [README](../README.md) for features and alternative platforms, or the [Cloudflare deployment guide](../SCAFFOLD.md) for the reference setup.

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
- **Alarm Dampening & Detection Health**: Poll-sourced `blocked` reports must persist across `HERDR_OUTPOST_BLOCKED_CONFIRM_POLLS` consecutive polls (default 2) before Web Push/Telegram alarms fire; hook/UDP reporters alarm immediately. When Herdr skips screen detection for an unregistered lifecycle session, the relay automatically re-evaluates recent pane output with Herdr's active screen manifest before confirming the block. The relay still records whether Herdr has a lifecycle session registered for each pane (`agent_session_registered`) so the dashboard can flag detection-health issues (see Troubleshooting).
- **Multi-Host Polling**: Polling across local and remote (SSH) `herdr` workspaces and panes.

## Troubleshooting

### Agents incorrectly show as BLOCKED

`herdr agent list` can report `blocked` for a pane whose lifecycle integration session
registration was lost. The unreliable signature is a `blocked` entry with
`screen_detection_skipped: true` and no `agent_session`: under Herdr's
[status-authority model](https://herdr.dev/docs/agents/#status-authority), the
lifecycle integration was authoritative, so Herdr did not also evaluate its
screen manifest.

The relay handles this signature automatically. It reads the pane's recent
plain-text detection snapshot and evaluates it with the detected agent's active
manifest using `herdr agent explain --file`. A manifest result of `working` or
`idle` clears the false block before status normalization, while `blocked`
preserves a genuine approval or permission prompt. Read or explain failures,
malformed output, and missing manifests fail safely by retaining the original
unverified block. Registered lifecycle sessions, non-skipped screen detection,
and hook/UDP events do not use this fallback.

To reproduce the screen evaluation manually, save a plain-text snapshot and use
Herdr's [file-based explain interface](https://herdr.dev/docs/cli-reference/#agents):

```bash
herdr pane read <pane_id> --source detection --format text > screen.txt
herdr agent explain --file screen.txt --agent <agent> --json
```

The relay keeps `agent_session_registered: false` even when the fallback
corrects the displayed state, preserving that detection-health signal. If the
fallback cannot classify the snapshot, the dashboard renders the retained block
with an `? UNVERIFIED` badge. To restore lifecycle authority upstream, send a
new prompt in that pane or restart the agent so its official integration can
register the native session again.
