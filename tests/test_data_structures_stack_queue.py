"""Checks for the stack and queue examples.

Both are tested in one file because the point of having both is the
difference between them, and that is easiest to assert side by side.
"""

import pytest

from data_structures.queue_smartcattle import MonitoringEvent as QueuedEvent
from data_structures.queue_smartcattle import PendingEventQueue
from data_structures.stack_smartcattle import MonitoringEvent as StackedEvent
from data_structures.stack_smartcattle import RecentEventStack


def stacked(event_id: str) -> StackedEvent:
    return StackedEvent(event_id, "cattle_out_of_zone", "camera-01", "cow", 0.95)


def queued(event_id: str) -> QueuedEvent:
    return QueuedEvent(event_id, "cattle_out_of_zone", "camera-01", "cow", 0.95)


def test_an_empty_stack_is_empty():
    stack = RecentEventStack()
    assert stack.is_empty() is True
    assert stack.size() == 0


def test_the_stack_returns_the_newest_first():
    # LIFO, which is the order operators want: the newest alert first, and the
    # same order GET /api/events returns.
    stack = RecentEventStack()
    for event_id in ["Event-001", "Event-002", "Event-003"]:
        stack.push(stacked(event_id))
    assert [stack.pop().event_id for _ in range(3)] == ["Event-003", "Event-002", "Event-001"]


def test_peek_does_not_remove():
    stack = RecentEventStack()
    stack.push(stacked("Event-001"))
    assert stack.peek().event_id == "Event-001"
    assert stack.size() == 1


@pytest.mark.parametrize("operation", ["pop", "peek"])
def test_an_empty_stack_raises(operation):
    # Raising rather than returning None: there is no event to describe, and a
    # None would be mistaken for a real one by the next line of caller code.
    with pytest.raises(IndexError, match="empty event stack"):
        getattr(RecentEventStack(), operation)()


def test_an_empty_queue_is_empty():
    queue = PendingEventQueue()
    assert queue.is_empty() is True
    assert queue.size() == 0


def test_the_queue_returns_the_oldest_first():
    # FIFO, which is what a processing queue needs: the event that has been
    # waiting longest is handled next, so nothing starves.
    queue = PendingEventQueue()
    for event_id in ["Event-A", "Event-B", "Event-C"]:
        queue.enqueue(queued(event_id))
    assert [queue.dequeue().event_id for _ in range(3)] == ["Event-A", "Event-B", "Event-C"]


def test_the_queue_peek_does_not_remove():
    queue = PendingEventQueue()
    queue.enqueue(queued("Event-A"))
    assert queue.peek().event_id == "Event-A"
    assert queue.size() == 1


@pytest.mark.parametrize("operation", ["dequeue", "peek"])
def test_an_empty_queue_raises(operation):
    with pytest.raises(IndexError, match="empty event queue"):
        getattr(PendingEventQueue(), operation)()


def test_the_stack_and_the_queue_disagree_on_purpose():
    """The whole reason both examples exist.

    Given the same three events, the stack hands back the newest and the queue
    the oldest. If either ever matched the other, one of them would be wrong.
    """
    stack = RecentEventStack()
    queue = PendingEventQueue()
    for event_id in ["first", "second", "third"]:
        stack.push(stacked(event_id))
        queue.enqueue(queued(event_id))

    assert stack.peek().event_id == "third"
    assert queue.peek().event_id == "first"


def test_interleaving_does_not_confuse_the_queue():
    # A queue used as a real pipeline is written to while being read from.
    queue = PendingEventQueue()
    queue.enqueue(queued("Event-A"))
    queue.enqueue(queued("Event-B"))
    assert queue.dequeue().event_id == "Event-A"
    queue.enqueue(queued("Event-C"))
    assert [queue.dequeue().event_id for _ in range(2)] == ["Event-B", "Event-C"]
    assert queue.is_empty() is True
