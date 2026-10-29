import pytest

REGISTER_URL = "/api/auth/register"


def test_register_returns_a_token_and_the_user(auth_client, owner_payload):
    response = auth_client.post(REGISTER_URL, json=owner_payload)
    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 3600
    assert body["access_token"]
    assert body["user"]["email"] == "ana@finca.example"
    assert body["user"]["full_name"] == "Ana Restrepo"
    assert body["user"]["is_active"] is True
    assert body["user"]["last_login_at"] is None


def test_the_response_never_carries_the_hash(auth_client, owner_payload):
    body = auth_client.post(REGISTER_URL, json=owner_payload).json()
    # Not just absent from the user object: the password must not appear
    # anywhere in the response, in any form.
    assert "password" not in body["user"]
    assert "password_hash" not in body["user"]
    assert owner_payload["password"] not in response_text(body)


def response_text(body: dict) -> str:
    import json

    return json.dumps(body)


def test_the_first_account_is_the_owner(auth_client, owner_payload):
    body = auth_client.post(REGISTER_URL, json=owner_payload).json()
    # Somebody has to be, and there is no other channel to grant it.
    assert body["user"]["role"] == "owner"


def test_every_later_account_is_a_worker(auth_client, owner, worker):
    assert owner["user"]["role"] == "owner"
    assert worker["user"]["role"] == "worker"


def test_the_email_is_stored_lower_cased(auth_client):
    body = auth_client.post(REGISTER_URL, json={
        "email": "Ana@Finca.Example", "password": "una-clave-larga", "full_name": "Ana",
    }).json()
    # users.email carries CHECK (email = lower(email)), so normalising here is
    # what keeps the database from rejecting the row.
    assert body["user"]["email"] == "ana@finca.example"


def test_a_differently_capitalised_email_is_the_same_account(auth_client, owner):
    # The reason the lower-case rule exists: without it this registers a second
    # account for the same person, each with its own password.
    response = auth_client.post(REGISTER_URL, json={
        "email": "ANA@FINCA.EXAMPLE", "password": "otra-clave-larga", "full_name": "Impostora",
    })
    assert response.status_code == 409


def test_the_full_name_is_trimmed(auth_client):
    body = auth_client.post(REGISTER_URL, json={
        "email": "ana@finca.example", "password": "una-clave-larga", "full_name": "  Ana Restrepo  ",
    }).json()
    assert body["user"]["full_name"] == "Ana Restrepo"


def test_a_repeated_email_is_a_conflict(auth_client, owner, owner_payload):
    response = auth_client.post(REGISTER_URL, json=owner_payload)
    assert response.status_code == 409
    assert "already registered" in response.json()["detail"]


def test_the_role_cannot_be_chosen_at_registration(auth_client, owner):
    # extra="forbid" on UserCreate. Without it the second account could ask for
    # owner, and so could every account after it.
    response = auth_client.post(REGISTER_URL, json={
        "email": "beto@finca.example", "password": "otra-clave-larga",
        "full_name": "Beto", "role": "owner",
    })
    assert response.status_code == 422


@pytest.mark.parametrize("field,value", [
    ("email", "not-an-email"),
    ("email", ""),
    ("password", "corta"),
    ("full_name", ""),
    ("full_name", "   "),
])
def test_invalid_registration_is_rejected(auth_client, owner_payload, field, value):
    response = auth_client.post(REGISTER_URL, json=owner_payload | {field: value})
    assert response.status_code == 422


def test_a_password_over_the_bcrypt_limit_is_rejected(auth_client, owner_payload):
    # Rejected by validation rather than hashed and silently truncated: bcrypt
    # ignores everything past 72 bytes, so two different passwords sharing a
    # 72-byte prefix would both open the account.
    response = auth_client.post(REGISTER_URL, json=owner_payload | {"password": "a" * 100})
    assert response.status_code == 422
    assert "72 bytes" in response.text


def test_an_accented_password_is_measured_in_bytes(auth_client, owner_payload):
    # 40 accented characters are 80 bytes in UTF-8, over the limit, even though
    # the string is well under 72 characters.
    response = auth_client.post(REGISTER_URL, json=owner_payload | {"password": "ñ" * 40})
    assert response.status_code == 422


def test_registration_needs_a_configured_secret(client, owner_payload):
    # The plain client fixture has no JWT_SECRET. 503 and not 500: nothing is
    # broken, the feature is switched off.
    response = client.post(REGISTER_URL, json=owner_payload)
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]
