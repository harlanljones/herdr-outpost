# herdr-outpost relay

Async daemon relay and gateway for `herdr-outpost`.

## Features
- WebSocket server for live bidirectional streaming with the `herdr-outpost` web dashboard.
- Live pane output streaming: clients send `subscribe_output` with an `agent_id` (composite `host:workspace:pane` or a bare `pane_id`) and receive `pane_output` frames whenever the captured ANSI output changes (cadence via `HERDR_OUTPOST_OUTPUT_INTERVAL`, default 2s; `unsubscribe_output` stops the stream).
- On-demand pane capture via the `read_pane` action (`read_pane_result` response).
- HTTP and UDP event endpoints for `herdr` hooks and push plugins.
- Token-based authentication and strict origin validation.
- Central secret scrubbing in all logging and audits.
- Web Push notifications (VAPID) for blocked and completed agent tasks.
- Optional Telegram bot integration.
- Polling for local and remote (SSH) `herdr` workspaces and panes.
