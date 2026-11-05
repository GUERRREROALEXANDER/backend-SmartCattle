"""Watching a camera from anywhere: the backend only says where, and signs a ticket for that one camera."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.security import MEDIA_AUDIENCE, MEDIA_ISSUER, create_media_ticket, decode_access_token, media_jwks
from app.dependencies import get_clock
from app.main import create_app
from tests.conftest import TEST_JWT_SECRET
from tests.test_farm_pc import SHARED, Clock, link

MEDIA_URL = "https://video-finca.trycloudflare.com"
PC_URL = "https://ia-finca.trycloudflare.com"
MOMENT = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
PASSWORD = "una-clave-larga"


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def api(clock):
    settings = Settings(
        _env_file=None, database_url=None, ai_api_key=SHARED, jwt_secret=TEST_JWT_SECRET,
        account_isolation=True, rate_limit_enabled=False,
    )
    app = create_app(settings)
    app.dependency_overrides[get_clock] = lambda: clock
    with TestClient(app) as client:
        yield client


def sign_up(api, email):
    body = {"email": email, "password": PASSWORD, "full_name": "Persona De Prueba"}
    return {"Authorization": f"Bearer {api.post('/api/auth/register', json=body).json()['access_token']}"}


def share_video(api, key, media_url=MEDIA_URL):
    body = {"url": PC_URL} if media_url is None else {"url": PC_URL, "media_url": media_url}
    assert api.put("/api/ai/farm-pc", json=body, headers=key).status_code == 204


def save_camera(api, headers):
    body = {"name": "Potrero norte", "source": "rtsp://192.168.1.50:554/live"}
    return api.post("/api/cameras/saved", json=body, headers=headers).json()["id"]


@pytest.fixture
def ana(api):
    return sign_up(api, "ana@finca.example")


@pytest.fixture
def key(api, ana):
    return link(api, ana)


@pytest.fixture
def camera_id(api, ana):
    return save_camera(api, ana)


def verified_claims(ticket):
    public = jwt.PyJWK(media_jwks(TEST_JWT_SECRET)["keys"][0]).key
    return jwt.decode(
        ticket, public, algorithms=["EdDSA"], audience=MEDIA_AUDIENCE, issuer=MEDIA_ISSUER,
        options={"verify_exp": False, "verify_iat": False},
    )


def test_the_owner_gets_the_playlist_and_a_ticket_for_that_camera_only(api, ana, key, camera_id):
    share_video(api, key)
    response = api.post(f"/api/cameras/{camera_id}/live", headers=ana)
    assert response.status_code == 200
    body = response.json()
    assert body["hls_url"] == f"{MEDIA_URL}/{camera_id}/index.m3u8"
    assert body["expires_in"] == 600
    assert verified_claims(body["token"])["mediamtx_permissions"] == [{"action": "read", "path": camera_id}]


def test_a_media_url_with_a_trailing_slash_still_builds_a_clean_playlist(api, ana, key, camera_id):
    share_video(api, key, f"{MEDIA_URL}/")
    assert api.post(f"/api/cameras/{camera_id}/live", headers=ana).json()["hls_url"] == (
        f"{MEDIA_URL}/{camera_id}/index.m3u8"
    )


def test_a_worker_of_the_account_can_watch(api, ana, key, camera_id):
    share_video(api, key)
    body = {"email": "beto@finca.example", "password": PASSWORD, "full_name": "Beto Salazar", "role": "worker"}
    assert api.post("/api/users", json=body, headers=ana).status_code == 201
    token = api.post("/api/auth/login", json={"email": body["email"], "password": PASSWORD}).json()["access_token"]
    assert api.post(f"/api/cameras/{camera_id}/live", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_another_account_cannot_tell_the_camera_exists(api, key, camera_id):
    share_video(api, key)
    beto = sign_up(api, "beto@lejos.example")
    share_video(api, link(api, beto))
    assert api.post(f"/api/cameras/{camera_id}/live", headers=beto).status_code == 404


def test_watching_needs_a_session(api, key, camera_id):
    share_video(api, key)
    assert api.post(f"/api/cameras/{camera_id}/live").status_code == 401


def test_watching_needs_a_session_without_account_isolation_too():
    settings = Settings(_env_file=None, jwt_secret=TEST_JWT_SECRET, database_url=None, account_isolation=False)
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/cameras/cam-1/live").status_code == 401


def test_the_shared_ai_key_opens_no_video(api, key, camera_id):
    share_video(api, key)
    assert api.post(f"/api/cameras/{camera_id}/live", headers={"X-API-Key": SHARED}).status_code == 401


def test_a_farm_pc_key_opens_no_video(api, key, camera_id):
    share_video(api, key)
    assert api.post(f"/api/cameras/{camera_id}/live", headers=key).status_code == 401


def test_an_unknown_camera_is_a_404(api, ana, key):
    share_video(api, key)
    assert api.post("/api/cameras/cam-nothing/live", headers=ana).status_code == 404


def test_a_deleted_camera_cannot_be_opened_again(api, ana, key, camera_id):
    share_video(api, key)
    assert api.delete(f"/api/cameras/saved/{camera_id}", headers=ana).status_code == 204
    assert api.post(f"/api/cameras/{camera_id}/live", headers=ana).status_code == 404


def test_a_pc_that_never_reported_is_a_conflict(api, ana, camera_id):
    assert api.post(f"/api/cameras/{camera_id}/live", headers=ana).status_code == 409


def test_a_pc_without_a_media_server_is_a_conflict(api, ana, key, camera_id):
    share_video(api, key, media_url=None)
    assert api.post(f"/api/cameras/{camera_id}/live", headers=ana).status_code == 409


def test_a_pc_that_stops_its_media_server_stops_being_offered(api, ana, key, camera_id):
    share_video(api, key)
    share_video(api, key, media_url=None)
    assert api.post(f"/api/cameras/{camera_id}/live", headers=ana).status_code == 409


def test_the_media_server_is_offered_until_the_pc_goes_quiet_and_not_after(api, ana, key, camera_id, clock):
    share_video(api, key)
    clock.now += timedelta(seconds=90)
    assert api.post(f"/api/cameras/{camera_id}/live", headers=ana).status_code == 200
    clock.now += timedelta(seconds=1)
    assert api.post(f"/api/cameras/{camera_id}/live", headers=ana).status_code == 409


def test_without_jwt_secret_no_key_is_published():
    settings = Settings(_env_file=None, jwt_secret=None, database_url=None, account_isolation=False)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/media/jwks.json").status_code == 503


def test_the_published_key_needs_no_session_and_holds_no_private_part(api):
    response = api.get("/api/media/jwks.json")
    assert response.status_code == 200
    key = response.json()["keys"][0]
    assert (key["kty"], key["crv"], key["alg"]) == ("OKP", "Ed25519", "EdDSA")
    assert "d" not in key


def test_the_key_stays_public_where_reads_need_a_session():
    settings = Settings(
        _env_file=None, jwt_secret=TEST_JWT_SECRET, database_url=None, account_isolation=True,
        require_auth_for_reads=True,
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/media/jwks.json").status_code == 200


def test_the_ticket_key_changes_with_the_secret():
    assert media_jwks(TEST_JWT_SECRET) != media_jwks(TEST_JWT_SECRET + "-replaced")


def test_a_ticket_lasts_exactly_what_was_asked():
    ticket = create_media_ticket(
        camera_id="cam-1", secret=TEST_JWT_SECRET, expires_in=timedelta(minutes=10), now=MOMENT,
    )
    claims = verified_claims(ticket)
    assert claims["iat"] == int(MOMENT.timestamp())
    assert claims["exp"] == int((MOMENT + timedelta(minutes=10)).timestamp())


def test_a_ticket_signed_with_another_secret_does_not_verify():
    ticket = create_media_ticket(
        camera_id="cam-1", secret=TEST_JWT_SECRET + "-other", expires_in=timedelta(minutes=10), now=MOMENT,
    )
    with pytest.raises(jwt.InvalidSignatureError):
        verified_claims(ticket)


def test_a_video_ticket_is_not_a_session():
    ticket = create_media_ticket(
        camera_id="cam-1", secret=TEST_JWT_SECRET, expires_in=timedelta(minutes=10), now=MOMENT,
    )
    assert decode_access_token(ticket, secret=TEST_JWT_SECRET, now=MOMENT) is None


def test_the_api_refuses_a_ticket_as_a_session(api, ana, key, camera_id):
    share_video(api, key)
    ticket = api.post(f"/api/cameras/{camera_id}/live", headers=ana).json()["token"]
    assert api.get("/api/auth/me", headers={"Authorization": f"Bearer {ticket}"}).status_code == 401


@pytest.mark.parametrize("media_url", [
    "https://admin:secret@video.example", "ftp://video.example", "https://video.example/" + "x" * 300,
])
def test_a_bad_media_url_is_refused_without_echoing_it(api, key, media_url):
    response = api.put("/api/ai/farm-pc", json={"url": PC_URL, "media_url": media_url}, headers=key)
    assert response.status_code == 422
    assert "secret" not in response.text


def test_a_media_url_of_exactly_the_limit_is_accepted(api, key):
    prefix = "https://video.example/"
    share_video(api, key, prefix + "x" * (300 - len(prefix)))


def test_the_panel_state_keeps_its_shape(api, ana, key):
    share_video(api, key)
    assert api.get("/api/farm-pc", headers=ana).json() == {"url": PC_URL}
