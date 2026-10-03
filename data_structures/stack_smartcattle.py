"""Academic example of how a stack could hold recent SmartCattle events."""

from dataclasses import dataclass


@dataclass
class MonitoringEvent:
    event_id: str
    event_type: str
    camera_id: str
    detected_object: str
    confidence: float


class RecentEventStack:
    # Operators want the newest alert first. GET /api/events already returns
    # newest-to-oldest events, matching LIFO order without using this example.
    def __init__(self) -> None:
        self._events: list[MonitoringEvent] = []

    def push(self, event: MonitoringEvent) -> None:
        # Appending at the end costs O(1) amortized; no front shifts are needed.
        self._events.append(event)

    def pop(self) -> MonitoringEvent:
        if self.is_empty():
            raise IndexError("Cannot pop from an empty event stack.")
        # Removing from the end is O(1).
        return self._events.pop()

    def peek(self) -> MonitoringEvent:
        if self.is_empty():
            raise IndexError("Cannot peek at an empty event stack.")
        return self._events[-1]

    def is_empty(self) -> bool:
        return len(self._events) == 0

    def size(self) -> int:
        return len(self._events)


if __name__ == "__main__":
    stack = RecentEventStack()
    for event in [
        MonitoringEvent("Event-001", "cattle_out_of_zone", "camera-01", "cow", 0.95),
        MonitoringEvent("Event-002", "cattle_out_of_zone", "camera-02", "cow", 0.91),
        MonitoringEvent("Event-003", "cattle_out_of_zone", "camera-03", "cow", 0.98),
    ]:
        stack.push(event)
        print(f"Pushed {event.event_id} from {event.camera_id}")
    print(f"Stack size: {stack.size()}")
    print(f"Latest event: {stack.peek().event_id}")
    while not stack.is_empty():
        print(f"Popped {stack.pop().event_id}")
    try:
        stack.pop()
    except IndexError as error:
        print(f"Empty stack: {error}")
    try:
        stack.peek()
    except IndexError as error:
        print(f"Empty stack: {error}")
