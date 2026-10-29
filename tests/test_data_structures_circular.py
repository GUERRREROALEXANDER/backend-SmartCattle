"""Checks for the doubly circular camera rotation.

A ring has no null to stop on, so the failures unique to it are a traversal
that never ends and a cursor left pointing outside the ring. Those are what
these tests are about; the rest of the behaviour is ordinary list behaviour.

Several cases are wrapped in a timeout, because the honest failure for an
unbounded walk is a hang rather than a wrong answer, and a hanging test is
worse than a failing one.
"""

import pytest

from data_structures.circular_list_smartcattle import CameraRotation, MonitoredCamera


def camera(camera_id: str, active: bool = True) -> MonitoredCamera:
    return MonitoredCamera(camera_id, f"Sector {camera_id}", active)


def filled(*ids: str) -> CameraRotation:
    rotation = CameraRotation()
    for camera_id in ids:
        rotation.add(camera(camera_id))
    return rotation


def ids(cameras: list[MonitoredCamera]) -> list[str]:
    return [item.camera_id for item in cameras]


def test_an_empty_rotation_has_no_cursor():
    rotation = CameraRotation()
    assert rotation.current is None
    assert len(rotation) == 0
    assert rotation.lap() == []
    assert rotation.next_active() is None


@pytest.mark.parametrize("operation", ["advance", "rewind", "peek"])
def test_an_empty_rotation_raises(operation):
    # Raising rather than returning None: there is no camera to describe, and
    # a None would be mistaken for one by the next line of caller code.
    with pytest.raises(IndexError, match="empty camera rotation"):
        getattr(CameraRotation(), operation)()


def test_one_camera_links_to_itself():
    # A ring of one is the base case that makes every other method
    # branch-free: both links are real, they just point back at the node.
    rotation = filled("camera-01")
    node = rotation.current
    assert node is not None
    assert node.next is node
    assert node.prev is node
    assert ids(rotation.lap()) == ["camera-01"]


@pytest.mark.timeout(5)
def test_one_camera_advances_to_itself_forever():
    rotation = filled("camera-01")
    for _ in range(5):
        assert rotation.advance().camera_id == "camera-01"
        assert rotation.rewind().camera_id == "camera-01"


def test_add_keeps_arrival_order_from_the_cursor():
    assert ids(filled("camera-01", "camera-02", "camera-03").lap()) == [
        "camera-01", "camera-02", "camera-03",
    ]


@pytest.mark.timeout(5)
def test_advancing_past_the_end_wraps_to_the_start():
    # The whole reason for the ring: no modulo, no branch, no end.
    rotation = filled("camera-01", "camera-02", "camera-03")
    assert [rotation.advance().camera_id for _ in range(4)] == [
        "camera-02", "camera-03", "camera-01", "camera-02",
    ]


@pytest.mark.timeout(5)
def test_rewinding_past_the_start_wraps_to_the_end():
    # The direction a list plus an index gets wrong, because -1 % n is where
    # the off-by-one lives.
    rotation = filled("camera-01", "camera-02", "camera-03")
    assert [rotation.rewind().camera_id for _ in range(4)] == [
        "camera-03", "camera-02", "camera-01", "camera-03",
    ]


def test_advancing_then_rewinding_returns_to_the_same_camera():
    rotation = filled("camera-01", "camera-02", "camera-03")
    rotation.advance()
    rotation.advance()
    before = rotation.peek().camera_id
    rotation.advance()
    assert rotation.rewind().camera_id == before


def test_peek_does_not_move_the_cursor():
    rotation = filled("camera-01", "camera-02")
    assert rotation.peek().camera_id == "camera-01"
    assert rotation.peek().camera_id == "camera-01"


@pytest.mark.timeout(5)
def test_a_lap_stops_after_one_circuit():
    # Bounded by the size, because `while current is not None` over a ring
    # never terminates. This is the test that would hang if it did.
    rotation = filled("camera-01", "camera-02", "camera-03")
    assert len(rotation.lap()) == 3


@pytest.mark.timeout(5)
def test_a_lap_starts_where_the_cursor_is():
    rotation = filled("camera-01", "camera-02", "camera-03")
    rotation.advance()
    assert ids(rotation.lap()) == ["camera-02", "camera-03", "camera-01"]


