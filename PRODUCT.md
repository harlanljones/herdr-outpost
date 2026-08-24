# Product — 1.0.0

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Static HTML/CSS/JS, zero build step, deployed to Cloudflare Workers via `web/wrangler.toml`
(`[assets] directory = "."`, `html_handling = "auto-trailing-slash"`, `not_found_handling = "single-page-application"`).
Confirmed during shape: stay single-file (`web/index.html`) with client-side SPA routing (`/session/{id}`).

## Users

The operator (Harlan) running several AI coding agents (Claude Code, cline, antigravity-cli)
concurrently across local repos via [Herdr](https://herdr.dev), a terminal workspace manager.
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

- Agents run inside Herdr panes on one machine (confirmed scale: 2–8 agents, one host); SSH
  remote hosts are supported by the relay.
- Three harnesses in active use on this machine: Claude Code (`~/.claude/projects/*.jsonl`),
  cline (`~/.cline/data/sessions/*`), antigravity-cli (`~/.gemini/antigravity-cli/`).
- `herdr agent list` polled by the relay every 3s returns per-pane `agent` (harness), `cwd`,
  `foreground_cwd`, `terminal_title_stripped` (live task), `workspace_id`, `pane_id`.
- Every pane has `$HERDR_PANE_ID` in its environment — usable by an in-pane reporter for exact
  pane↔session identity.
- The relay (`relay/herdr_relay.py`) multiplexes HTTP + WebSocket on one port, plus a UDP event
  ingress (`relay/on_event.py`) fed by Herdr event hooks and optional reporters. `relay/agent_state.py` is the single
  shared schema all transports normalize through (`normalize_agent_dict`).
- Session lifecycle is authoritative and self-healing: closed agents absent from 2 consecutive
  polls are pruned, while hook/UDP-only sessions expire after a TTL (`HERDR_OUTPOST_SESSION_TTL`,
  default 90s). Pruned sessions emit `agent_removed` WebSocket broadcasts.
- For the exact unreliable Herdr result `blocked` + `screen_detection_skipped: true` + no
  `agent_session`, the relay asks Herdr's active screen manifest to classify a fresh detection
  snapshot. Successful `working`, `idle`, or `blocked` results replace the stale state; failures
  retain the original unverified block.
- Deployment: Cloudflare Workers (static web) + Cloudflare Tunnel (relay), documented in
  `SCAFFOLD.md`; alternative static hosts and process runners also supported.

## Capabilities and Constraints

- Confirmed interaction model: monitor **and** full terminal control — live ANSI output plus
  arbitrary input to any agent, not read-only.
- Confirmed: unblocking an agent (approve/reject/reply) must be possible without opening the
  terminal — it's the primary phone action.
- Path-based session routing (`/session/{id}`) enables direct bookmarking, notification deep-linking,
  and browser history navigation (pushState/popstate) directly into focused terminal sheets.
- Harness subagent trees are derivable and surfaced at `/tree`: OpenCode via its local SQLite
  session store (`session.parent_id`, keyed off herdr's exact `agent_session` pane identity), and
  Claude Code via its transcript sidechains (`~/.claude/projects/<slug>/<sessionId>/subagents/`).
  cline and antigravity-cli expose no parent links, so their agents show childless. A subagent's
  `active` flag means "session data written recently" — an activity signal, never a fabricated
  lifecycle status.
- Per-session context-window usage is directly readable for Claude Code and cline via their local
  session files. antigravity-cli does not expose a readable per-session context figure (session
  data is protobuf inside SQLite); it exposes quota-window usage instead via a collector already
  present on this machine (`~/.config/omarchy/plugins/mustafaokur.agent-leaderboard/collect-antigravity.py`).
  The UI must not fabricate a context percentage where none exists — show the honest metric
  available (context used/limit, or quota window %) or nothing.
- Git repo/branch is derivable locally via `git -C <cwd>` for any agent whose cwd is on this
  machine.
- The existing WebSocket message schema (`agent_update`, `agents_snapshot`, `agent_removed`) and
  its agent shape in `relay/agent_state.py` is the extension point — including observation provenance
  (`source`) and timestamp (`last_seen_at`). New fields must be added there first, per `AGENTS.md`'s
  "shared interface defined first" rule, then flow to relay callers and the web client.

## Brand Commitments

- Name: **herdr-outpost**. Existing logo asset: `web/logo.svg` (gradient mark, cyan → blue →
  violet, matching status colors). Kept as-is; the mark's use in the surface may be restyled, but
  the asset itself is not being redrawn as part of this brief.
- Public links and copy use the `herdr-outpost` name and point to the current repository.

## Release Evidence

- The relay consumes the documented `herdr agent list`, `herdr pane process-info`, `herdr pane
  get`, `herdr pane read`, and `herdr agent explain --file` interfaces. Herdr's installed screen
  manifest remains the source of classification rules.

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

The 1.0.0 surface supports real pinch-zoom (no `user-scalable=no`),
`prefers-reduced-motion`, status
conveyed by glyph + label in addition to color, 44×44px minimum touch targets, complete keyboard
path, visible focus states, WCAG AA contrast across both themes.
