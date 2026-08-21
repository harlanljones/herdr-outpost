#!/usr/bin/env bash
# herdr-outpost reporter for cline.
#
# cline does not currently expose a per-turn hook the way Claude Code's
# statusLine does, so this is a --once invocation: run it from a place that
# fires periodically in a cline session's lifecycle (a shell alias wrapping
# `cline`, a cron entry, or manually) rather than a true per-turn hook.
# Until a real hook exists, the relay's zero-config probe
# (relay/probes/cline.py) is the primary path for cline agents -- this
# script only sharpens identity when $HERDR_PANE_ID is available, which the
# probe's pid/cwd matching cannot see.
#
# Usage: HERDR_PANE_ID is read from the environment automatically inside a
# herdr pane. Run with no arguments.

set -euo pipefail

RELAY_HTTP_BASE="${HERDR_OUTPOST_RELAY_HTTP:-${HERDR_RELAY_HTTP:-http://127.0.0.1:8375}}"
RELAY_TOKEN="${HERDR_OUTPOST_RELAY_TOKEN:-${HERDR_RELAY_TOKEN:-}}"

if [[ -z "${HERDR_PANE_ID:-}" ]]; then
  # Not running inside a herdr pane -- nothing to report against.
  exit 0
fi

CWD="$(pwd)"
GIT_REPO=""
GIT_BRANCH=""
if git -C "$CWD" rev-parse --show-toplevel >/dev/null 2>&1; then
  GIT_REPO="$(basename "$(git -C "$CWD" rev-parse --show-toplevel)")"
  GIT_BRANCH="$(git -C "$CWD" rev-parse --abbrev-ref HEAD)"
fi

PAYLOAD=$(python3 - "$HERDR_PANE_ID" "$CWD" "$GIT_REPO" "$GIT_BRANCH" <<'PYEOF'
import json, sys
pane_id, cwd, git_repo, git_branch = sys.argv[1:5]
event = {"pane_id": pane_id, "harness": "cline", "cwd": cwd}
if git_repo:
    event["git_repo"] = git_repo
if git_branch:
    event["git_branch"] = git_branch
print(json.dumps({"payload": event}))
PYEOF
)

AUTH_HEADER=()
if [[ -n "$RELAY_TOKEN" ]]; then
  AUTH_HEADER=(-H "Authorization: Bearer ${RELAY_TOKEN}")
fi

curl -fsS -m 2 -X POST "${RELAY_HTTP_BASE%/}/event" \
  -H "Content-Type: application/json" \
  "${AUTH_HEADER[@]}" \
  -d "$PAYLOAD" >/dev/null 2>&1 || true
