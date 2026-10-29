import pytest

ANIMALS_URL = "/api/animals"


@pytest.fixture
def animal(auth_client, owner_headers):
    return auth_client.post(
        ANIMALS_URL,
        json={"tag": "A-441", "name": "Lucera", "camera_id": "camera-01"},
        headers=owner_headers,
    ).json()


def test_a_partial_update_leaves_the_other_fields_alone(auth_client, owner_headers, animal):
    # The classic PATCH bug: without exclude_unset, omitting a field blanks it.
    updated = auth_client.patch(
        f"{ANIMALS_URL}/A-441", json={"status": "lost"}, headers=owner_headers,
    ).json()
    assert updated["status"] == "lost"
    assert updated["name"] == "Lucera"
    assert updated["camera_id"] == "camera-01"


def test_an_update_moves_updated_at(auth_client, owner_headers, animal):
    updated = auth_client.patch(
        f"{ANIMALS_URL}/A-441", json={"name": "Lucerita"}, headers=owner_headers,
    ).json()
    assert updated["updated_at"] >= animal["updated_at"]
    assert updated["created_at"] == animal["created_at"]


def test_a_name_can_be_cleared(auth_client, owner_headers, animal):
    # Explicitly sending null is different from omitting the field, and both
    # have to work.
    updated = auth_client.patch(
        f"{ANIMALS_URL}/A-441", json={"name": None}, headers=owner_headers,
    ).json()
    assert updated["name"] is None


def test_the_tag_cannot_be_changed_through_an_update(auth_client, owner_headers, animal):
    # Replacing a tag replaces the animal's identity and cascades to its whole
    # event history, so it is not something a general update may do.
    response = auth_client.patch(
        f"{ANIMALS_URL}/A-441", json={"tag": "A-442"}, headers=owner_headers,
    )
    assert response.status_code == 422


def test_updating_an_unknown_animal_is_a_404(auth_client, owner_headers):
    response = auth_client.patch(
        f"{ANIMALS_URL}/NOPE", json={"status": "lost"}, headers=owner_headers,
    )
    assert response.status_code == 404


def test_deactivating_keeps_the_row(auth_client, owner_headers, animal):
    response = auth_client.post(f"{ANIMALS_URL}/A-441/deactivate", headers=owner_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "inactive"
    # There is no DELETE on purpose: a deleted animal leaves its alert history
    # pointing at nothing.
    assert auth_client.get(f"{ANIMALS_URL}/A-441").status_code == 200


def test_there_is_no_delete(auth_client, owner_headers, animal):
    assert auth_client.delete(f"{ANIMALS_URL}/A-441", headers=owner_headers).status_code == 405


def test_the_status_filter_selects(auth_client, owner_headers, animal):
    auth_client.post(ANIMALS_URL, json={"tag": "A-442"}, headers=owner_headers)
    auth_client.post(f"{ANIMALS_URL}/A-442/deactivate", headers=owner_headers)

    assert auth_client.get(ANIMALS_URL, params={"status": "active"}).json()["total"] == 1
    assert auth_client.get(ANIMALS_URL, params={"status": "inactive"}).json()["total"] == 1
    # Unfiltered by default: a default filter would hide rows and make the
    # total disagree with the table for reasons the caller cannot see.
    assert auth_client.get(ANIMALS_URL).json()["total"] == 2


def test_an_unknown_status_filter_is_rejected(auth_client, animal):
    assert auth_client.get(ANIMALS_URL, params={"status": "vendida"}).status_code == 422


def test_a_worker_may_read_the_herd(auth_client, worker_headers, animal):
    assert auth_client.get(ANIMALS_URL).status_code == 200
    assert auth_client.get(f"{ANIMALS_URL}/A-441").status_code == 200


@pytest.mark.parametrize("method,path,body", [
    ("post", "", {"tag": "A-500"}),
    ("patch", "/A-441", {"status": "lost"}),
    ("post", "/A-441/deactivate", None),
])
def test_a_worker_may_not_change_the_herd(auth_client, worker_headers, animal, method, path, body):
    # The first place the role column is enforced rather than merely recorded.
    response = getattr(auth_client, method)(
        f"{ANIMALS_URL}{path}", json=body, headers=worker_headers,
    )
    assert response.status_code == 403
    assert "owner" in response.json()["detail"]


@pytest.mark.parametrize("method,path,body", [
    ("post", "", {"tag": "A-500"}),
    ("patch", "/A-441", {"status": "lost"}),
    ("post", "/A-441/deactivate", None),
])
def test_writing_without_a_token_is_rejected(auth_client, animal, method, path, body):
    response = getattr(auth_client, method)(f"{ANIMALS_URL}{path}", json=body)
    assert response.status_code == 401


def test_recording_a_detection_needs_the_ai_key(secured_auth_client):
    # Registered on this client. A token from the auth_client fixture belongs
    # to a different application instance and its own user store, so it would
    # be rejected here and nothing would be created.
    token = secured_auth_client.post("/api/auth/register", json={
        "email": "ana@finca.example", "password": "una-clave-larga", "full_name": "Ana",
    }).json()["access_token"]
    created = secured_auth_client.post(
        ANIMALS_URL, json={"tag": "A-441"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201

    body = {"camera_id": "camera-02", "detected_at": "2026-10-29T12:00:00Z"}

    # The caller is the vision service, which holds the shared key, so a user
    # token is not what guards this one.
    assert secured_auth_client.put(f"{ANIMALS_URL}/A-441/detection", json=body).status_code == 401

    response = secured_auth_client.put(
        f"{ANIMALS_URL}/A-441/detection", json=body, headers={"X-API-Key": "test-shared-key"},
    )
    assert response.status_code == 200
    assert response.json()["last_detection"] == "2026-10-29T12:00:00Z"
    assert response.json()["camera_id"] == "camera-02"


def test_a_detection_for_an_unknown_animal_is_a_404(auth_client):
    response = auth_client.put(
        f"{ANIMALS_URL}/NOPE/detection",
        json={"detected_at": "2026-10-29T12:00:00Z"},
    )
    assert response.status_code == 404


def test_a_detection_needs_a_timezone(auth_client, owner_headers, animal):
    # Every timestamp in SmartCattle is UTC-aware. A naive one would be read in
    # whichever zone the caller happens to be in.
    response = auth_client.put(
        f"{ANIMALS_URL}/A-441/detection", json={"detected_at": "2026-10-29T12:00:00"},
    )
    assert response.status_code == 422


def test_the_exists_check_needs_a_token(auth_client, animal):
    # Reveals almost nothing on its own, and without it the farm's ear tags can
    # be enumerated one request at a time.
    assert auth_client.get(f"{ANIMALS_URL}/A-441/exists").status_code == 401


def test_the_exists_check_answers(auth_client, worker_headers, animal):
    assert auth_client.get(
        f"{ANIMALS_URL}/A-441/exists", headers=worker_headers,
    ).json() == {"exists": True}
    assert auth_client.get(
        f"{ANIMALS_URL}/A-999/exists", headers=worker_headers,
    ).json() == {"exists": False}
