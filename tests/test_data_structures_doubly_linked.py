"""Checks for the doubly linked event history.

The two links are what this structure is for, so the tests are about what the
second one buys: walking backwards without rebuilding the list, and unlinking
a node already in hand. Everything else a singly linked list already did.
"""

import pytest

from data_structures.doubly_linked_list_smartcattle import EventHistory, MonitoringEvent


def event(event_id: str) -> MonitoringEvent:
    return MonitoringEvent(event_id, "cattle_out_of_zone", "camera-01", "cow", 0.95)


def filled(*ids: str) -> EventHistory:
    history = EventHistory()
    for event_id in ids:
        history.append(event(event_id))
    return history


def ids(events: list[MonitoringEvent]) -> list[str]:
    return [item.event_id for item in events]


def test_an_empty_history_has_no_ends():
    history = EventHistory()
    assert history.head is None
    assert history.tail is None
    assert len(history) == 0
    assert history.forward() == []
    assert history.backward() == []


def test_append_keeps_arrival_order():
    assert ids(filled("E1", "E2", "E3").forward()) == ["E1", "E2", "E3"]


def test_backward_reads_from_the_tail():
    # The method the second link exists for. Without `prev` this would have to
    # build the forward list and reverse it.
    assert ids(filled("E1", "E2", "E3").backward()) == ["E3", "E2", "E1"]


def test_the_ends_point_at_the_right_nodes():
    history = filled("E1", "E2", "E3")
    assert history.head is not None and history.head.data.event_id == "E1"
    assert history.tail is not None and history.tail.data.event_id == "E3"
    # The ends terminate. A `prev` on the head or a `next` on the tail would
    # make either traversal run past the list.
    assert history.head.prev is None
    assert history.tail.next is None


def test_the_links_agree_in_both_directions():
    # Walking forward and walking back must visit the same nodes. A single
    # mismatched pointer gives two different lists depending on the direction,
    # which is the failure mode unique to this structure.
    history = filled("E1", "E2", "E3", "E4")
    assert ids(history.forward()) == list(reversed(ids(history.backward())))


def test_prepend_adds_at_the_front():
    history = filled("E2", "E3")
    history.prepend(event("E1"))
    assert ids(history.forward()) == ["E1", "E2", "E3"]
    assert ids(history.backward()) == ["E3", "E2", "E1"]


def test_prepend_into_an_empty_history_sets_both_ends():
    history = EventHistory()
    history.prepend(event("E1"))
    assert history.head is history.tail
    assert len(history) == 1


def test_append_returns_a_handle_that_can_be_unlinked():
    # The point of returning the node: dismissing the alert on screen is
    # "remove the node I am holding", with no search.
    history = EventHistory()
    history.append(event("E1"))
    handle = history.append(event("E2"))
    history.append(event("E3"))

    history.unlink(handle)
    assert ids(history.forward()) == ["E1", "E3"]
    assert ids(history.backward()) == ["E3", "E1"]
    assert len(history) == 2


def test_unlinking_the_head_moves_the_head():
    # The node has no `prev` to update, so the list's own head has to move.
    # Forgetting this leaves head pointing at a removed node.
    history = filled("E1", "E2", "E3")
    assert history.head is not None
    history.unlink(history.head)
    assert history.head is not None and history.head.data.event_id == "E2"
    assert history.head.prev is None
    assert ids(history.backward()) == ["E3", "E2"]


def test_unlinking_the_tail_moves_the_tail():
    history = filled("E1", "E2", "E3")
    assert history.tail is not None
    history.unlink(history.tail)
    assert history.tail is not None and history.tail.data.event_id == "E2"
    assert history.tail.next is None
    assert ids(history.forward()) == ["E1", "E2"]


def test_unlinking_the_only_node_empties_both_ends():
    history = EventHistory()
    handle = history.append(event("E1"))
    history.unlink(handle)
    assert history.head is None
    assert history.tail is None
    assert len(history) == 0


def test_an_unlinked_node_cannot_walk_back_into_the_list():
    # Its links are cleared, so a caller still holding it reads an isolated
    # node rather than a path into a list it no longer belongs to.
    history = filled("E1", "E2", "E3")
    handle = history.find("E2")
    assert handle is not None
    history.unlink(handle)
    assert handle.prev is None
    assert handle.next is None


def test_remove_by_id_reports_whether_it_removed():
    history = filled("E1", "E2")
    assert history.remove("E2") is True
    assert history.remove("E2") is False
    assert ids(history.forward()) == ["E1"]


def test_removing_from_an_empty_history_reports_failure():
    assert EventHistory().remove("E1") is False


def test_find_locates_and_misses():
    history = filled("E1", "E2")
    found = history.find("E2")
    assert found is not None and found.data.event_id == "E2"
    assert history.find("E404") is None


def test_appending_after_emptying_still_works():
    # A stale head or tail left over from the removal would surface here.
    history = EventHistory()
    history.unlink(history.append(event("E1")))
    history.append(event("E2"))
    assert ids(history.forward()) == ["E2"]
    assert ids(history.backward()) == ["E2"]
    assert len(history) == 1


@pytest.mark.parametrize("removals", [
    ["E1", "E2", "E3"],
    ["E3", "E2", "E1"],
    ["E2", "E1", "E3"],
    ["E2", "E3", "E1"],
])
def test_removing_everything_in_any_order_leaves_a_consistent_list(removals):
    # Each order exercises a different combination of the head and tail
    # branches, and all four have to end with both ends cleared.
    history = filled("E1", "E2", "E3")
    for event_id in removals:
        assert history.remove(event_id) is True
    assert len(history) == 0
    assert history.head is None
    assert history.tail is None
    assert history.forward() == []
    assert history.backward() == []
