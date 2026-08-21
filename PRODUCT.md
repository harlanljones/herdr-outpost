# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Static HTML/CSS/JS, zero build step, deployed to Cloudflare Workers via `web/wrangler.toml`
(`[assets] directory = "."`). Confirmed during shape: stay single-file (`web/index.html`).

## Users

The operator (Harlan) running several AI coding agents (Claude Code, cline, antigravity-cli)
concurrently across local repos via [herdr](https://herdr.dev), a terminal workspace manager.
The primary situation is checking on the fleet from a phone — mid-day, away from the desk —
to see which agent is blocked and needs a decision, and to unblock or reply without opening a
full terminal.

## Product Purpose

`herdr-outpost` is a remote dashboard and relay gateway that surfaces live agent state (working /
blocked / done / idle), streams terminal output, and lets the operator respond to blocked agents
from anywhere. Success is a fast, honest glance: which agents exist, what each is doing, whether
any need attention, and whether any is close to running out of context.

## Positioning

Unlike a generic remote-terminal or tmux-over-web tool, herdr-outpost is agent-aware: it
understands agent lifecycle states (blocked-on-approval vs. working vs. idle) and is built to
answer "does anything need me right now" at a glance, from a phone, across multiple concurrent
coding agents and multiple harnesses.

## Operating Context

- Agents run inside `herdr` panes on one machine (confirmed scale: 2–8 agents, one host; SSH
  remote hosts are supported by the relay but out of scope for this revamp's probes).
- Three harnesses in active use on this machine: Claude Code (`~/.claude/projects/*.jsonl`),
  cline (`~/.cline/data/sessions/*`), antigravity-cli (`~/.gemini/antigravity-cli/`).
- `herdr agent list` polled by the relay every 3s returns per-pane `agent` (harness), `cwd`,
  `foreground_cwd`, `terminal_title_stripped` (live task), `workspace_id`, `pane_id`.
- Every pane has `$HERDR_PANE_ID` in its environment — usable by an in-pane reporter for exact
  pane↔session identity.
- The relay (`relay/herdr_relay.py`) multiplexes HTTP + WebSocket on one port, plus a UDP event
  ingress (`relay/on_event.py`) fed by herdr plugin hooks. `relay/agent_state.py` is the single
  shared schema all transports normalize through (`normalize_agent_dict`).
- Deployment: Cloudflare Workers (static web) + Cloudflare Tunnel (relay), documented in
  `SCAFFOLD.md`; alternative static hosts and process runners also supported.

## Capabilities and Constraints

- Confirmed interaction model: monitor **and** full terminal control — live ANSI output plus
  arbitrary input to any agent, not read-only.
- Confirmed: unblocking an agent (approve/reject/reply) must be possible without opening the
  terminal — it's the primary phone action.
- Per-session context-window usage is directly readable for Claude Code and cline via their local
  session files. antigravity-cli does not expose a readable per-session context figure (session
  data is protobuf inside SQLite); it exposes quota-window usage instead via a collector already
  present on this machine (`~/.config/omarchy/plugins/mustafaokur.agent-leaderboard/collect-antigravity.py`).
  The UI must not fabricate a context percentage where none exists — show the honest metric
  available (context used/limit, or quota window %) or nothing.
- Git repo/branch is derivable locally via `git -C <cwd>` for any agent whose cwd is on this
  machine.
- The existing WebSocket message schema (`agent_update`, `agents_snapshot`) and its 13-field
  agent shape in `relay/agent_state.py` is the extension point — new fields must be added there
  first, per `AGENTS.md`'s "shared interface defined first" rule, then flow to relay callers and
  the web client.

## Brand Commitments

- Name: **herdr-outpost**. Existing logo asset: `web/logo.svg` (gradient mark, cyan → blue →
  violet, matching status colors). Kept as-is; the mark's use in the surface may be restyled, but
  the asset itself is not being redrawn as part of this brief.
- Footer previously linked the retired upstream repo name (`dcolinmorgan/herdr-remote`); that is
  a defect to fix, not a brand commitment to preserve.

## Evidence on Hand

- Live on this machine at plan time: 2 Claude Code agents in `herdr-outpost` (one on `main`),
  1 cline agent in `urban-development`, 1 cline agent in `latent-roast`. Real repo names, real
  branch, real task titles (from `terminal_title_stripped`) — usable as authentic demonstration
  content during the build rather than invented placeholder agents.
- `herdr agent list`, `herdr pane process-info`, `herdr pane get` JSON shapes captured directly
  from this machine (see shape-round tool output) — the ground truth for what the relay can poll
  without any new probe.

## Product Principles

1. **Identity over decoration.** What replaced the redundant "herdr-agent / default / local"
   triad must be real, distinguishing facts: harness, model, repo, branch, live task — not a
   different arrangement of the same emptiness.
2. **Honest instrumentation.** Never show a metric (context %, cost) the system cannot actually
   measure for that harness; label what's shown truthfully.
3. **Mobile is the primary client, not a breakpoint.** The phone is where blocked-agent triage
   actually happens; desktop is the secondary, higher-density case.
4. **Unblock without full context-switch.** The fastest path from "agent needs me" to "agent is
   moving again" must not require opening the terminal.
5. **Grouped by what the operator is working on** (repo), not by herdr's internal workspace ids,
   which carry no operator-facing meaning.

## Accessibility & Inclusion

Confirmed priority for this revamp (explicit ask: "prioritize mobile accessibility"). Binding
requirements carried into the surface brief: real pinch-zoom (no `user-scalable=no`),
`prefers-reduced-motion` support (current blocked-card pulse animation has no escape), status
conveyed by glyph + label in addition to color, 44×44px minimum touch targets, complete keyboard
path, visible focus states, WCAG AA contrast across both themes.
