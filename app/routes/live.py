"""The dashboard's live stream.

Protocol:
- The client's first message must be {"type": "auth", "token": "<access token>"},
  within AUTH_TIMEOUT_SECONDS. Browsers cannot set headers on a WebSocket, and a
  token in the query string would be written to every access log on the way.
- The server answers {"type": "hello", ...} and then forwards every broadcast:
  event.created, alert.created, alert.updated, camera.status and the rest.
- {"type": "ping"} is answered with {"type": "pong"}; anything else is ignored.
"""

import asyncio
import json
import logging
from collections import Counter
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.dependencies import Clock, user_for_token
from app.guard import client_address
from app.services.broadcaster import Broadcaster, Subscription, envelope
from app.services.user_store import StoredUser, UserStore

logger = logging.getLogger(__name__)

router = APIRouter(tags=["live"])

AUTH_TIMEOUT_SECONDS = 5.0

#: Each open socket holds memory and receives every broadcast. A person has a few tabs; a loop has thousands.
MAX_SOCKETS = 200
MAX_SOCKETS_PER_ADDRESS = 20
CLOSE_TRY_AGAIN_LATER = 1013

#: Application close codes (4000-4999). Mirroring HTTP keeps them obvious.
CLOSE_UNAUTHORIZED = 4401
CLOSE_AUTH_NOT_CONFIGURED = 4503


async def _receive_json(websocket: WebSocket) -> Any:
    """The next message as JSON, or None when it is not JSON at all."""
    text = await websocket.receive_text()
    try:
        return json.loads(text)
    except ValueError:
        return None


async def _authenticate(websocket: WebSocket, settings: Settings, clock: Clock) -> StoredUser | None:
    if settings.jwt_secret is None:
        # Same as the HTTP API, which answers 503 rather than serving data
        # unprotected when authentication has not been configured.
        await websocket.close(code=CLOSE_AUTH_NOT_CONFIGURED, reason="Authentication is not configured")
        return None
    try:
        message = await asyncio.wait_for(_receive_json(websocket), timeout=AUTH_TIMEOUT_SECONDS)
    except TimeoutError:
        await websocket.close(code=CLOSE_UNAUTHORIZED, reason="Authentication timed out")
        return None
    token = message.get("token") if isinstance(message, dict) and message.get("type") == "auth" else None
    store: UserStore = websocket.app.state.user_store
    user = None
    if isinstance(token, str) and token:
        # The SQL store blocks; keep it off the event loop.
        user = await run_in_threadpool(
            user_for_token, token, settings.jwt_secret.get_secret_value(), store, clock(),
        )
    if user is None:
        await websocket.close(code=CLOSE_UNAUTHORIZED, reason="Missing or invalid access token")
    return user


async def _forward(websocket: WebSocket, subscription: Subscription) -> None:
    # The only task that writes to the socket, so frames never interleave.
    while True:
        await websocket.send_json(await subscription.next())


@router.websocket("/ws/live")
async def live(websocket: WebSocket) -> None:
    await websocket.accept()
    settings: Settings = websocket.app.state.settings
    clock: Clock = websocket.app.state.clock
    broadcaster: Broadcaster = websocket.app.state.broadcaster
    sockets: Counter[str] = websocket.app.state.live_sockets
    peer = websocket.client.host if websocket.client else "unknown"
    address = client_address(websocket.headers, peer, settings.trust_proxy_headers)
    if sockets.total() >= MAX_SOCKETS or sockets[address] >= MAX_SOCKETS_PER_ADDRESS:
        await websocket.close(code=CLOSE_TRY_AGAIN_LATER, reason="Too many connections")
        return
    sockets[address] += 1
    try:
        user = await _authenticate(websocket, settings, clock)
        if user is None:
            return
        with broadcaster.subscribe() as subscription:
            subscription.offer(envelope("hello", {"user_id": user.id, "role": user.role.value}, clock()))
            sender = asyncio.create_task(_forward(websocket, subscription))
            try:
                while True:
                    message = await _receive_json(websocket)
                    if isinstance(message, dict) and message.get("type") == "ping":
                        subscription.offer(envelope("pong", None, clock()))
            finally:
                sender.cancel()
                # Collected so a send that failed on a closed socket is not
                # reported later as a never-retrieved task exception.
                await asyncio.gather(sender, return_exceptions=True)
                if subscription.dropped:
                    logger.info("Live client fell behind", extra={"dropped_messages": subscription.dropped})
    except WebSocketDisconnect:
        return
    finally:
        sockets[address] -= 1
        if sockets[address] <= 0:
            del sockets[address]
