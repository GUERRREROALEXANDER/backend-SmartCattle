"""Academic example of how linked nodes could sequence SmartCattle events."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MonitoringEvent:
    event_id: str
    event_type: str
    camera_id: str
    detected_object: str
    confidence: float


class Node:
    def __init__(self, data: MonitoringEvent) -> None:
        self.data: MonitoringEvent = data
        self.next: Node | None = None


class EventLinkedList:
    # Production Python would use list or deque; nodes here teach how data
    # and a reference to the next node form a sequence.
    def __init__(self) -> None:
        self.head: Node | None = None

    def insert(self, event: MonitoringEvent) -> None:
        node = Node(event)
        if self.head is None:
            self.head = node
            return
        current = self.head
        while current.next is not None:
            current = current.next
        current.next = node

    def traverse(self) -> None:
        current = self.head
        while current is not None:
            print(current.data.event_id, end=" -> ")
            current = current.next
        print("None")

    def search(self, event_id: str) -> MonitoringEvent | None:
        current = self.head
        while current is not None:
            if current.data.event_id == event_id:
                return current.data
            current = current.next
        return None

    def delete(self, event_id: str) -> bool:
        if self.head is None:
            return False
        if self.head.data.event_id == event_id:
            self.head = self.head.next
            return True
        current = self.head
        while current.next is not None:
            if current.next.data.event_id == event_id:
                # Bypass the matching node to preserve the remaining sequence.
                current.next = current.next.next
                return True
            current = current.next
        return False

    def count(self) -> int:
        total = 0
        current = self.head
        while current is not None:
            total += 1
            current = current.next
        return total


if __name__ == "__main__":
    events = EventLinkedList()
    events.insert(MonitoringEvent("Event-001", "cattle_out_of_zone", "camera-01", "cow", 0.95))
    events.insert(MonitoringEvent("Event-002", "cattle_out_of_zone", "camera-02", "cow", 0.91))
    events.insert(MonitoringEvent("Event-003", "cattle_out_of_zone", "camera-03", "cow", 0.98))
    events.traverse()
    print(f"Event count: {events.count()}")
    print(f"Find Event-002: {events.search('Event-002')}")
    print(f"Find Event-099: {events.search('Event-099')}")
    print(f"Delete middle Event-002: {events.delete('Event-002')}")
    events.traverse()
    print(f"Delete head Event-001: {events.delete('Event-001')}")
    events.traverse()
    print(f"Event count: {events.count()}")
