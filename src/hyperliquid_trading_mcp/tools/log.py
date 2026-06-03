"""Trade-loop event log: append/read the workspace ``log.jsonl``.

The looping trade cycle records one JSON line per (crypto, tick). Exposing this
as MCP tools — instead of client-side Bash ``>>`` — means the skill writes the
log via a stable, allowlistable tool call (no per-tick shell permission prompt)
and the file always lands in the workspace (``CLAUDE_PROJECT_DIR``), never a
plugin path. Logging is not an order, so it runs in both DRY-RUN and LIVE.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from ..app import mcp
from ..models import LogAppendResult, LogTailResult

_log_lock = threading.Lock()


def _log_path() -> Path:
    """The workspace trade log — `CLAUDE_PROJECT_DIR/log.jsonl` (cwd fallback)."""
    base = os.getenv("CLAUDE_PROJECT_DIR") or "."
    return Path(base) / "log.jsonl"


@mcp.tool()
async def append_log(event: dict[str, Any]) -> LogAppendResult:
    """Append one event object as a JSON line to the workspace trade log
    (``CLAUDE_PROJECT_DIR/log.jsonl``). The trade loop calls this once per
    (crypto, iteration) to record a tick. Returns the file path and the new
    total line count. Local-only financial data — git-ignore it.
    """
    path = _log_path()
    try:
        line = json.dumps(event, separators=(",", ":"))
        with _log_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            with path.open("r", encoding="utf-8") as f:
                lines = sum(1 for _ in f)
        return LogAppendResult(status="ok", path=str(path.resolve()), lines=lines)
    except Exception as e:  # noqa: BLE001
        return LogAppendResult(status="error", path=str(path), error=str(e))


@mcp.tool()
async def get_log(
    limit: Annotated[int, Field(ge=1, le=2000)] = 50,
    session_id: str | None = None,
) -> LogTailResult:
    """Return the most recent `limit` events from the workspace trade log
    (oldest-first), the file path, and the highest `iteration_id` seen — for the
    next tick's id. If `session_id` is given, `max_iteration_id` is scoped to
    that session; otherwise it is the file-wide max. Read-only.
    """
    path = _log_path()
    try:
        if not path.exists():
            return LogTailResult(
                status="ok", path=str(path.resolve()), count=0, max_iteration_id=0, events=[]
            )
        with _log_lock:
            raw = path.read_text(encoding="utf-8").splitlines()
        parsed: list[dict[str, Any]] = []
        max_it = 0
        for ln in raw:
            s = ln.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                continue  # tolerate a partially-written/corrupt line
            if not isinstance(obj, dict):
                continue
            parsed.append(obj)
            it = obj.get("iteration_id")
            if isinstance(it, int) and (session_id is None or obj.get("session_id") == session_id):
                max_it = max(max_it, it)
        tail = parsed[-limit:]
        return LogTailResult(
            status="ok",
            path=str(path.resolve()),
            count=len(tail),
            max_iteration_id=max_it,
            events=tail,
        )
    except Exception as e:  # noqa: BLE001
        return LogTailResult(status="error", path=str(path), error=str(e))
