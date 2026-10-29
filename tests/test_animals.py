import pytest

ANIMALS_URL = "/api/animals"


@pytest.fixture
def animal_payload():
    return {"tag": "A-441", "name": "Lucera", "camera_id": "camera-01"}


def test_the_herd_starts_empty(auth_client):
    response = auth_client.get(ANIMALS_URL)
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}


def test_registering_an_animal_returns_it(auth_client, owner_headers, animal_payload):
    response = auth_client.post(ANIMALS_URL, json=animal_payload, headers=owner_headers)
    assert response.status_code == 201
    animal = response.json()
    assert animal["tag"] == "A-441"
    assert animal["name"] == "Lucera"
    assert animal["camera_id"] == "camera-01"
    # A new animal has not been detected yet, and that is not the same as
    # having been detected at the epoch.
    assert animal["last_detection"] is None
    assert animal["status"] == "active"


def test_the_registered_animal_is_listed_and_retrievable(auth_client, owner_headers, animal_payload):
    created = auth_client.post(ANIMALS_URL, json=animal_payload, headers=owner_headers).json()
    assert auth_client.get(ANIMALS_URL).json() == {"items": [created], "total": 1}
    assert auth_client.get(f"{ANIMALS_URL}/A-441").json() == created


def test_the_total_reflects_the_herd(auth_client, owner_headers, animal_payload):
    # The regression that matters: the endpoint used to return a hardcoded
    # total of 0, and the frontend reads that as the registered cattle count,
    # so the dashboard showed an empty farm.
    for index in range(3):
        auth_client.post(
            ANIMALS_URL, json=animal_payload | {"tag": f"A-{index}"}, headers=owner_headers,
        )
    assert auth_client.get(ANIMALS_URL).json()["total"] == 3


def test_animals_are_listed_in_tag_order(auth_client, owner_headers, animal_payload):
    for tag in ["B-2", "A-1", "C-3"]:
        auth_client.post(ANIMALS_URL, json=animal_payload | {"tag": tag}, headers=owner_headers)
    listed = [animal["tag"] for animal in auth_client.get(ANIMALS_URL).json()["items"]]
    assert listed == ["A-1", "B-2", "C-3"]


def test_an_unknown_tag_is_a_404(auth_client):
    response = auth_client.get(f"{ANIMALS_URL}/NOPE")
    assert response.status_code == 404
    assert "NOPE" in response.json()["detail"]


def test_a_repeated_tag_is_a_conflict(auth_client, owner_headers, animal_payload):
    auth_client.post(ANIMALS_URL, json=animal_payload, headers=owner_headers)
    response = auth_client.post(ANIMALS_URL, json=animal_payload, headers=owner_headers)
    assert response.status_code == 409
    assert "A-441" in response.json()["detail"]


def test_an_animal_can_be_registered_without_a_camera(auth_client, owner_headers):
    # camera_id is nullable: staff register the herd before anyone knows where
    # each animal is, and an unlocated animal still belongs to the farm.
    response = auth_client.post(
        ANIMALS_URL, json={"tag": "A-900", "name": "Sin ubicar"}, headers=owner_headers,
    )
    assert response.status_code == 201
    assert response.json()["camera_id"] is None


def test_an_unlocated_animal_is_still_listed(auth_client, owner_headers):
    auth_client.post(ANIMALS_URL, json={"tag": "A-900"}, headers=owner_headers)
    # The case an inner join to cameras would silently drop, and the animal
    # most worth looking at.
    assert auth_client.get(ANIMALS_URL).json()["total"] == 1


def test_a_blank_name_is_stored_as_null(auth_client, owner_headers):
    # An empty string is how a form submits "not set"; storing it would leave
    # two ways to say the same absence.
    body = auth_client.post(
        ANIMALS_URL, json={"tag": "A-901", "name": "   "}, headers=owner_headers,
    ).json()
    assert body["name"] is None


@pytest.mark.parametrize("tag", ["", "with space", "A/441", "a" * 65, "tag#1"])
def test_an_unusable_tag_is_rejected(auth_client, owner_headers, tag):
    # A tag with a slash would split the route; the rest are not ear tags.
    response = auth_client.post(
        ANIMALS_URL, json={"tag": tag, "name": "x"}, headers=owner_headers,
    )
    assert response.status_code == 422


def test_the_status_can_be_set_at_registration(auth_client, owner_headers):
    body = auth_client.post(
        ANIMALS_URL, json={"tag": "A-902", "status": "lost"}, headers=owner_headers,
    ).json()
    assert body["status"] == "lost"


def test_an_unknown_status_is_rejected(auth_client, owner_headers):
    response = auth_client.post(
        ANIMALS_URL, json={"tag": "A-903", "status": "vendida"}, headers=owner_headers,
    )
    assert response.status_code == 422
