"""Bearer-token HTTP auth middleware tests."""

from __future__ import annotations

import asyncio

from hyperliquid_trading_mcp.auth import BearerAuthMiddleware

TOKEN = "s3cret-token"


def _run(scope):
    """Drive the middleware once, returning (downstream_called, sent_messages)."""
    state = {"called": False}

    async def downstream(_scope, _receive, _send):
        state["called"] = True

    async def receive():
        return {"type": "http.request"}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    mw = BearerAuthMiddleware(downstream, TOKEN, exempt_paths={"/health"})
    asyncio.run(mw(scope, receive, send))
    return state["called"], sent


def _http(path="/mcp", auth=None):
    headers = []
    if auth is not None:
        headers.append((b"authorization", auth))
    return {"type": "http", "path": path, "headers": headers}


def test_missing_token_rejected_401():
    called, sent = _run(_http(auth=None))
    assert called is False
    assert sent[0]["status"] == 401


def test_wrong_token_rejected_401():
    called, sent = _run(_http(auth=b"Bearer nope"))
    assert called is False
    assert sent[0]["status"] == 401


def test_correct_token_passes_through():
    called, sent = _run(_http(auth=f"Bearer {TOKEN}".encode()))
    assert called is True
    assert sent == []  # middleware emitted nothing; downstream owns the response


def test_health_path_exempt_without_token():
    called, sent = _run(_http(path="/health", auth=None))
    assert called is True
    assert sent == []


def test_non_http_scope_passes_through():
    called, _ = _run({"type": "lifespan"})
    assert called is True
