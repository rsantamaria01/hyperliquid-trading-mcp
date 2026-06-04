"""append_log / get_log write to and read the workspace log.jsonl.

These back the trade-loop's logging so it doesn't shell out to Bash >> (which
prompts for permission every tick). The log must land in CLAUDE_PROJECT_DIR.
"""

from __future__ import annotations

import json

from hyperliquid_trading_mcp.tools.log import append_log, get_log


async def test_append_writes_to_workspace_and_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    r = await append_log(
        {"iteration_id": 1, "session_id": "s1", "crypto": "BTC", "decision": "hold"}
    )
    assert r.status == "ok"
    assert r.lines == 1
    assert r.path == str((tmp_path / "log.jsonl").resolve())
    # File actually exists in the workspace, valid JSON line.
    lines = (tmp_path / "log.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["crypto"] == "BTC"


async def test_get_log_returns_tail_and_max_iteration(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    await append_log({"iteration_id": 1, "session_id": "s1", "crypto": "BTC", "decision": "hold"})
    await append_log({"iteration_id": 1, "session_id": "s1", "crypto": "ETH", "decision": "hold"})
    await append_log({"iteration_id": 2, "session_id": "s1", "crypto": "BTC", "decision": "open"})
    t = await get_log(limit=10, session_id="s1")
    assert t.status == "ok"
    assert t.count == 3
    assert t.max_iteration_id == 2  # next tick = 3
    assert t.events[0]["crypto"] == "BTC"
    assert t.events[-1]["decision"] == "open"


async def test_max_iteration_scoped_to_session(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    await append_log({"iteration_id": 9, "session_id": "other", "crypto": "BTC"})
    await append_log({"iteration_id": 2, "session_id": "mine", "crypto": "BTC"})
    assert (await get_log(session_id="mine")).max_iteration_id == 2  # ignores 'other'
    assert (await get_log()).max_iteration_id == 9  # file-wide when unscoped


async def test_get_log_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    t = await get_log()
    assert t.status == "ok"
    assert t.count == 0
    assert t.max_iteration_id == 0


async def test_get_log_tolerates_corrupt_line(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    (tmp_path / "log.jsonl").write_text(
        '{"iteration_id":1,"session_id":"s"}\nNOT JSON\n{"iteration_id":2,"session_id":"s"}\n'
    )
    t = await get_log(session_id="s")
    assert t.count == 2  # corrupt middle line skipped
    assert t.max_iteration_id == 2
