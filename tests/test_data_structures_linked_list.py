"""Checks for the linked list example.

The delete method is the one worth testing properly: bypassing a node is how
a linked list loses the rest of its sequence, and the three cases -- head,
middle, tail -- take different branches.
"""

from data_structures.linked_list_smartcattle import EventLinkedList, MonitoringEvent


def event(event_id: str) -> MonitoringEvent:
    return MonitoringEvent(event_id, "cattle_out_of_zone", "camera-01", "cow", 0.95)


def ids(events: EventLinkedList) -> list[str]:
    """The sequence, read through the nodes.

    traverse() prints, so it cannot be asserted on directly; walking the nodes
    is what proves the links survived a delete.
    """
    found = []
    current = events.head
    while current is not None:
        found.append(current.data.event_id)
        current = current.next
    return found


def test_an_empty_list_has_no_head():
    events = EventLinkedList()
    assert events.head is None
    assert events.count() == 0


def test_insert_appends_at_the_end():
    events = EventLinkedList()
    for event_id in ["E1", "E2", "E3"]:
        events.insert(event(event_id))
    assert ids(events) == ["E1", "E2", "E3"]
    assert events.count() == 3


def test_search_finds_and_misses():
    events = EventLinkedList()
    events.insert(event("E1"))
    events.insert(event("E2"))
    assert events.search("E2").event_id == "E2"
    assert events.search("E404") is None


def test_deleting_the_head_keeps_the_tail():
    events = EventLinkedList()
    for event_id in ["E1", "E2", "E3"]:
        events.insert(event(event_id))
    assert events.delete("E1") is True
    assert ids(events) == ["E2", "E3"]
    assert events.head.data.event_id == "E2"


def test_deleting_the_middle_rejoins_the_sequence():
    # The case that loses the tail if the bypass is written wrong.
    events = EventLinkedList()
    for event_id in ["E1", "E2", "E3"]:
        events.insert(event(event_id))
    assert events.delete("E2") is True
    assert ids(events) == ["E1", "E3"]


def test_deleting_the_tail_leaves_a_terminated_list():
    events = EventLinkedList()
    for event_id in ["E1", "E2", "E3"]:
        events.insert(event(event_id))
    assert events.delete("E3") is True
    assert ids(events) == ["E1", "E2"]
    # The new last node must actually terminate, or traversal never ends.
    current = events.head
    while current.next is not None:
        current = current.next
    assert current.data.event_id == "E2"


def test_deleting_the_only_node_empties_the_list():
    events = EventLinkedList()
    events.insert(event("E1"))
    assert events.delete("E1") is True
    assert events.head is None
    assert events.count() == 0


def test_deleting_an_absent_id_reports_failure():
    events = EventLinkedList()
    events.insert(event("E1"))
    assert events.delete("E404") is False
    assert ids(events) == ["E1"]


def test_deleting_from_an_empty_list_reports_failure():
    assert EventLinkedList().delete("E1") is False


def test_inserting_after_emptying_still_works():
    # A head left pointing at a removed node would surface here.
    events = EventLinkedList()
    events.insert(event("E1"))
    events.delete("E1")
    events.insert(event("E2"))
    assert ids(events) == ["E2"]


def test_traverse_prints_the_sequence(capsys):
    events = EventLinkedList()
    events.insert(event("E1"))
    events.insert(event("E2"))
    events.traverse()
    printed = capsys.readouterr().out
    assert printed.strip() == "E1 -> E2 -> None"
