#!/usr/bin/env bash
# herdr-outpost reporter for antigravity-cli.
#
# Same --once shape as cline_hook.sh: antigravity-cli exposes no per-turn
# hook either, so this sharpens identity (pane<->workspace, git) when run
# manually or wrapped around an `agy` invocation. The relay's zero-config
# probe (relay/probes/antigravity.py) covers model + quota-window on its
# own via history.jsonl and settings.json; this only adds the exact
# $HERDR_PANE_ID mapping the probe's workspace-matching can't guarantee.

set -euo pipefail

RELAY_HTTP_BASE="${HERDR_OUTPOST_RELAY_HTTP:-${HERDR_RELAY_HTTP:-http://127.0.0.1:8375}}"
RELAY_TOKEN="${HERDR_OUTPOST_RELAY_TOKEN:-${HERDR_RELAY_TOKEN:-}}"

if [[ -z "${HERDR_PANE_ID:-}" ]]; then
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
event = {"pane_id": pane_id, "harness": "antigravity", "harness_version": "antigravity-cli", "cwd": cwd}
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
