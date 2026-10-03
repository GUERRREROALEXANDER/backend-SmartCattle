from datetime import datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.event import AIEventCreate
from app.services.event_store import InMemoryEventStore


def test_events_start_empty(client):
    response = client.get("/api/events")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}


def test_create_event(client, event_payload):
    response = client.post("/api/ai/events", json=event_payload)
    assert response.status_code == 201
    event = response.json()
    assert UUID(event["id"]).version == 4
    assert datetime.fromisoformat(event["received_at"]).utcoffset() == timedelta(0)
    for field, value in event_payload.items():
        assert event[field] == value
    assert client.get("/api/events").json() == {"items": [event], "total": 1}


@pytest.mark.parametrize("changes", [
    {"confidence": 1.5}, {"confidence": -0.1},
    {"timestamp": "2026-10-03T15:30:00"},
    {"event_type": "unknown"}, {"extra_field": "unexpected"},
    {"camera_id": ""}, {"camera_id": "   "}, {"camera_id": "x" * 65},
    {"detected_object": "   "}, {"detected_object": "x" * 65},
])
def test_invalid_event(client, event_payload, changes):
    response = client.post("/api/ai/events", json=event_payload | changes)
    assert response.status_code == 422
    assert client.get("/api/events").json()["total"] == 0


def test_identifiers_are_trimmed(client, event_payload):
    response = client.post("/api/ai/events", json=event_payload | {
        "camera_id": " camera-01 ", "detected_object": " cow ",
    })
    assert response.status_code == 201
    assert response.json()["camera_id"] == "camera-01"
    assert response.json()["detected_object"] == "cow"


@pytest.mark.parametrize("key,expected", [(None, 401), ("incorrect", 401), ("test-shared-key", 201)])
def test_api_key(secured_client, event_payload, key, expected):
    headers = {} if key is None else {"X-API-Key": key}
    response = secured_client.post("/api/ai/events", json=event_payload, headers=headers)
    assert response.status_code == expected
    if expected == 401:
        assert response.json() == {"detail": "Invalid or missing API key"}
        assert secured_client.get("/api/events").json()["total"] == 0


def test_store_capacity_and_reception_order(event_payload):
    store = InMemoryEventStore()
    created = []
    for index in range(1002):
        data = AIEventCreate(**(event_payload | {"camera_id": str(index)}))
        created.append(store.add(data))
    events = store.list()
    assert len(events) == 1000
    assert events == list(reversed(created[2:]))


def test_apps_have_isolated_stores(client, settings, event_payload):
    assert client.post("/api/ai/events", json=event_payload).status_code == 201
    with TestClient(create_app(settings)) as other:
        assert other.get("/api/events").json() == {"items": [], "total": 0}
