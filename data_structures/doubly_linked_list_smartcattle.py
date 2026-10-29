"""Academic example of a doubly linked list over the SmartCattle event history.

Why a second link per node, when linked_list_smartcattle.py already sequences
events with one:

1. **Reviewing alerts moves both ways.** An operator reading the night's
   events goes forward and back. With only `next`, stepping back means
   traversing from the head again, so moving one event backwards costs the
   whole list.

2. **Removing a node you already hold is O(1).** To unlink a node from a
   singly linked list you need its predecessor, which means a search even when
   the node is already in your hand. With `prev` the unlink is four pointer
   assignments and no traversal.

The second point is the one that matters in practice: dismissing a reviewed
alert is exactly "remove the node I am looking at".

Not used by the API. Individual animal identities such as "Cow-001" assume a
future phase; the event records here are simplified teaching examples, not API
payloads.
"""

from dataclasses import dataclass


@dataclass
class MonitoringEvent:
    event_id: str
    event_type: str
    camera_id: str
    detected_object: str
    confidence: float


class Node:
    """One event, linked to the events on either side of it."""

    def __init__(self, data: MonitoringEvent) -> None:
        self.data: MonitoringEvent = data
        self.prev: Node | None = None
        self.next: Node | None = None


class EventHistory:
    """Event history that can be walked in either direction.

    Keeps `tail` as well as `head`. Without it, appending would have to walk
    the whole list to find the end -- which is what makes the singly linked
    version's insert O(n) -- and reverse traversal would have no starting
    point.
    """

    def __init__(self) -> None:
        self.head: Node | None = None
        self.tail: Node | None = None
        self._size = 0

    def append(self, event: MonitoringEvent) -> Node:
        """Adds an event at the end. O(1), because the tail is held.

        Returns the node, so a caller that will later dismiss this event can
        keep the handle and unlink it without searching.
        """
        node = Node(event)
        if self.tail is None:
            # Empty list: the single node is both ends.
            self.head = node
            self.tail = node
        else:
            node.prev = self.tail
            self.tail.next = node
            self.tail = node
        self._size += 1
        return node

    def prepend(self, event: MonitoringEvent) -> Node:
        """Adds an event at the front. O(1), the mirror of append."""
        node = Node(event)
        if self.head is None:
            self.head = node
            self.tail = node
        else:
            node.next = self.head
            self.head.prev = node
            self.head = node
        self._size += 1
        return node

    def unlink(self, node: Node) -> None:
        """Removes a node already in hand. O(1), which is the point.

        The four assignments cover both ends: a node at the head has no `prev`
        to update, so the list's `head` moves instead, and likewise for the
        tail. Getting those two cases wrong is how a doubly linked list ends
        up with a `head` pointing at a removed node.
        """
        if node.prev is None:
            self.head = node.next
        else:
            node.prev.next = node.next

        if node.next is None:
            self.tail = node.prev
        else:
            node.next.prev = node.prev

        # Cleared so a caller still holding the node cannot walk back into the
        # list it no longer belongs to.
        node.prev = None
        node.next = None
        self._size -= 1

    def remove(self, event_id: str) -> bool:
        """Removes by id. O(n), because finding it is the cost."""
        node = self.find(event_id)
        if node is None:
            return False
        self.unlink(node)
        return True

    def find(self, event_id: str) -> Node | None:
        current = self.head
        while current is not None:
            if current.data.event_id == event_id:
                return current
            current = current.next
        return None

    def forward(self) -> list[MonitoringEvent]:
        """Oldest first."""
        events = []
        current = self.head
        while current is not None:
            events.append(current.data)
            current = current.next
        return events

    def backward(self) -> list[MonitoringEvent]:
        """Newest first, walked from the tail.

        This is the method the second link buys. Reversing `forward()` would
        give the same answer by building the whole list and turning it around;
        this walks only as far as the caller reads.
        """
        events = []
        current = self.tail
        while current is not None:
            events.append(current.data)
            current = current.prev
        return events

    def __len__(self) -> int:
        return self._size


if __name__ == "__main__":
    history = EventHistory()
    handles = [
        history.append(MonitoringEvent(f"Event-{index:03d}", "cattle_out_of_zone", f"camera-0{index}", "cow", 0.9))
        for index in range(1, 4)
    ]
    print(f"Events in history: {len(history)}")
    print("Oldest first:", [event.event_id for event in history.forward()])
    print("Newest first:", [event.event_id for event in history.backward()])

    # Dismissing the alert currently on screen: the node is already held, so
    # no search is needed.
    history.unlink(handles[1])
    print(f"After dismissing Event-002: {len(history)}")
    print("Oldest first:", [event.event_id for event in history.forward()])
    print("Newest first:", [event.event_id for event in history.backward()])

    history.prepend(MonitoringEvent("Event-000", "cattle_out_of_zone", "camera-01", "cow", 0.95))
    print("After prepending an earlier event:", [event.event_id for event in history.forward()])
    print(f"Find Event-404: {history.find('Event-404')}")
