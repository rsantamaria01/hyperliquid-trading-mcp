"""Optional bearer-token auth for the Streamable HTTP transport.

The MCP endpoint ships with no auth of its own — anyone who can reach the port
can drive the trading tools. When the server is reachable beyond localhost
(e.g. behind a reverse proxy on another host), set ``MCP_AUTH_TOKEN`` so every
request to ``/mcp`` must carry ``Authorization: Bearer <token>``. An unset or
empty token disables the check, so local/dev deployments are unaffected.

Implemented as *pure ASGI* middleware, deliberately NOT Starlette's
``BaseHTTPMiddleware``: that class buffers the whole response before sending it,
which breaks the long-lived streaming responses the transport relies on. This
wrapper only inspects the request headers and either short-circuits with 401 or
passes the scope through untouched, so the stream is never disturbed.

Paths in ``exempt_paths`` skip the check — used for the unauthenticated
``/health`` liveness route the container healthcheck hits, so the healthcheck
never needs to carry the token.
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable, Iterable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class BearerAuthMiddleware:
    """Reject HTTP requests lacking ``Authorization: Bearer <token>``.

    Non-HTTP scopes (lifespan, websocket) and any path in ``exempt_paths`` pass
    straight through. The token comparison uses ``hmac.compare_digest`` to avoid
    leaking length/content via timing.
    """

    def __init__(
        self,
        app: ASGIApp,
        token: str,
        exempt_paths: Iterable[str] = (),
    ) -> None:
        self.app = app
        self._expected = f"Bearer {token}".encode()
        self._exempt = frozenset(exempt_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self._exempt:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        provided = headers.get(b"authorization", b"")
        if not hmac.compare_digest(provided, self._expected):
            await self._unauthorized(send)
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _unauthorized(send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'{"error":"unauthorized"}',
            }
        )
