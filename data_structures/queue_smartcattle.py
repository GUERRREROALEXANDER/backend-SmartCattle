"""Academic example of how a queue could hold pending SmartCattle events.

Future flow: Detection → Event → EVENT QUEUE → Backend → Alert.
This example is not connected to YOLO or the backend.
"""

from collections import deque
from dataclasses import dataclass


@dataclass
class MonitoringEvent:
    event_id: str
    event_type: str
    camera_id: str
    detected_object: str
    confidence: float


class PendingEventQueue:
    def __init__(self) -> None:
        # deque.popleft() is O(1); list.pop(0) is O(n) because items shift.
        self._events: deque[MonitoringEvent] = deque()

    def enqueue(self, event: MonitoringEvent) -> None:
        self._events.append(event)

    def dequeue(self) -> MonitoringEvent:
        if self.is_empty():
            raise IndexError("Cannot dequeue from an empty event queue.")
        return self._events.popleft()

    def peek(self) -> MonitoringEvent:
        if self.is_empty():
            raise IndexError("Cannot peek at an empty event queue.")
        return self._events[0]

    def is_empty(self) -> bool:
        return len(self._events) == 0

    def size(self) -> int:
        return len(self._events)


if __name__ == "__main__":
    queue = PendingEventQueue()
    for event in [
        MonitoringEvent("Event-A", "cattle_out_of_zone", "camera-01", "cow", 0.95),
        MonitoringEvent("Event-B", "cattle_out_of_zone", "camera-02", "cow", 0.91),
        MonitoringEvent("Event-C", "cattle_out_of_zone", "camera-03", "cow", 0.98),
    ]:
        queue.enqueue(event)
        print(f"Enqueued {event.event_id} from {event.camera_id}")
    print(f"Queue size: {queue.size()}")
    print(f"Next event: {queue.peek().event_id}")
    while not queue.is_empty():
        event = queue.dequeue()
        print(f"Processing {event.event_id} from {event.camera_id}")
    try:
        queue.dequeue()
    except IndexError as error:
        print(f"Empty queue: {error}")
    try:
        queue.peek()
    except IndexError as error:
        print(f"Empty queue: {error}")
