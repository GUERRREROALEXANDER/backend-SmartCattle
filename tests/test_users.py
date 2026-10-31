import pytest

USERS_URL = "/api/users"
PASSWORD_URL = "/api/auth/me/password"
NEW_WORKER = {"email": "Carla@Finca.example", "password": "clave-inicial-1", "full_name": " Carla Ruiz "}


def call(client, method, url, body, headers):
    kwargs = {} if headers is None else {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    return getattr(client, method)(url, **kwargs)


def login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_an_owner_creates_a_worker_account(auth_client, owner_headers):
    response = auth_client.post(USERS_URL, json=NEW_WORKER, headers=owner_headers)
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "carla@finca.example"
    assert body["full_name"] == "Carla Ruiz"
    assert body["role"] == "worker"
    assert "access_token" not in body and "password_hash" not in body
    assert login(auth_client, "carla@finca.example", NEW_WORKER["password"]).status_code == 200


def test_an_owner_may_create_another_owner(auth_client, owner_headers):
    body = auth_client.post(USERS_URL, json=NEW_WORKER | {"role": "owner"}, headers=owner_headers).json()
    assert body["role"] == "owner"


def test_a_taken_email_is_a_conflict(auth_client, owner_headers, worker):
    response = auth_client.post(
        USERS_URL, json=NEW_WORKER | {"email": "beto@finca.example"}, headers=owner_headers,
    )
    assert response.status_code == 409


@pytest.mark.parametrize("password,expected", [("a" * 7, 422), ("a" * 8, 201), ("ñ" * 37, 422)])
def test_the_initial_password_follows_the_registration_rules(auth_client, owner_headers, password, expected):
    response = auth_client.post(USERS_URL, json=NEW_WORKER | {"password": password}, headers=owner_headers)
    assert response.status_code == expected


def test_the_list_is_filtered_and_paged(auth_client, owner_headers, worker):
    auth_client.post(USERS_URL, json=NEW_WORKER, headers=owner_headers)
    everyone = auth_client.get(USERS_URL, headers=owner_headers).json()
    assert [user["email"] for user in everyone["items"]] == [
        "ana@finca.example", "beto@finca.example", "carla@finca.example",
    ]
    workers = auth_client.get(USERS_URL, params={"role": "worker", "limit": 1}, headers=owner_headers).json()
    assert workers["total"] == 2 and len(workers["items"]) == 1
    second = auth_client.get(
        USERS_URL, params={"role": "worker", "limit": 1, "offset": 1}, headers=owner_headers,
    ).json()
    assert second["items"][0]["email"] == "carla@finca.example"


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}])
def test_paging_bounds_are_enforced(auth_client, owner_headers, params):
    assert auth_client.get(USERS_URL, params=params, headers=owner_headers).status_code == 422


@pytest.mark.parametrize("method,url,body", [
    ("get", USERS_URL, None),
    ("post", USERS_URL, NEW_WORKER),
    ("get", f"{USERS_URL}/1", None),
    ("patch", f"{USERS_URL}/1", {"full_name": "X"}),
])
def test_a_worker_cannot_manage_accounts(auth_client, worker_headers, method, url, body):
    assert call(auth_client, method, url, body, worker_headers).status_code == 403


@pytest.mark.parametrize("method,url,body", [
    ("get", USERS_URL, None),
    ("post", USERS_URL, NEW_WORKER),
    ("put", PASSWORD_URL, {"current_password": "x", "new_password": "otra-clave-9"}),
])
def test_account_endpoints_are_not_public(auth_client, owner, method, url, body):
    assert call(auth_client, method, url, body, None).status_code == 401


@pytest.mark.parametrize("method,url,body", [
    ("get", f"{USERS_URL}/999", None),
    ("patch", f"{USERS_URL}/999", {"is_active": False}),
])
def test_an_unknown_account_is_a_404(auth_client, owner_headers, method, url, body):
    assert call(auth_client, method, url, body, owner_headers).status_code == 404


def test_promoting_a_worker_takes_effect_on_their_old_token(auth_client, owner_headers, worker, worker_headers):
    assert auth_client.post("/api/farms", json={"name": "Mine"}, headers=worker_headers).status_code == 403
    promoted = auth_client.patch(
        f"{USERS_URL}/{worker['user']['id']}", json={"role": "owner"}, headers=owner_headers,
    ).json()
    assert promoted["role"] == "owner"
    assert auth_client.post("/api/farms", json={"name": "Mine"}, headers=worker_headers).status_code == 201


