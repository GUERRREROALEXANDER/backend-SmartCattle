"""What stands between the internet and the routes: request limits, a body size cap and security headers.

The limits live in the process, like the login lockout: they stop a loop from one address, which is what would exhaust
a small instance or the database's monthly compute. A wider attack is for the hosting provider's network to absorb.
"""

import json
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from typing import Any

from app.core.config import Settings

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

#: Every JSON body this API takes fits comfortably. Videos go straight to object storage, not through here.
MAX_BODY_BYTES = 1024 * 1024

#: (requests, seconds). A password check is the most expensive thing a request can ask for, and every new account
#: adds rows that stay.
REGISTER_LIMIT = (10, 3600)
LOGIN_LIMIT = (20, 60)
WRITE_LIMIT = (120, 60)
#: A dashboard polling several screens, and several people behind one farm connection.
GENERAL_LIMIT = (600, 60)

MAX_TRACKED_KEYS = 50_000

SECURITY_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"strict-transport-security", b"max-age=31536000"),
)


def client_address(headers: Mapping[str, str], peer: str, trust_proxy: bool) -> str:
    """Who is asking. Behind a proxy the peer is the proxy, and every visitor would share one limit.

    Cloudflare, in front of Render, overwrites CF-Connecting-IP. X-Forwarded-For can start with whatever the client
    sent, so it is only the fallback. Without a proxy, both are just the client's own words.
    """
    if trust_proxy:
        address = (headers.get("cf-connecting-ip") or headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if address:
            return address[:64]
    return peer


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[tuple[str, str], tuple[float, int]] = {}

    def hit(self, bucket: str, address: str, limit: tuple[int, int]) -> int:
        """Counts a request; the seconds to wait if it is over the limit, else 0."""
        now = self._clock()
        if len(self._hits) >= MAX_TRACKED_KEYS:
            self._hits = {key: value for key, value in self._hits.items() if value[0] > now}
            if len(self._hits) >= MAX_TRACKED_KEYS:
                self._hits.clear()  # Forgetting counts is better than running out of memory.
        allowed, seconds = limit
        ends, count = self._hits.get((bucket, address), (now + seconds, 0))
        if ends <= now:
            ends, count = now + seconds, 0
        if count >= allowed:
            return max(1, int(ends - now) + 1)
        self._hits[(bucket, address)] = (ends, count + 1)
        return 0


def _limit_for(method: str, path: str) -> tuple[str, tuple[int, int]]:
    if method == "POST" and path == "/api/auth/register":
        return "register", REGISTER_LIMIT
    if method == "POST" and path == "/api/auth/login":
        return "login", LOGIN_LIMIT
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        return "write", WRITE_LIMIT
    return "general", GENERAL_LIMIT


async def _answer(send: Send, status: int, detail: str, headers: list[tuple[bytes, bytes]] | None = None) -> None:
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send({
        "type": "http.response.start", "status": status,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("ascii")),
                    *(headers or [])],
    })
    await send({"type": "http.response.body", "body": body})


class GuardMiddleware:
    """A plain ASGI middleware: it must answer before a body is read, and must not touch WebSockets."""

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]], settings: Settings,
                 limiter: RateLimiter | None = None) -> None:
        self.app = app
        self.settings = settings
        self.limiter = limiter or RateLimiter()

    def _is_ai_service(self, path: str, headers: Mapping[str, str]) -> bool:
        """The AI service reports every second per camera, from one address, and proves who it is with its key."""
        key = self.settings.ai_api_key
        sent = headers.get("x-api-key")
        return (path.startswith("/api/ai/") and key is not None and sent is not None
                and secrets.compare_digest(sent.encode("utf-8"), key.get_secret_value().encode("utf-8")))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), *SECURITY_HEADERS]
            await send(message)

        path, method = scope["path"], scope["method"]
        headers = {name.decode("latin-1"): value.decode("latin-1") for name, value in scope["headers"]}
        # Render restarts the service when /health stops answering, so nobody may be able to make that happen.
        if self.settings.rate_limit_enabled and path != "/health" and not self._is_ai_service(path, headers):
            peer = scope["client"][0] if scope.get("client") else "unknown"
            address = client_address(headers, peer, self.settings.trust_proxy_headers)
            bucket, limit = _limit_for(method, path)
            wait = self.limiter.hit(bucket, address, limit)
            if wait:
                await _answer(send_with_headers, 429, "Too many requests; try again later",
                              [(b"retry-after", str(wait).encode("ascii"))])
                return

        declared = headers.get("content-length")
        if declared is not None:
            if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
                await _answer(send_with_headers, 413, "Request body too large")
                return
            await self.app(scope, receive, send_with_headers)
            return
        if method not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send_with_headers)
            return

        # No declared length: read up to the cap here, and hand the routes the same messages.
        messages: list[Message] = []
        total = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":
                break
            total += len(message.get("body", b""))
            if total > MAX_BODY_BYTES:
                await _answer(send_with_headers, 413, "Request body too large")
                return
            if not message.get("more_body", False):
                break

        async def replay() -> Message:
            return messages.pop(0) if messages else await receive()

        await self.app(scope, replay, send_with_headers)
