"""Academic example of a doubly circular linked list over the camera rotation.

The live monitoring view cycles through cameras: after the last one it shows
the first again, and stepping back from the first shows the last. That is a
ring, and a ring is what this structure is.

Why it is the right shape, rather than a list plus an index:

1. **No wrap-around arithmetic.** With a list you write
   `(index + 1) % len(cameras)` at every call site, and the modulo is where
   the off-by-one lives -- especially going backwards, where `-1 % n` behaves
   differently across languages and `(index - 1) % n` is easy to write as
   `index - 1 % n`.

2. **No special case at the ends.** Every node has a real `prev` and a real
   `next`, so "advance" is one assignment with no branch. The ends are where a
   carousel breaks, and here there are none.

3. **Removing a camera does not move the others.** Unlinking a retired camera
   is O(1) and leaves every other node's identity intact. A list plus an index
   shifts everything after the removal, so whichever camera the operator was
   watching silently becomes a different one.

The cost is the cost of every circular structure: **no traversal terminates by
itself.** Every walk here stops by counting, and each one says so, because a
`while current is not None` loop over a ring never ends.

Not used by the API. The camera names match the ones the real API uses, but
the records are simplified teaching examples.
"""

from dataclasses import dataclass


@dataclass
class MonitoredCamera:
    camera_id: str
    location: str
    active: bool = True


class Node:
    """One camera. In a populated ring, both links are always real nodes."""

    def __init__(self, data: MonitoredCamera) -> None:
        self.data: MonitoredCamera = data
        self.prev: Node = self
        self.next: Node = self


class CameraRotation:
    """A ring of cameras with a cursor, walked in either direction.

    `current` is where the view is pointed. It is the only entry point: there
    is no head and no tail, because in a ring neither exists.
    """

    def __init__(self) -> None:
        self.current: Node | None = None
        self._size = 0

    def add(self, camera: MonitoredCamera) -> Node:
        """Inserts a camera before the cursor, so additions keep arrival order.

        Inserting "before current" on a ring is the same as appending to the
        end of a list whose start is the cursor -- which is why a single
        insertion point is enough.
        """
        node = Node(camera)
        if self.current is None:
            # First node: a ring of one, linked to itself. Node.__init__
            # already does that, which is why there is no special case to
            # write here.
            self.current = node
        else:
            last = self.current.prev
            node.prev = last
            node.next = self.current
            last.next = node
            self.current.prev = node
        self._size += 1
        return node

    def advance(self) -> MonitoredCamera:
        """Moves to the next camera. Wraps with no arithmetic and no branch."""
        if self.current is None:
            raise IndexError("Cannot advance an empty camera rotation.")
        self.current = self.current.next
        return self.current.data

    def rewind(self) -> MonitoredCamera:
        """Moves to the previous camera. The mirror, and the reason for `prev`."""
        if self.current is None:
            raise IndexError("Cannot rewind an empty camera rotation.")
        self.current = self.current.prev
        return self.current.data

    def peek(self) -> MonitoredCamera:
        if self.current is None:
            raise IndexError("Cannot read an empty camera rotation.")
        return self.current.data

    def remove(self, camera_id: str) -> bool:
        """Unlinks a camera and keeps the cursor usable.

        Two cases have to be handled or the ring breaks. Removing the only node
        empties the rotation, so the cursor becomes None rather than pointing
        at a node outside the ring. Removing the node the cursor is on moves
        the cursor forward first; leaving it where it was would leave the view
        pointed at a camera that is no longer in the rotation.
        """
        node = self.find(camera_id)
        if node is None:
            return False

        if self._size == 1:
            self.current = None
            self._size = 0
            return True

        if node is self.current:
            self.current = node.next

        node.prev.next = node.next
        node.next.prev = node.prev
        # Pointed at itself, so a caller still holding it sees a ring of one
        # rather than a way back into a rotation it has left.
        node.prev = node
        node.next = node
        self._size -= 1
        return True

    def find(self, camera_id: str) -> Node | None:
        """Walks at most one full lap.

        The counter is the termination condition. A ring has no null to stop
        on, so a loop written the way the singly linked version writes it would
        never return.
        """
        if self.current is None:
            return None
        current = self.current
        for _ in range(self._size):
            if current.data.camera_id == camera_id:
                return current
            current = current.next
        return None

    def lap(self) -> list[MonitoredCamera]:
        """One full lap from the cursor, in order, stopping by count."""
        if self.current is None:
            return []
        cameras = []
        current = self.current
        for _ in range(self._size):
            cameras.append(current.data)
            current = current.next
        return cameras

    def next_active(self) -> MonitoredCamera | None:
        """The next camera that is switched on, or None if none is.

        What a real carousel needs: a retired camera should be skipped rather
        than shown as a blank panel. Bounded by the size, so a rotation where
        every camera is inactive returns None instead of spinning forever --
        which is the bug this method exists to not have.
        """
        if self.current is None:
            return None
        for _ in range(self._size):
            self.current = self.current.next
            if self.current.data.active:
                return self.current.data
        return None

    def __len__(self) -> int:
        return self._size


if __name__ == "__main__":
    rotation = CameraRotation()
    for index, location in enumerate(["Porton norte", "Bebedero", "Potrero sur"], start=1):
        rotation.add(MonitoredCamera(f"camera-0{index}", location))
    print(f"Cameras in rotation: {len(rotation)}")
    print("One lap:", [camera.camera_id for camera in rotation.lap()])

    print("Forward through the ring, past the end:")
    for _ in range(4):
        print(f"  {rotation.advance().camera_id}")

    print("Backward through the ring, past the start:")
    for _ in range(4):
        print(f"  {rotation.rewind().camera_id}")

    rotation.find("camera-02").data.active = False  # type: ignore[union-attr]
    print(f"Skipping the camera that was switched off: {rotation.next_active().camera_id}")  # type: ignore[union-attr]

    print(f"Remove camera-01: {rotation.remove('camera-01')}")
    print("One lap:", [camera.camera_id for camera in rotation.lap()])
    print(f"Remove camera-404: {rotation.remove('camera-404')}")

    rotation.remove("camera-02")
    rotation.remove("camera-03")
    print(f"Empty rotation: {len(rotation)} cameras")
    try:
        rotation.advance()
    except IndexError as error:
        print(f"Empty rotation: {error}")
