"""Local enrichment probes for herdr-outpost.

Each probe reads a harness's own on-disk state (or, for git, the working tree)
to fill in fields `herdr agent list` does not report: model, context usage,
cost, and git identity. Every probe function returns a **partial dict** (only
the keys it could determine) or `{}` -- it must never raise into the relay's
poll loop, and it must never invent a value it cannot actually measure.

Enrichment only applies to `host == "local"`; SSH remotes are out of scope
for v1 (their session files live on the remote machine).
"""

from __future__ import annotations

import inspect
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("herdr-outpost.probes")

# Per-agent-id cache: (expires_at, partial_dict). Keeps the probe cost off the
# 3s poll loop -- session files and git status are re-read at most every
# PROBE_TTL_SECONDS regardless of poll frequency.
PROBE_TTL_SECONDS = 10.0
_cache: Dict[str, tuple] = {}


def enrich(
    agent_id: str,
    cwd: str,
    pid: Optional[int],
    harness: str,
    agent_session: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return cached or freshly-probed enrichment fields for one agent.

    `harness` is herdr's own label ("claude", "cline", ...); probes are tried
    in a fixed order and merged, cheapest/most-certain first, so a later
    probe's absence never clobbers an earlier probe's finding.

    `agent_session` is herdr's lifecycle-session envelope for the pane (with
    the harness-native session id in `value`); only probes that declare an
    `agent_session` parameter receive it.
    """
    now = time.monotonic()
    cached = _cache.get(agent_id)
    if cached and cached[0] > now:
        return cached[1]

    result: Dict[str, Any] = {}
    for probe, accepts_session in _PROBES:
        try:
            kwargs: Dict[str, Any] = {"cwd": cwd, "pid": pid, "harness": harness}
            if accepts_session:
                kwargs["agent_session"] = agent_session
            partial = probe(**kwargs)
        except Exception as err:  # a probe must never break the poll loop
            logger.debug(f"probe {probe.__name__} failed for {agent_id}: {err}")
            partial = None
        if partial:
            for k, v in partial.items():
                if v not in (None, ""):
                    result[k] = v

    _cache[agent_id] = (now + PROBE_TTL_SECONDS, result)
    return result


def _load_probes():
    from . import git as git_probe
    from . import claude_code as claude_probe
    from . import cline as cline_probe
    from . import antigravity as antigravity_probe
    from . import subagents as subagents_probe

    def accepts_agent_session(fn) -> bool:
        params = inspect.signature(fn).parameters
        return "agent_session" in params or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        )

    # Subagents last: most expensive (SQLite / transcript scans), least certain.
    return [
        (git_probe.probe, False),
        (claude_probe.probe, False),
        (cline_probe.probe, False),
        (antigravity_probe.probe, False),
        (subagents_probe.probe, True),
    ]


_PROBES = _load_probes()
