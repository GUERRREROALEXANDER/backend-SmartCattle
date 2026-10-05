from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.event import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, AIEventCreate
from app.services.event_store import InMemoryEventStore
from tests.conftest import INGEST_KEY, READ_KEY


def test_events_start_empty(client):
    response = client.get("/api/events")
    assert response.status_code == 200
    assert response.json() == {
        "items": [], "total": 0, "limit": DEFAULT_PAGE_SIZE, "offset": 0,
    }


def test_create_event(client, event_payload):
    response = client.post("/api/ai/events", json=event_payload)
    assert response.status_code == 201
    event = response.json()
    assert UUID(event["id"]).version == 4
    assert datetime.fromisoformat(event["received_at"]).utcoffset() == timedelta(0)
    for field, value in event_payload.items():
        assert event[field] == value
    assert client.get("/api/events").json() == {
        "items": [event], "total": 1, "limit": DEFAULT_PAGE_SIZE, "offset": 0,
    }


@pytest.mark.parametrize("changes", [
    {"confidence": 1.5}, {"confidence": -0.1},
    {"timestamp": "2026-10-03T15:30:00"},
    {"event_type": "unknown"}, {"extra_field": "unexpected"},
    {"camera_id": ""}, {"camera_id": "   "}, {"camera_id": "x" * 65},
    {"detected_object": "   "}, {"detected_object": "x" * 65},
    {"ai_event_id": "not-a-uuid"},
])
def test_invalid_event(client, event_payload, changes):
    response = client.post("/api/ai/events", json=event_payload | changes)
    assert response.status_code == 422
    assert client.get("/api/events").json()["total"] == 0


def test_missing_ai_event_id_is_rejected(client, event_payload):
    del event_payload["ai_event_id"]
    response = client.post("/api/ai/events", json=event_payload)
    assert response.status_code == 422
    assert client.get("/api/events").json()["total"] == 0


def test_rejected_values_are_not_echoed_back(client, event_payload):
    response = client.post("/api/ai/events", json=event_payload | {"camera_id": "<script>" * 20})
    assert response.status_code == 422
    assert "<script>" not in response.text
    assert response.json()["detail"][0]["loc"] == ["body", "camera_id"]


