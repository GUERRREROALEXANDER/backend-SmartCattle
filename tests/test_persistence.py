"""Checks that the new tables survive a restart.

The in-memory stores are covered elsewhere. These run against a real database
through the migrations, which is the only place the column mapping, the
constraints and the timezone handling are all exercised at once.
"""

from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_database import alembic_config, database_settings, sign_in

RECTANGLE = {"name": "Safe zone", "x_min": 0.1, "y_min": 0.1, "x_max": 0.9, "y_max": 0.9}


def migrated(tmp_path, name: str):
    from alembic import command

    url = f"sqlite:///{(tmp_path / name).as_posix()}"
    command.upgrade(alembic_config(url), "head")
    return database_settings(url)


def test_an_account_survives_a_restart(tmp_path):
    settings = migrated(tmp_path, "users.db")
    with TestClient(create_app(settings)) as client:
        created = client.post("/api/auth/register", json={
            "email": "ana@finca.example", "password": "una-clave-larga", "full_name": "Ana",
        })
        assert created.status_code == 201
        assert created.json()["user"]["role"] == "owner"

    # A new application on the same database: the account is still there and
    # the password still verifies, which is what proves the hash round-tripped.
    with TestClient(create_app(settings)) as client:
        signed_in = client.post("/api/auth/login", json={
            "email": "ana@finca.example", "password": "una-clave-larga",
        })
        assert signed_in.status_code == 200
        assert signed_in.json()["user"]["role"] == "owner"
        # And the second registration is a worker, so the count came from the
        # database rather than from process state.
        second = client.post("/api/auth/register", json={
            "email": "beto@finca.example", "password": "otra-clave-larga", "full_name": "Beto",
        })
        assert second.json()["user"]["role"] == "worker"


def test_the_hash_is_what_is_stored(tmp_path, engine_for):
    from sqlalchemy import text

    settings = migrated(tmp_path, "hash.db")
    with TestClient(create_app(settings)) as client:
        client.post("/api/auth/register", json={
            "email": "ana@finca.example", "password": "una-clave-larga", "full_name": "Ana",
        })

    with engine_for(settings.sqlalchemy_url).connect() as connection:
        stored = connection.execute(text("SELECT password_hash FROM users")).scalar_one()
    # bcrypt, and not the password in any form. The schema has specified this
    # since before any code could produce one.
    assert stored.startswith("$2b$")
    assert "una-clave-larga" not in stored


def test_the_herd_survives_a_restart(tmp_path):
    settings = migrated(tmp_path, "animals.db")
    with TestClient(create_app(settings)) as client:
        sign_in(client)
        created = client.post("/api/animals", json={
            "tag": "A-441", "name": "Lucera", "camera_id": "camera-01",
        })
        assert created.status_code == 201

    with TestClient(create_app(settings)) as client:
        sign_in(client, "otra@finca.example")
        listed = client.get("/api/animals").json()
        assert listed["total"] == 1
        animal = listed["items"][0]
        # The camera name round-trips through the numeric foreign key.
        assert animal["camera_id"] == "camera-01"
        assert animal["name"] == "Lucera"
        assert animal["status"] == "active"


def test_deactivation_survives_a_restart(tmp_path):
    settings = migrated(tmp_path, "deactivate.db")
    with TestClient(create_app(settings)) as client:
        sign_in(client)
        client.post("/api/animals", json={"tag": "A-441"})
        client.post("/api/animals/A-441/deactivate")

    with TestClient(create_app(settings)) as client:
        sign_in(client, "otra@finca.example")
        # The row is kept, not deleted: there is no DELETE on purpose.
        assert client.get("/api/animals/A-441").json()["status"] == "inactive"
        assert client.get("/api/animals", params={"status": "active"}).json()["total"] == 0


def test_a_zone_survives_a_restart_with_its_polygon(tmp_path):
    settings = migrated(tmp_path, "zones.db")
    outline = [{"x": 0.2, "y": 0.2}, {"x": 0.8, "y": 0.3}, {"x": 0.7, "y": 0.8}]
    with TestClient(create_app(settings)) as client:
        sign_in(client)
        created = client.post(
            "/api/zones/camera/camera-01", json=RECTANGLE | {"outline": outline},
        )
        assert created.status_code == 201

    with TestClient(create_app(settings)) as client:
        sign_in(client, "otra@finca.example")
        zone = client.get("/api/zones").json()["items"][0]
        assert zone["camera_id"] == "camera-01"
        # The polygon round-trips through JSON, which is where a column mapped
        # as text rather than JSON would surface.
        assert zone["outline"] == outline


def test_two_cameras_keep_their_own_zone_across_a_restart(tmp_path):
    settings = migrated(tmp_path, "two-zones.db")
    with TestClient(create_app(settings)) as client:
        sign_in(client)
        client.post("/api/zones/camera/camera-01", json=RECTANGLE)
        client.post("/api/zones/camera/camera-02", json=RECTANGLE)

    with TestClient(create_app(settings)) as client:
        sign_in(client, "otra@finca.example")
        # UNIQUE (camera_id, name) held: the same name twice is allowed across
        # cameras, and the constraint is in the database and not in Python.
        assert client.get("/api/zones").json()["total"] == 2
        assert client.get(
            "/api/zones", params={"camera_id": "camera-02"},
        ).json()["total"] == 1


def test_the_event_severity_survives_a_restart(tmp_path, event_payload):
    settings = migrated(tmp_path, "levels.db")
    with TestClient(create_app(settings)) as client:
        created = client.post("/api/ai/events", json=event_payload | {"confidence": 0.95})
        assert created.json()["level"] == "high"

    with TestClient(create_app(settings)) as client:
        # Read from the stored column, not recomputed on the way out.
        assert client.get("/api/events").json()["items"][0]["level"] == "high"
