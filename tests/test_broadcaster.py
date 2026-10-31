import asyncio
from datetime import UTC, datetime

from pydantic import BaseModel

from app.services.broadcaster import Broadcaster, envelope

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


class Reading(BaseModel):
    value: int


class Unserialisable(BaseModel):
    def model_dump(self, **_kwargs):
        raise AssertionError("serialised with nobody listening")


def test_the_envelope_serialises_models_and_stamps_the_time():
    assert envelope("reading", Reading(value=3), NOW) == {
        "type": "reading", "data": {"value": 3}, "sent_at": "2026-10-31T12:00:00+00:00",
    }
    assert envelope("pong", None, NOW)["data"] is None


def test_publishing_with_nobody_listening_does_no_work():
    Broadcaster().publish("reading", Unserialisable(), NOW)


def test_a_message_published_from_a_worker_thread_reaches_the_subscriber():
    broadcaster = Broadcaster()

    async def scenario():
        with broadcaster.subscribe() as subscription:
            loop = asyncio.get_running_loop()
            # FastAPI runs synchronous handlers on a thread pool, which is
            # where every real publish comes from.
            await loop.run_in_executor(None, broadcaster.publish, "reading", Reading(value=7), NOW)
            return await asyncio.wait_for(subscription.next(), timeout=1)

    assert asyncio.run(scenario())["data"] == {"value": 7}


def test_every_subscriber_gets_its_own_copy():
    broadcaster = Broadcaster()

    async def scenario():
        with broadcaster.subscribe() as first, broadcaster.subscribe() as second:
            broadcaster.publish("reading", Reading(value=1), NOW)
            return await first.next(), await second.next()

    first, second = asyncio.run(scenario())
    assert first == second


def test_a_full_queue_drops_the_oldest_message():
    broadcaster = Broadcaster(queue_size=2)

    async def scenario():
        with broadcaster.subscribe() as subscription:
            for value in (1, 2, 3):
                broadcaster.publish("reading", Reading(value=value), NOW)
            await asyncio.sleep(0)
            received = [(await subscription.next())["data"]["value"] for _ in range(2)]
            return received, subscription.dropped

    assert asyncio.run(scenario()) == ([2, 3], 1)


def test_a_subscription_ends_with_its_block():
    broadcaster = Broadcaster()

    async def scenario():
        with broadcaster.subscribe():
            inside = broadcaster.subscriber_count
        return inside, broadcaster.subscriber_count

    assert asyncio.run(scenario()) == (1, 0)


def test_a_subscriber_whose_loop_closed_does_not_break_publishing():
    broadcaster = Broadcaster()

    async def leak_a_subscription():
        # Entered and never exited, as if the loop died before the socket's
        # cleanup ran.
        broadcaster.subscribe().__enter__()

    asyncio.run(leak_a_subscription())
    broadcaster.publish("reading", Reading(value=1), NOW)
