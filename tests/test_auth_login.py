import pytest

LOGIN_URL = "/api/auth/login"
ME_URL = "/api/auth/me"


def test_login_returns_a_working_token(auth_client, owner, owner_payload):
    response = auth_client.post(LOGIN_URL, json={
        "email": owner_payload["email"], "password": owner_payload["password"],
    })
    assert response.status_code == 200
    token = response.json()["access_token"]
    assert auth_client.get(ME_URL, headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_login_records_the_moment(auth_client, owner, owner_payload):
    assert owner["user"]["last_login_at"] is None
    body = auth_client.post(LOGIN_URL, json={
        "email": owner_payload["email"], "password": owner_payload["password"],
    }).json()
    assert body["user"]["last_login_at"] is not None
    # And it is persisted, not only reflected in the response.
    me = auth_client.get(ME_URL, headers={"Authorization": f"Bearer {body['access_token']}"}).json()
    assert me["last_login_at"] is not None


def test_login_accepts_a_differently_capitalised_email(auth_client, owner, owner_payload):
    response = auth_client.post(LOGIN_URL, json={
        "email": "ANA@Finca.Example", "password": owner_payload["password"],
    })
    assert response.status_code == 200


def test_a_wrong_password_and_an_unknown_email_are_indistinguishable(auth_client, owner):
    wrong = auth_client.post(LOGIN_URL, json={
        "email": "ana@finca.example", "password": "incorrecta",
    })
    unknown = auth_client.post(LOGIN_URL, json={
        "email": "nadie@finca.example", "password": "incorrecta",
    })
    # Same status and same message. Telling them apart turns login into a way
    # to find out which addresses have accounts.
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_a_failed_login_says_nothing_about_the_account(auth_client, owner):
    response = auth_client.post(LOGIN_URL, json={
        "email": "ana@finca.example", "password": "incorrecta",
    })
    assert response.json()["detail"] == "Invalid email or password"
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_a_short_password_is_a_failed_login_not_a_validation_error(auth_client, owner):
    # A 422 here would say the password was too short to be the real one, which
    # is information about the account.
    response = auth_client.post(LOGIN_URL, json={"email": "ana@finca.example", "password": "x"})
    assert response.status_code == 401


def test_an_inactive_account_cannot_sign_in(auth_client, owner, owner_payload):
    store = auth_client.app.state.user_store
    stored = store.by_email("ana@finca.example")
    store._by_id[stored.id] = stored.model_copy(update={"is_active": False})

    response = auth_client.post(LOGIN_URL, json={
        "email": owner_payload["email"], "password": owner_payload["password"],
    })
    # Invalid credentials, not a distinct status: "this account is disabled"
    # confirms the account exists.
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


def test_an_existing_token_stops_working_when_the_account_is_deactivated(
    auth_client, owner, owner_headers,
):
    assert auth_client.get(ME_URL, headers=owner_headers).status_code == 200

    store = auth_client.app.state.user_store
    stored = store.by_email("ana@finca.example")
    store._by_id[stored.id] = stored.model_copy(update={"is_active": False})

    # The token is still signed and unexpired. It fails because the user is
    # loaded on every request instead of being trusted from the claims, which
    # is the whole point of deactivation.
    assert auth_client.get(ME_URL, headers=owner_headers).status_code == 401


def test_repeated_failures_lock_the_address(auth_client, auth_settings, owner):
    attempts = auth_settings.max_login_attempts
    for _ in range(attempts):
        assert auth_client.post(LOGIN_URL, json={
            "email": "ana@finca.example", "password": "incorrecta",
        }).status_code == 401

    locked = auth_client.post(LOGIN_URL, json={
        "email": "ana@finca.example", "password": "incorrecta",
    })
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0


def test_the_lock_refuses_even_the_correct_password(auth_client, auth_settings, owner, owner_payload):
    for _ in range(auth_settings.max_login_attempts):
        auth_client.post(LOGIN_URL, json={"email": "ana@finca.example", "password": "incorrecta"})

    # Otherwise the lock is useless: an attacker who guesses on the next
    # attempt is let straight in.
    response = auth_client.post(LOGIN_URL, json={
        "email": owner_payload["email"], "password": owner_payload["password"],
    })
    assert response.status_code == 429


def test_a_successful_login_clears_the_failures(auth_client, auth_settings, owner, owner_payload):
    for _ in range(auth_settings.max_login_attempts - 1):
        auth_client.post(LOGIN_URL, json={"email": "ana@finca.example", "password": "incorrecta"})

    assert auth_client.post(LOGIN_URL, json={
        "email": owner_payload["email"], "password": owner_payload["password"],
    }).status_code == 200

    # The counter is reset, so the next mistake does not land on a nearly full
    # tally from an hour ago.
    assert auth_client.post(LOGIN_URL, json={
        "email": "ana@finca.example", "password": "incorrecta",
    }).status_code == 401


def test_locking_one_address_leaves_another_alone(auth_client, auth_settings, owner, worker):
    for _ in range(auth_settings.max_login_attempts):
        auth_client.post(LOGIN_URL, json={"email": "ana@finca.example", "password": "incorrecta"})

    assert auth_client.post(LOGIN_URL, json={
        "email": "ana@finca.example", "password": "incorrecta",
    }).status_code == 429
    # The throttle is keyed per address, so one locked account does not lock out
    # the rest of the farm.
    assert auth_client.post(LOGIN_URL, json={
        "email": "beto@finca.example", "password": "incorrecta",
    }).status_code == 401


def test_the_lock_expires(auth_client, auth_settings, owner, owner_payload):
    from datetime import timedelta

    for _ in range(auth_settings.max_login_attempts):
        auth_client.post(LOGIN_URL, json={"email": "ana@finca.example", "password": "incorrecta"})
    assert auth_client.post(LOGIN_URL, json={
        "email": "ana@finca.example", "password": "incorrecta",
    }).status_code == 429

    clock = auth_client.app.state.clock
    moved = clock() + timedelta(seconds=auth_settings.login_lockout_seconds + 1)
    auth_client.app.state.clock = lambda: moved

    assert auth_client.post(LOGIN_URL, json={
        "email": owner_payload["email"], "password": owner_payload["password"],
    }).status_code == 200


@pytest.mark.parametrize("header", [
    None,
    "",
    "Bearer",
    "Bearer ",
    "Bearer not-a-token",
    "Basic YW5hOmNsYXZl",
])
def test_me_rejects_a_missing_or_malformed_token(auth_client, owner, header):
    headers = {} if header is None else {"Authorization": header}
    assert auth_client.get(ME_URL, headers=headers).status_code == 401


def test_me_returns_the_signed_in_user(auth_client, owner, owner_headers):
    body = auth_client.get(ME_URL, headers=owner_headers).json()
    assert body["email"] == "ana@finca.example"
    assert body["role"] == "owner"
    assert "password_hash" not in body


def test_a_token_from_another_deployment_is_rejected(auth_client, auth_settings, owner):
    from datetime import timedelta

    from app.core.security import create_access_token

    forged = create_access_token(
        subject="1", secret="a-different-deployment-secret-01234", role="owner",
        expires_in=timedelta(hours=1), now=auth_client.app.state.clock(),
    )
    assert auth_client.get(ME_URL, headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_a_token_for_a_user_that_does_not_exist_is_rejected(auth_client, auth_settings, owner):
    from datetime import timedelta

    from app.core.security import create_access_token
    from tests.conftest import TEST_JWT_SECRET

    # Correctly signed by this deployment, and still unusable: the user is
    # resolved on every request rather than trusted from the claims.
    token = create_access_token(
        subject="999", secret=TEST_JWT_SECRET, role="owner",
        expires_in=timedelta(hours=1), now=auth_client.app.state.clock(),
    )
    assert auth_client.get(ME_URL, headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_me_needs_a_configured_secret(client):
    assert client.get(ME_URL).status_code == 503