def test_a_demoted_owner_loses_access_with_their_old_token(auth_client, owner, owner_headers, worker, worker_headers):
    auth_client.patch(f"{USERS_URL}/{worker['user']['id']}", json={"role": "owner"}, headers=owner_headers)
    auth_client.patch(f"{USERS_URL}/{owner['user']['id']}", json={"role": "worker"}, headers=worker_headers)
    assert auth_client.get(USERS_URL, headers=owner_headers).status_code == 403


def test_a_deactivated_account_is_signed_out(auth_client, owner_headers, worker, worker_headers):
    auth_client.patch(f"{USERS_URL}/{worker['user']['id']}", json={"is_active": False}, headers=owner_headers)
    assert auth_client.get("/api/auth/me", headers=worker_headers).status_code == 401
    assert login(auth_client, "beto@finca.example", "otra-clave-larga").status_code == 401


@pytest.mark.parametrize("change", [{"role": "worker"}, {"is_active": False}])
def test_the_last_active_owner_cannot_be_removed(auth_client, owner, owner_headers, change):
    response = auth_client.patch(f"{USERS_URL}/{owner['user']['id']}", json=change, headers=owner_headers)
    assert response.status_code == 409
    assert auth_client.get("/api/auth/me", headers=owner_headers).json()["role"] == "owner"


@pytest.mark.parametrize("change", [{"role": "worker"}, {"is_active": False}])
def test_an_owner_can_step_down_once_another_exists(auth_client, owner, owner_headers, worker, change):
    auth_client.patch(f"{USERS_URL}/{worker['user']['id']}", json={"role": "owner"}, headers=owner_headers)
    response = auth_client.patch(f"{USERS_URL}/{owner['user']['id']}", json=change, headers=owner_headers)
    assert response.status_code == 200


def test_an_inactive_owner_does_not_count_as_the_remaining_one(auth_client, owner, owner_headers):
    second = auth_client.post(USERS_URL, json=NEW_WORKER | {"role": "owner"}, headers=owner_headers).json()
    auth_client.patch(f"{USERS_URL}/{second['id']}", json={"is_active": False}, headers=owner_headers)
    response = auth_client.patch(f"{USERS_URL}/{owner['user']['id']}", json={"role": "worker"}, headers=owner_headers)
    assert response.status_code == 409


def test_editing_a_worker_never_trips_the_owner_rule(auth_client, owner_headers, worker):
    response = auth_client.patch(
        f"{USERS_URL}/{worker['user']['id']}", json={"is_active": False, "full_name": "Beto S."},
        headers=owner_headers,
    )
    assert response.status_code == 200
    assert response.json()["full_name"] == "Beto S."


@pytest.mark.parametrize("body", [{}, {"role": None}, {"is_active": None}, {"full_name": "  "}, {"email": "x@y.z"}])
def test_an_update_must_be_a_valid_change(auth_client, owner, owner_headers, body):
    assert auth_client.patch(
        f"{USERS_URL}/{owner['user']['id']}", json=body, headers=owner_headers,
    ).status_code == 422


def test_a_user_changes_their_own_password(auth_client, worker, worker_headers):
    response = auth_client.put(PASSWORD_URL, json={
        "current_password": "otra-clave-larga", "new_password": "clave-nueva-123",
    }, headers=worker_headers)
    assert response.status_code == 204
    assert login(auth_client, "beto@finca.example", "otra-clave-larga").status_code == 401
    assert login(auth_client, "beto@finca.example", "clave-nueva-123").status_code == 200


def test_a_wrong_current_password_is_a_400_not_a_sign_out(auth_client, worker_headers):
    response = auth_client.put(PASSWORD_URL, json={
        "current_password": "not-the-password", "new_password": "clave-nueva-123",
    }, headers=worker_headers)
    assert response.status_code == 400


def test_guessing_the_current_password_is_throttled(auth_client, worker_headers):
    wrong = {"current_password": "not-the-password", "new_password": "clave-nueva-123"}
    statuses = [auth_client.put(PASSWORD_URL, json=wrong, headers=worker_headers).status_code for _ in range(6)]
    assert statuses == [400] * 5 + [429]
    right = {"current_password": "otra-clave-larga", "new_password": "clave-nueva-123"}
    locked = auth_client.put(PASSWORD_URL, json=right, headers=worker_headers)
    assert locked.status_code == 429 and "Retry-After" in locked.headers


@pytest.mark.parametrize("new_password", ["corta", "otra-clave-larga", "ñ" * 37])
def test_the_new_password_is_validated(auth_client, worker_headers, new_password):
    response = auth_client.put(PASSWORD_URL, json={
        "current_password": "otra-clave-larga", "new_password": new_password,
    }, headers=worker_headers)
    assert response.status_code == 422
