"""In-process fan-out of live dashboard messages.

Route handlers publish; each open WebSocket owns one subscription. Publishing
never blocks and never fails the request that triggered it: a dashboard tab
that stopped reading loses its oldest messages, not the farm its ingestion.

Per process by design. Render's free plan runs a single worker; running
several needs a shared bus (Redis pub/sub, PostgreSQL LISTEN/NOTIFY) behind
the same publish/subscribe interface, because a message published in one
worker never reaches a socket held by another.
"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from threading import Lock
from typing import Any

from pydantic import BaseModel

#: Enough for a burst of detections at a few frames per second across several
#: cameras, small enough that a stalled tab cannot hold much memory.
QUEUE_SIZE = 256

Message = dict[str, Any]


def envelope(kind: str, data: object, sent_at: datetime) -> Message:
    payload = data.model_dump(mode="json") if isinstance(data, BaseModel) else data
    return {"type": kind, "data": payload, "sent_at": sent_at.isoformat()}


class Subscription:
    """One consumer's bounded queue, bound to the event loop that reads it."""

    def __init__(self, loop: asyncio.AbstractEventLoop, size: int, account_id: int | None = None) -> None:
        self.account_id = account_id
        self._loop = loop
        self._queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=size)
        self.dropped = 0

    def offer(self, message: Message) -> None:
        """Queues a message from any thread."""
        self._loop.call_soon_threadsafe(self._put, message)

    async def next(self) -> Message:
        return await self._queue.get()

    def _put(self, message: Message) -> None:
        # Runs on the loop thread, so the full-check and the eviction cannot
        # interleave with the reader.
        if self._queue.full():
            self._queue.get_nowait()
            self.dropped += 1
        self._queue.put_nowait(message)


class Broadcaster:
    def __init__(self, queue_size: int = QUEUE_SIZE) -> None:
        self._subscriptions: set[Subscription] = set()
        self._queue_size = queue_size
        self._lock = Lock()

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscriptions)

    @contextmanager
    def subscribe(self, account_id: int | None = None) -> Iterator[Subscription]:
        """Registers a consumer for the duration of the block. Call from the event loop.

        With an account it only receives that account's messages; without one, every message.
        """
        subscription = Subscription(asyncio.get_running_loop(), self._queue_size, account_id)
        with self._lock:
            self._subscriptions.add(subscription)
        try:
            yield subscription
        finally:
            with self._lock:
                self._subscriptions.discard(subscription)

    def publish(self, kind: str, data: object, sent_at: datetime, account_id: int | None = None) -> None:
        """Sends a message to the subscribers of this account, and to those not limited to one.

        Safe from the worker threads FastAPI runs synchronous handlers on.
        With nobody listening it returns before serialising anything.
        """
        with self._lock:
            targets = [item for item in self._subscriptions if item.account_id in (None, account_id)]
        if not targets:
            return
        message = envelope(kind, data, sent_at)
        for subscription in targets:
            try:
                subscription.offer(message)
            except RuntimeError:
                # The subscriber's loop closed between the snapshot and the
                # offer; its context manager is about to unregister it.
                continue
