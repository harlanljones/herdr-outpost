# Design System: Annunciator Control Panel

<!-- impeccable:design-schema 1 -->

## Thesis

The fleet reads as a control-room annunciator panel — a management-by-exception board that stays quiet while agents work and demands acknowledgment the instant one needs you — refusing generic glass-and-neon card grids in favor of honest, high-density telemetry.

## Visual Direction & Foundations

### 1. Panel Ground & Architecture
- **Graphite / Charcoal Ground**: Hard-edged opaque panels (`#12151b` ground, `#191e27` surface, `#212835` raised surface) with raking 1px borders (`#2c3647`) and zero blurry backdrops or thick side-tab borders.
- **Rhythm Heartbeat**: Live connection status indicator featuring an animated heartbeat tick tied directly to WebSocket freshness and connection health.
- **ASCII Density & Telemetry Scaling**: Zone visual weight scales with agent density and activity level.
- **Narrow Accent Bands**: Amber hue (`#f59e0b` / `#fbbf24`) is strictly reserved for alarming/blocked state indicators and borders, never washed as ambient glow.
- **Tactile Hinge**: Blocked agent rows expand with a tactile 3D hinge drop transition (`transform-origin: top center; perspective(600px)`).
- **GPU Transform Runway**: Telemetry meters animate via `transform: scaleX(...)` with `transform-origin: left; will-change: transform` to eliminate layout thrash.
- **Branded Browser Surfaces**: System-wide custom themed scrollbars (`scrollbar-color: var(--border-strong) var(--bg-panel)`) and branded text selection (`::selection`).

### 2. Typographic Scale
A disciplined 3-tier ramp with strict $\ge 1.25$ ratio between steps:
- **Display / Headers (20px / 24px)**: Bold industrial titles for brand, repo sections, modal headers.
- **Body & Controls (16px)**: Agent task titles, terminal output, quick keys, prompt input (16px floor prevents iOS auto-zoom).
- **Micro & Silkscreen Badges (12px)**: Harness tags, status plates, context percentage runway, timecodes, telemetry keys.
- **Font Stack**: Clean industrial sans (`-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif`) paired with monospace (`"JetBrains Mono", "SF Mono", Menlo, Monaco, Consolas, monospace`).
- **Tabular Numerals**: `font-variant-numeric: tabular-nums` for rock-solid metric alignments.

### 3. Color Palette & WCAG AA Contrast Audit

#### Dark Mode (Default)
| Role | Background | Border | Text | Contrast Ratio |
|---|---|---|---|---|
| Ground | `#12151b` | — | `#f1f5f9` | 13.8:1 |
| Surface | `#191e27` | `#2c3647` | `#f1f5f9` | 12.4:1 |
| Alarm (Blocked) | `#2d1808` | `#f59e0b` | `#fbbf24` | 8.6:1 |
| Working | `#07251a` | `#10b981` | `#34d399` | 7.4:1 |
| Done | `#0c1e30` | `#38bdf8` | `#7dd3fc` | 8.2:1 |
| Idle | `#1a202c` | `#475569` | `#94a3b8` | 5.3:1 |

#### Light Mode
| Role | Background | Border | Text | Contrast Ratio |
|---|---|---|---|---|
| Ground | `#edf1f7` | — | `#0f172a` | 13.6:1 |
| Surface | `#ffffff` | `#cfd8e3` | `#0f172a` | 15.2:1 |
| Alarm (Blocked) | `#fef3c7` | `#d97706` | `#92400e` | 6.8:1 |
| Working | `#d1fae5` | `#059669` | `#065f46` | 6.4:1 |
| Done | `#e0f2fe` | `#0284c7` | `#075985` | 7.1:1 |
| Idle | `#f1f5f9` | `#94a3b8` | `#475569` | 5.2:1 |

## Component Hierarchy & Layout

### 1. Header & Network Resilience
- Brand logo & title (`herdr-outpost Operate`).
- Live rhythm heartbeat connection indicator (`Connected`, `Connecting...`, `Auth Required`, `Error`, `Offline`).
- Persistent offline alert banner triggered immediately on network disconnection with auto-reconnect upon restoration.
- Watchdog ping-pong monitoring detecting dead sockets within 12 seconds and recycling stale connections.
- Quick actions: Theme switcher (Light/Dark) and Settings & QR Pairing modal button.

### 2. First Viewport: ALARM Section
- Prominently positioned above repo zones on all devices.
- Pulls currently blocked agents to the top with immediate inline action buttons (`✓ Approve`, `✕ Reject`, quick-choice chips `y`, `yes`, `n`, `always allow`, and `Terminal →`).
- Unblocking agents from phone requires zero scrolling and zero sheet transitions.
- Client-side action debouncing (400ms) prevents duplicate command submissions.

### 3. Repo-Grouped Fleet Zones
- Agents are grouped by `git_repo` (fallback to `workspace`).
- Sticky zone header shows `repo · branch*` once, eliminating repetitive per-card strings.
- Sorted within zone: `blocked` > `working` > `idle` > `done`.
- Zones ordered by most urgent agent status.
- Agent row structure:
  1. **Line 1 (Identity)**: Harness tag + model name + remote host badge + annunciator status plate (glyph + text label).
  2. **Line 2 (Task)**: Live task title clamped to 2 lines with robust word-break and XSS-safe escaping.
  3. **Line 3 (Runway)**: Honest context percentage meter (tokens used / limit) or quota window %, plus relative timestamp.
  4. **Inline Hinge**: Expanding action block for inline unblocking.

### 4. Terminal Sheet / Docked Desktop Side Panel
- Modal sheet on mobile (<1024px), persistent side panel on desktop (>=1024px).
- **Path-Based Session Routing**: First-class URL routing (`/session/{id}`) mapping to individual agent sheets. History push/pop (`pushState`/`popstate`) syncs sheet open/close state with the browser back button and enables SPA fallback on static hosts.
- Modal focus trap retaining keyboard navigation within the dialog and restoring focus to the trigger element on close.
- Collapsible telemetry drawer displaying exact harness, version, model, context used/limit, repo, branch, dirty flag, PID, pane ID, CWD, and observation metadata (`source`, `last_seen_at`).
- Live ANSI terminal console with autoscroll toggle, copy, clear controls, and memory-safe buffer capping (250KB / 2,000 lines).
- Interactive quick keys bar (↵ Enter, ⎋ Esc, ⌃C Interrupt, ␣ Space, ⇥ Tab, ↑ Up, ↓ Down).
- Command prompt input with 16px font floor to prevent mobile browser auto-zoom.

### 5. Settings & Device Pairing
- Instant QR-code mobile pairing with automatic URL token persistence and URL scrubbing.
- Web Push notifications integration with VAPID negotiation, Service Worker offline caching, and direct notification click deep-linking (`/session/{id}?action={action}`).
- Web Audio synthesizer tone engine for audible annunciator cues.
- Live connection diagnostics log.

### 6. Context-Aware Empty, Filter & Lifecycle States
- Distinct empty states for initial connection waiting, zero search results (with "Clear Search" CTA), and zero status filter matches (with "Show All Agents" CTA).
- Diacritic and case-normalized Unicode search across harness, model, repo, branch, cwd, task, and host.
- **Session Lifecycle & Pruning**: Responds to real-time `agent_removed` broadcasts when sessions close (2 consecutive missed polls) or expire (hook TTL), cleanly tearing down active subscriptions and closing terminal sheets if the active session terminates.
