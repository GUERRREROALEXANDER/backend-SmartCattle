import pytest
from starlette.websockets import WebSocketDisconnect

from app.routes import live

LIVE_URL = "/ws/live"


def closed_with(client, *messages, text=None):
    """Connects, sends the given messages and returns the close code the server chose."""
    with pytest.raises(WebSocketDisconnect) as closed, client.websocket_connect(LIVE_URL) as socket:
        if text is not None:
            socket.send_text(text)
        for message in messages:
            socket.send_json(message)
        socket.receive_json()
    return closed.value.code


def signed_in(socket, token):
    socket.send_json({"type": "auth", "token": token})
    return socket.receive_json()


def test_a_valid_token_opens_the_stream(auth_client, owner):
    with auth_client.websocket_connect(LIVE_URL) as socket:
        hello = signed_in(socket, owner["access_token"])
    assert hello["type"] == "hello"
    assert hello["data"] == {"user_id": owner["user"]["id"], "role": "owner"}
    assert hello["sent_at"].endswith("+00:00")


@pytest.mark.parametrize("message", [
    {"type": "auth", "token": "not-a-token"},
    {"type": "auth", "token": ""},
    {"type": "auth"},
    {"type": "ping"},
    ["auth"],
])
def test_anything_but_a_valid_auth_message_is_refused(auth_client, owner, message):
    assert closed_with(auth_client, message) == live.CLOSE_UNAUTHORIZED


def test_a_first_message_that_is_not_json_is_refused(auth_client, owner):
    assert closed_with(auth_client, text="hello?") == live.CLOSE_UNAUTHORIZED


def test_a_silent_client_is_dropped(auth_client, owner, monkeypatch):
    monkeypatch.setattr(live, "AUTH_TIMEOUT_SECONDS", 0.05)
    assert closed_with(auth_client) == live.CLOSE_UNAUTHORIZED


def test_a_deactivated_account_cannot_open_the_stream(auth_client, owner_headers, worker):
    auth_client.patch(f"/api/users/{worker['user']['id']}", json={"is_active": False}, headers=owner_headers)
    message = {"type": "auth", "token": worker["access_token"]}
    assert closed_with(auth_client, message) == live.CLOSE_UNAUTHORIZED


def test_without_authentication_configured_the_stream_is_unavailable(client):
    assert closed_with(client) == live.CLOSE_AUTH_NOT_CONFIGURED


def test_a_ping_is_answered(auth_client, owner):
    with auth_client.websocket_connect(LIVE_URL) as socket:
        signed_in(socket, owner["access_token"])
        socket.send_text("not json")
        socket.send_json({"type": "something-else"})
        socket.send_json({"type": "ping"})
        assert socket.receive_json()["type"] == "pong"


def test_an_ai_event_reaches_the_dashboard_with_its_alert(auth_client, worker, event_payload):
    with auth_client.websocket_connect(LIVE_URL) as socket:
        signed_in(socket, worker["access_token"])
        event = auth_client.post("/api/ai/events", json=event_payload).json()
        created = socket.receive_json()
        alert = socket.receive_json()
    assert created["type"] == "event.created" and created["data"] == event
    assert alert["type"] == "alert.created"
    assert alert["data"]["event_id"] == event["id"]
    assert alert["data"]["status"] == "open"


def test_handling_an_alert_is_pushed_to_every_dashboard(auth_client, owner, owner_headers, worker, event_payload):
    auth_client.post("/api/ai/events", json=event_payload)
    alert_id = auth_client.get("/api/alerts", headers=owner_headers).json()["items"][0]["id"]
    with auth_client.websocket_connect(LIVE_URL) as socket:
        signed_in(socket, worker["access_token"])
        auth_client.post(f"/api/alerts/{alert_id}/acknowledge", headers=owner_headers)
        update = socket.receive_json()
    assert update["type"] == "alert.updated"
    assert update["data"]["status"] == "acknowledged"
    assert update["data"]["acknowledged_by"] == owner["user"]["id"]


def test_a_camera_report_is_pushed(auth_client, owner):
    with auth_client.websocket_connect(LIVE_URL) as socket:
        signed_in(socket, owner["access_token"])
        auth_client.put("/api/ai/cameras/camera-01/status", json={
            "status": "online", "frame_width": 640, "frame_height": 360, "fps": 10,
            "observed_at": "2026-10-31T15:30:00Z",
        })
        message = socket.receive_json()
    assert message["type"] == "camera.status"
    assert message["data"]["id"] == "camera-01"


def test_a_removed_camera_is_announced_and_its_late_reports_are_not_pushed(auth_client, owner):
    status_url, source_url = "/api/ai/cameras/cam-1/status", "/api/ai/cameras/cam-1/source"
    report = {"frame_width": 640, "frame_height": 360, "fps": 10, "observed_at": "2026-10-31T15:30:00Z"}
    with auth_client.websocket_connect(LIVE_URL) as socket:
        signed_in(socket, owner["access_token"])
        auth_client.put(source_url, json={"source": "0", "active": False})
        removed = socket.receive_json()
        auth_client.put(status_url, json={**report, "status": "offline"})
        auth_client.put(source_url, json={"source": "0"})
        auth_client.put(status_url, json={**report, "status": "online"})
        next_push = socket.receive_json()
    assert removed["type"] == "camera.removed"
    assert removed["data"] == {"id": "cam-1"}
    # The offline report came while it was removed: only the one after adding it again arrives.
    assert (next_push["type"], next_push["data"]["reported_status"]) == ("camera.status", "online")


def test_closing_the_socket_unsubscribes(auth_client, owner):
    with auth_client.websocket_connect(LIVE_URL) as socket:
        signed_in(socket, owner["access_token"])
        assert auth_client.app.state.broadcaster.subscriber_count == 1
    # The server notices the close on its next receive; a round trip on a
    # fresh connection guarantees that has happened.
    with auth_client.websocket_connect(LIVE_URL) as socket:
        signed_in(socket, owner["access_token"])
        assert auth_client.app.state.broadcaster.subscriber_count == 1


def test_the_server_can_upgrade_to_websockets():
    """TestClient does not need this library, but uvicorn does: without it /ws/live is a 404 in production."""
    import importlib.util

    assert importlib.util.find_spec("websockets") is not None
