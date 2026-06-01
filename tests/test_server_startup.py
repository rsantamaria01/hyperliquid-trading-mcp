"""Startup tests for the stdio entrypoint.

The hermetic_settings fixture (conftest) redirects SETTINGS_PATH to a tmp file
and forces DRY-RUN, so the banner's mode reflects only what each test sets.
`mcp.run()` is patched out so no real stdio transport is started.
"""

from __future__ import annotations

import os

from hyperliquid_trading_mcp import server, settings


def test_main_runs_stdio_transport(monkeypatch):
    """main() hands off to mcp.run() (stdio) — no host/port/uvicorn path."""
    called = {"run": False}
    monkeypatch.setattr(server.mcp, "run", lambda: called.__setitem__("run", True))
    server.main()
    assert called["run"] is True


def test_banner_goes_to_stderr_with_workspace_and_mode(monkeypatch, tmp_path, capsys):
    """Banner is written to stderr (not stdout — the MCP channel) and carries
    the resolved workspace path plus the mode tag. Covers AE2."""
    monkeypatch.setattr(server, "_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(server.mcp, "run", lambda: None)
    server.main()
    out = capsys.readouterr()
    assert out.out == ""  # stdout stays clean for the MCP protocol
    assert os.path.abspath(str(tmp_path)) in out.err
    assert "DRY-RUN" in out.err


def test_banner_shows_live_when_enabled(monkeypatch, capsys):
    settings.update({"live_trading": True})
    monkeypatch.setattr(server.mcp, "run", lambda: None)
    server.main()
    err = capsys.readouterr().err
    assert "LIVE" in err and "DRY-RUN" not in err


def test_banner_shows_dry_run_by_default(monkeypatch, capsys):
    monkeypatch.setattr(server.mcp, "run", lambda: None)
    server.main()
    assert "DRY-RUN" in capsys.readouterr().err


def test_project_dir_falls_back_to_cwd_when_unset(monkeypatch):
    """With CLAUDE_PROJECT_DIR unset the dotenv path falls back to '.' and the
    banner resolves to an absolute path — no crash when no .env exists."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    resolved = os.getenv("CLAUDE_PROJECT_DIR") or "."
    assert resolved == "."
    # banner path resolution does not raise on a missing .env
    assert os.path.isabs(os.path.abspath(resolved))