@pytest.mark.parametrize("raw_confidence", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_confidence_is_rejected_without_a_server_error(client, raw_confidence):
    body = (
        '{"ai_event_id":"0f8bc0f2-8e4d-4c6e-9a3b-5d7c1e2f4a60",'
        '"event_type":"cattle_out_of_zone","camera_id":"camera-01",'
        '"detected_object":"cow","confidence":' + raw_confidence + ','
        '"timestamp":"2026-10-03T15:30:00Z"}'
    )
    response = client.post(
        "/api/ai/events", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert client.get("/api/events").json()["total"] == 0


def test_identifiers_are_trimmed(client, event_payload):
    response = client.post("/api/ai/events", json=event_payload | {
        "camera_id": " camera-01 ", "detected_object": " cow ",
    })
    assert response.status_code == 201
    assert response.json()["camera_id"] == "camera-01"
    assert response.json()["detected_object"] == "cow"


def test_offsets_are_normalized_to_utc(client, event_payload):
    response = client.post(
        "/api/ai/events", json=event_payload | {"timestamp": "2026-10-03T10:30:00-05:00"}
    )
    assert response.status_code == 201
    assert response.json()["timestamp"] == "2026-10-03T15:30:00Z"


def test_resending_the_same_ai_event_id_does_not_duplicate(client, event_payload):
    first = client.post("/api/ai/events", json=event_payload)
    assert first.status_code == 201
    for _ in range(3):
        retry = client.post("/api/ai/events", json=event_payload)
        assert retry.status_code == 200
        assert retry.json() == first.json()
    assert client.get("/api/events").json()["total"] == 1


def test_identical_payloads_with_distinct_ids_are_both_stored(client, event_payload):
    """Two animals detected in the same second are two events, not a duplicate."""
    first = client.post("/api/ai/events", json=event_payload)
    second = client.post("/api/ai/events", json=event_payload | {"ai_event_id": str(uuid4())})
    assert (first.status_code, second.status_code) == (201, 201)
    assert client.get("/api/events").json()["total"] == 2


@pytest.mark.parametrize("key,expected", [
    (None, 401), ("incorrect", 401), (READ_KEY, 401), (INGEST_KEY, 201),
])
def test_ingest_key(secured_client, event_payload, key, expected):
    headers = {} if key is None else {"X-API-Key": key}
    response = secured_client.post("/api/ai/events", json=event_payload, headers=headers)
    assert response.status_code == expected
    if expected == 401:
        assert response.json() == {"detail": "Invalid or missing API key"}
        assert secured_client.get(
            "/api/events", headers={"X-API-Key": READ_KEY}
        ).json()["total"] == 0


@pytest.mark.parametrize("path", ["/api/events", "/api/animals"])
@pytest.mark.parametrize("key,expected", [
    (None, 401), ("incorrect", 401), (INGEST_KEY, 401), (READ_KEY, 200),
])
def test_read_key(secured_client, path, key, expected):
    headers = {} if key is None else {"X-API-Key": key}
    response = secured_client.get(path, headers=headers)
    assert response.status_code == expected
    if expected == 401:
        assert response.json() == {"detail": "Invalid or missing API key"}


@pytest.mark.parametrize("path", ["/", "/health", "/api/status"])
def test_public_endpoints_need_no_key(secured_client, path):
    assert secured_client.get(path).status_code == 200


def test_store_capacity_and_reception_order(event_payload):
    store = InMemoryEventStore()
    created = []
    for index in range(1002):
        data = AIEventCreate(**(event_payload | {
            "ai_event_id": str(uuid4()), "camera_id": str(index),
        }))
        event, was_created = store.add(data)
        assert was_created
        created.append(event)
    assert store.count() == 1000
    events = store.list(limit=MAX_PAGE_SIZE)
    assert len(events) == 1000
    assert events == list(reversed(created[2:]))


def test_apps_have_isolated_stores(client, settings, event_payload):
    assert client.post("/api/ai/events", json=event_payload).status_code == 201
    with TestClient(create_app(settings)) as other:
        assert other.get("/api/events").json()["total"] == 0


def _store_events(client, event_payload, count):
    created = []
    for index in range(count):
        response = client.post("/api/ai/events", json=event_payload | {
            "ai_event_id": str(uuid4()), "camera_id": f"camera-{index}",
        })
        assert response.status_code == 201
        created.append(response.json())
    return list(reversed(created))  # newest first, as the endpoint returns them


def test_pages_cover_every_event_exactly_once(client, event_payload):
    expected = _store_events(client, event_payload, 25)
    collected = []
    for offset in range(0, 25, 10):
        page = client.get("/api/events", params={"limit": 10, "offset": offset}).json()
        assert page["total"] == 25
        assert page["limit"] == 10
        assert page["offset"] == offset
        collected.extend(page["items"])
    assert collected == expected
    assert len({item["id"] for item in collected}) == 25


def test_total_counts_every_event_not_the_page(client, event_payload):
    _store_events(client, event_payload, 25)
    page = client.get("/api/events", params={"limit": 5}).json()
    assert len(page["items"]) == 5
    assert page["total"] == 25


def test_offset_past_the_end_returns_an_empty_page(client, event_payload):
    _store_events(client, event_payload, 3)
    page = client.get("/api/events", params={"offset": 10}).json()
    assert page["items"] == []
    assert page["total"] == 3


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": -1}, {"limit": MAX_PAGE_SIZE + 1},
    {"offset": -1}, {"limit": "many"}, {"offset": 1.5},
])
def test_invalid_pagination_is_rejected(client, params):
    assert client.get("/api/events", params=params).status_code == 422
