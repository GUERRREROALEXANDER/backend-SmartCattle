"""Who did what: the audit log and the actions that write to it."""

import pytest

from app.services.audit_log import InMemoryAuditLog

AUDIT_URL = "/api/audit-log"


def actions(client, headers, **params):
    return [entry["action"] for entry in client.get(AUDIT_URL, headers=headers, params=params).json()["items"]]


def test_registering_and_signing_in_are_recorded(auth_client, owner, owner_headers, owner_payload):
    auth_client.post("/api/auth/login", json={"email": owner_payload["email"], "password": owner_payload["password"]})
    page = auth_client.get(AUDIT_URL, headers=owner_headers).json()
    assert [entry["action"] for entry in page["items"]] == ["auth.signed_in", "auth.registered"]
    registered = page["items"][1]
    assert registered["actor_id"] == owner["user"]["id"]
    assert registered["actor_email"] == owner_payload["email"]
    assert registered["detail"] == {"role": "owner"}
    assert registered["ip_address"] == "testclient"


def test_a_failed_sign_in_records_the_address_typed(auth_client, owner, owner_headers):
    auth_client.post("/api/auth/login", json={"email": "nadie@finca.example", "password": "lo-que-sea-123"})
    entry = auth_client.get(AUDIT_URL, headers=owner_headers, params={"action": "auth.sign_in_failed"}).json()
    assert entry["total"] == 1
    assert entry["items"][0]["actor_id"] is None
    assert entry["items"][0]["actor_email"] == "nadie@finca.example"


def test_handling_an_alert_is_recorded(auth_client, owner, owner_headers, worker, worker_headers, event_payload):
    auth_client.post("/api/ai/events", json=event_payload)
    alert = auth_client.get("/api/alerts", headers=worker_headers).json()["items"][0]
    auth_client.post(f"/api/alerts/{alert['id']}/acknowledge", headers=worker_headers)
    auth_client.post(f"/api/alerts/{alert['id']}/resolve", headers=owner_headers)
    entries = auth_client.get(AUDIT_URL, headers=owner_headers, params={"target_id": alert["id"]}).json()["items"]
    assert [(entry["action"], entry["actor_email"]) for entry in entries] == [
        ("alert.resolved", "ana@finca.example"), ("alert.acknowledged", "beto@finca.example"),
    ]


def test_farm_and_lot_changes_are_recorded(auth_client, owner_headers):
    farm = auth_client.post("/api/farms", json={"name": "La Esperanza"}, headers=owner_headers).json()
    lot = auth_client.post(f"/api/farms/{farm['id']}/lots", json={"name": "Lote 1"}, headers=owner_headers).json()
    auth_client.patch(f"/api/lots/{lot['id']}", json={"capacity": 30}, headers=owner_headers)
    auth_client.delete(f"/api/lots/{lot['id']}", headers=owner_headers)
    auth_client.delete(f"/api/farms/{farm['id']}", headers=owner_headers)
    entries = auth_client.get(AUDIT_URL, headers=owner_headers, params={"limit": 5}).json()["items"]
    assert [entry["action"] for entry in entries] == [
        "farm.deleted", "lot.deleted", "lot.updated", "lot.created", "farm.created",
    ]
    assert entries[2]["detail"] == {"changed": ["capacity"]}
    assert entries[0]["detail"] == {"name": "La Esperanza"}


def test_account_changes_are_recorded_without_secrets(auth_client, owner_headers, worker):
    auth_client.post("/api/users", json={
        "email": "carla@finca.example", "password": "clave-inicial-123", "full_name": "Carla", "role": "worker",
    }, headers=owner_headers)
    auth_client.patch(f"/api/users/{worker['user']['id']}", json={"role": "owner"}, headers=owner_headers)
    entries = auth_client.get(AUDIT_URL, headers=owner_headers, params={"limit": 2}).json()["items"]
    assert entries[0]["action"] == "user.updated" and entries[0]["detail"] == {"role": "owner"}
    assert entries[1]["action"] == "user.created"
    assert "clave-inicial-123" not in str(entries)


def test_zone_and_animal_changes_are_recorded(auth_client, owner_headers):
    zone = auth_client.post("/api/zones/camera/camera-01", json={
        "name": "Safe zone", "x_min": 0.1, "y_min": 0.1, "x_max": 0.9, "y_max": 0.9,
    }, headers=owner_headers).json()
    auth_client.delete(f"/api/zones/{zone['id']}", headers=owner_headers)
    auth_client.post("/api/animals", json={"tag": "CO-001", "name": "Lucero"}, headers=owner_headers)
    auth_client.post("/api/animals/CO-001/deactivate", headers=owner_headers)
    assert actions(auth_client, owner_headers, limit=4) == [
        "animal.deactivated", "animal.created", "zone.deleted", "zone.created",
    ]


def test_a_rejected_action_is_not_recorded(auth_client, owner_headers, worker_headers):
    auth_client.post("/api/farms", json={"name": "La Esperanza"}, headers=worker_headers)
    auth_client.post("/api/farms", json={"name": "   "}, headers=owner_headers)
    assert "farm.created" not in actions(auth_client, owner_headers)


def test_only_an_owner_reads_the_log(auth_client, owner, worker_headers):
    assert auth_client.get(AUDIT_URL, headers=worker_headers).status_code == 403
    assert auth_client.get(AUDIT_URL).status_code == 401


def test_the_log_filters_by_actor_and_window(auth_client, owner, owner_headers, worker):
    mine = auth_client.get(AUDIT_URL, headers=owner_headers, params={"actor_id": worker["user"]["id"]}).json()
    assert [entry["action"] for entry in mine["items"]] == ["auth.registered"]
    future = auth_client.get(AUDIT_URL, headers=owner_headers, params={"start": "2999-01-01T00:00:00"}).json()
    assert future["total"] == 0


@pytest.mark.parametrize("params", [{"action": "farm.burned"}, {"limit": 0}, {"limit": 201}, {"actor_id": 0}])
def test_bad_filters_are_rejected(auth_client, owner_headers, params):
    assert auth_client.get(AUDIT_URL, headers=owner_headers, params=params).status_code == 422


def test_a_failing_log_does_not_fail_the_action(auth_client, owner_headers, monkeypatch, caplog):
    def broken(_record):
        raise RuntimeError("database is gone")

    monkeypatch.setattr(auth_client.app.state.audit_log, "record", broken)
    response = auth_client.post("/api/farms", json={"name": "La Esperanza"}, headers=owner_headers)
    assert response.status_code == 201
    assert "Could not write the audit entry for farm.created" in caplog.text


def test_the_in_memory_log_keeps_a_bounded_tail(monkeypatch, auth_client, owner_headers):
    monkeypatch.setattr("app.services.audit_log.MAX_IN_MEMORY_ENTRIES", 3)
    assert isinstance(auth_client.app.state.audit_log, InMemoryAuditLog)
    for name in ("A", "B", "C", "D"):
        auth_client.post("/api/farms", json={"name": name}, headers=owner_headers)
    page = auth_client.get(AUDIT_URL, headers=owner_headers).json()
    assert page["total"] == 3
    assert [entry["detail"]["name"] for entry in page["items"]] == ["D", "C", "B"]