@pytest.mark.timeout(5)
def test_find_walks_at_most_one_lap():
    rotation = filled("camera-01", "camera-02", "camera-03")
    found = rotation.find("camera-03")
    assert found is not None and found.data.camera_id == "camera-03"
    # A miss has to terminate too, which is the case a null-terminated loop
    # would spin on forever.
    assert rotation.find("camera-404") is None


def test_find_on_an_empty_rotation_is_none():
    assert CameraRotation().find("camera-01") is None


@pytest.mark.timeout(5)
def test_removing_a_camera_keeps_the_ring_closed():
    rotation = filled("camera-01", "camera-02", "camera-03")
    assert rotation.remove("camera-02") is True
    assert ids(rotation.lap()) == ["camera-01", "camera-03"]
    # Still a ring: advancing twice comes back round.
    assert [rotation.advance().camera_id for _ in range(3)] == [
        "camera-03", "camera-01", "camera-03",
    ]


@pytest.mark.timeout(5)
def test_removing_the_camera_the_cursor_is_on_moves_the_cursor():
    # Leaving it where it was would point the view at a camera that is no
    # longer in the rotation.
    rotation = filled("camera-01", "camera-02", "camera-03")
    assert rotation.peek().camera_id == "camera-01"
    rotation.remove("camera-01")
    assert rotation.peek().camera_id == "camera-02"
    assert ids(rotation.lap()) == ["camera-02", "camera-03"]


def test_removing_the_only_camera_empties_the_rotation():
    # The cursor has to become None. Pointing it at the removed node would
    # leave a rotation of size zero that still answers peek().
    rotation = filled("camera-01")
    assert rotation.remove("camera-01") is True
    assert rotation.current is None
    assert len(rotation) == 0
    with pytest.raises(IndexError):
        rotation.peek()


def test_removing_an_absent_camera_reports_failure():
    rotation = filled("camera-01")
    assert rotation.remove("camera-404") is False
    assert len(rotation) == 1


def test_removing_from_an_empty_rotation_reports_failure():
    assert CameraRotation().remove("camera-01") is False


@pytest.mark.timeout(5)
def test_a_removed_camera_cannot_lead_back_into_the_rotation():
    rotation = filled("camera-01", "camera-02", "camera-03")
    node = rotation.find("camera-02")
    assert node is not None
    rotation.remove("camera-02")
    # Isolated into a ring of one, so a caller still holding it cannot walk
    # into the rotation it has left.
    assert node.next is node
    assert node.prev is node


@pytest.mark.timeout(5)
def test_adding_after_emptying_still_works():
    rotation = filled("camera-01")
    rotation.remove("camera-01")
    rotation.add(camera("camera-02"))
    assert ids(rotation.lap()) == ["camera-02"]
    assert rotation.peek().camera_id == "camera-02"


@pytest.mark.timeout(5)
def test_next_active_skips_a_camera_that_is_switched_off():
    # A retired camera should be skipped rather than shown as a blank panel.
    rotation = CameraRotation()
    rotation.add(camera("camera-01"))
    rotation.add(camera("camera-02", active=False))
    rotation.add(camera("camera-03"))
    assert rotation.next_active().camera_id == "camera-03"


@pytest.mark.timeout(5)
def test_next_active_returns_none_when_every_camera_is_off():
    # The bug this method exists to not have: unbounded, it would spin forever
    # looking for an active camera that is not there.
    rotation = CameraRotation()
    for camera_id in ["camera-01", "camera-02"]:
        rotation.add(camera(camera_id, active=False))
    assert rotation.next_active() is None


@pytest.mark.timeout(5)
def test_next_active_on_a_single_active_camera_returns_it():
    rotation = filled("camera-01")
    assert rotation.next_active().camera_id == "camera-01"


@pytest.mark.timeout(5)
def test_removing_every_camera_in_any_order_leaves_an_empty_ring():
    for order in [
        ["camera-01", "camera-02", "camera-03"],
        ["camera-03", "camera-02", "camera-01"],
        ["camera-02", "camera-01", "camera-03"],
    ]:
        rotation = filled("camera-01", "camera-02", "camera-03")
        for camera_id in order:
            assert rotation.remove(camera_id) is True
        assert len(rotation) == 0
        assert rotation.current is None
        assert rotation.lap() == []
