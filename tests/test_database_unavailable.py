"""A database that cannot answer is a prompt, structured answer and never a hung request."""

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as SqlAlchemyTimeoutError

from app import db
from app.main import create_app

HOST_IN_DRIVER_MESSAGE = "db.internal.example"


class _UnreachableUsers:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def count(self) -> int:
        raise self._error


def _register_with(auth_settings, owner_payload, error: Exception):
    app = create_app(auth_settings)
    app.state.user_store = _UnreachableUsers(error)
    with TestClient(app, raise_server_exceptions=False) as client:
        return client.post("/api/auth/register", json=owner_payload)


def test_a_failed_connection_answers_503_in_json(auth_settings, owner_payload):
    error = OperationalError("SELECT 1", {}, Exception(f"connection to {HOST_IN_DRIVER_MESSAGE} failed"))
    response = _register_with(auth_settings, owner_payload, error)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "10"
    assert response.json()["detail"] == "The database is unavailable. Try again shortly."
    assert response.json()["request_id"]


def test_an_exhausted_pool_answers_503_in_json(auth_settings, owner_payload):
    response = _register_with(auth_settings, owner_payload, SqlAlchemyTimeoutError("QueuePool limit reached"))
    assert response.status_code == 503


def test_the_driver_message_never_reaches_the_caller(auth_settings, owner_payload):
    error = OperationalError("SELECT 1", {}, Exception(f"connection to {HOST_IN_DRIVER_MESSAGE} failed"))
    assert HOST_IN_DRIVER_MESSAGE not in _register_with(auth_settings, owner_payload, error).text


def test_another_failure_is_still_a_500(auth_settings, owner_payload):
    assert _register_with(auth_settings, owner_payload, RuntimeError("a bug")).status_code == 500


def test_postgresql_connections_give_up_instead_of_waiting_for_the_operating_system(monkeypatch):
    seen = {}
    monkeypatch.setattr(db, "create_engine", lambda _url, **options: seen.update(options))
    db.create_db_engine("postgresql+psycopg://user@db.example/smartcattle")
    assert seen["connect_args"] == {"connect_timeout": db.DATABASE_WAIT_SECONDS}
    assert seen["pool_timeout"] == db.DATABASE_WAIT_SECONDS


def test_the_wait_stays_short_enough_for_a_person_at_a_form():
    assert db.DATABASE_WAIT_SECONDS <= 15


def test_sqlite_receives_no_postgresql_only_option(monkeypatch):
    seen = {}
    monkeypatch.setattr(db, "create_engine", lambda _url, **options: seen.update(options))
    db.create_db_engine("sqlite:///local.db")
    assert "connect_args" not in seen
