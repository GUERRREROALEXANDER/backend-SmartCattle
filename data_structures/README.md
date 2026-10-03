# SmartCattle data structure examples

These independent academic examples show how data structures could apply to
SmartCattle. They use only the Python standard library and are not used by the API.
Animal identities assume future identification; event IDs are teaching labels,
not backend UUIDs. The event records are simplified examples, not API payloads.

| Structure | Principle | SmartCattle application |
|---|---|---|
| List/Array | Indexed access and traversal | Currently detected animals |
| Stack | LIFO | Recent event history |
| Queue | FIFO | Pending events |
| Linked list | Linked nodes | Event sequence |

### List/Array

- **Principle:** Indexed access and traversal.
- **SmartCattle use:** Keeping a temporary collection of currently detected animals.
- **Key operations:** `add`, `find`, `update`, `remove`, `show`, `count_by_status`.

### Stack

- **Principle:** LIFO (last in, first out).
- **SmartCattle use:** Handling recent information in LIFO order (newest event first, the same order `GET /api/events` returns).
- **Key operations:** `push`, `pop`, `peek`, `is_empty`, `size`.

### Queue

- **Principle:** FIFO (first in, first out).
- **SmartCattle use:** Handling events pending processing in FIFO order (future flow: Detection → Event → Queue → Backend → Alert).
- **Key operations:** `enqueue`, `dequeue`, `peek`, `is_empty`, `size`.
- **Implementation:** `collections.deque` provides O(1) `popleft()`, whereas `list.pop(0)` takes O(n) because the remaining items shift.

### Linked list

- **Principle:** Nodes holding data and a reference to the next node.
- **SmartCattle use:** Demonstrating dynamic storage and sequential traversal of events.
- **Key operations:** `insert`, `traverse`, `search`, `delete`, `count`.
- **Implementation:** Production Python code would use `list` or `deque`; explicit nodes serve as a teaching example here.

Run each file from the repository root:

| File | Command |
|---|---|
| `array_smartcattle.py` | `python data_structures/array_smartcattle.py` |
| `stack_smartcattle.py` | `python data_structures/stack_smartcattle.py` |
| `queue_smartcattle.py` | `python data_structures/queue_smartcattle.py` |
| `linked_list_smartcattle.py` | `python data_structures/linked_list_smartcattle.py` |
