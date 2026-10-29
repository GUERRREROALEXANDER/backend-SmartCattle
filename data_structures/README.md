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
| Singly linked list | Nodes with one link | Event sequence |
| **Doubly linked list** | Nodes with two links | Event history reviewed in both directions |
| **Doubly circular list** | A ring with two links | Camera rotation in the live view |

The last two are not variations for their own sake. Each exists because a
specific SmartCattle screen needs something the single link cannot do: the
event review moves backwards, and the camera carousel has no end.

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

### Singly linked list

- **Principle:** Nodes holding data and a reference to the next node.
- **SmartCattle use:** Demonstrating dynamic storage and sequential traversal of events.
- **Key operations:** `insert`, `traverse`, `search`, `delete`, `count`.
- **Implementation:** Production Python code would use `list` or `deque`; explicit nodes serve as a teaching example here.
- **Limit:** One link means one direction. Stepping back costs a traversal
  from the head, and removing a node requires finding its predecessor first --
  even when the node is already in hand.

### Doubly linked list

- **Principle:** Each node links to both neighbours (`prev` and `next`); the
  list holds `head` and `tail`.
- **SmartCattle use:** The event history an operator reviews. Reading the
  night's alerts moves forward and back.
- **Key operations:** `append`, `prepend`, `unlink`, `remove`, `find`,
  `forward`, `backward`.
- **Why the second link:** two reasons, and the second is the practical one.
  `backward()` walks from the tail as far as the caller reads, instead of
  building the forward list and reversing it. And `unlink()` removes a node
  already held in O(1) -- four pointer assignments, no search -- which is
  exactly what dismissing the alert on screen is.
- **Where it breaks if written carelessly:** a node at either end has no
  neighbour to update, so the list's own `head` or `tail` has to move. Miss
  that and the list points at a removed node.

### Doubly circular list

- **Principle:** A doubly linked list closed into a ring: the last node's
  `next` is the first, and the first node's `prev` is the last. There is no
  head and no tail -- only a cursor.
- **SmartCattle use:** The camera rotation in the live view. After the last
  camera comes the first; stepping back from the first shows the last.
- **Key operations:** `add`, `advance`, `rewind`, `peek`, `remove`, `find`,
  `lap`, `next_active`.
- **Why a ring rather than a list and an index:** no wrap-around arithmetic,
  so there is no `% len` to get wrong -- and the backward direction is where
  that happens, since `(index - 1) % n` is easy to mistype. No special case at
  the ends, because every node has a real `prev` and `next`. And removing a
  retired camera keeps every other node's identity, where a list shifts
  everything after it and silently changes which camera the operator is
  watching.
- **The cost, stated plainly:** **no traversal terminates by itself.** A
  `while current is not None` loop over a ring never ends. Every walk here
  stops by counting, including `next_active()`, which returns `None` rather
  than searching forever when every camera is switched off.

Run each file from the repository root:

| File | Command |
|---|---|
| `array_smartcattle.py` | `python data_structures/array_smartcattle.py` |
| `stack_smartcattle.py` | `python data_structures/stack_smartcattle.py` |
| `queue_smartcattle.py` | `python data_structures/queue_smartcattle.py` |
| `linked_list_smartcattle.py` | `python data_structures/linked_list_smartcattle.py` |
