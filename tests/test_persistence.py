"""Checks that the new tables survive a restart.

The in-memory stores are covered elsewhere. These run against a real database
through the migrations, which is the only place the column mapping, the
constraints and the timezone handling are all exercised at once.
"""

import os

from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_database import alembic_config, database_settings, reset_postgresql, sign_in

RECTANGLE = {"name": "Safe zone", "x_min": 0.1, "y_min": 0.1, "x_max": 0.9, "y_max": 0.9}


def migrated(tmp_path, name: str):
    """A freshly migrated database: SQLite by default, PostgreSQL when TEST_DATABASE_URL is set.

    SQLite does not enforce what PostgreSQL does -- row locks, SKIP LOCKED,
    foreign keys to non-unique columns, timezone round trips -- so CI runs
    this module against PostgreSQL too. That database is rebuilt for every
    test, so point it at a throwaway database only.
    """
    from alembic import command

    url = os.getenv("TEST_DATABASE_URL") or f"sqlite:///{(tmp_path / name).as_posix()}"
    settings = database_settings(url)
    config = alembic_config(settings.sqlalchemy_url)
    if settings.storage_name == "postgresql":
        reset_postgresql(settings.sqlalchemy_url)
    command.upgrade(config, "head")
    return settings


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
        # Filtered: on PostgreSQL the shared schema seeds a "Main camera" zone too.
        zone = client.get("/api/zones", params={"camera_id": "camera-01"}).json()["items"][0]
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
        # Compared by camera: on PostgreSQL the shared schema seeds a "Main camera" zone too.
        cameras = [zone["camera_id"] for zone in client.get("/api/zones").json()["items"]]
        assert sorted(camera for camera in cameras if camera != "Main camera") == ["camera-01", "camera-02"]
        assert client.get(
            "/api/zones", params={"camera_id": "camera-02"},
        ).json()["total"] == 1


def test_alerts_and_their_handling_survive_a_restart(tmp_path, event_payload):
    settings = migrated(tmp_path, "alerts.db")
    with TestClient(create_app(settings)) as client:
        sign_in(client)
        event = client.post("/api/ai/events", json=event_payload).json()
        client.post("/api/ai/events", json=event_payload | {"camera_id": "camera-02", "confidence": 0.5})
        alerts = client.get("/api/alerts").json()
        assert alerts["total"] == 2
        linked = next(alert for alert in alerts["items"] if alert["camera_id"] == "camera-01")
        assert linked["event_id"] == event["id"] and linked["severity"] == "high"
        assert client.post(f"/api/alerts/{linked['id']}/acknowledge").json()["status"] == "acknowledged"
        assert client.post(f"/api/alerts/{linked['id']}/acknowledge").status_code == 409

    with TestClient(create_app(settings)) as client:
        sign_in(client, "otra@finca.example")
        stored = client.get(f"/api/alerts/{linked['id']}").json()
        assert stored["status"] == "acknowledged" and stored["acknowledged_by"] == 1
        assert client.post(f"/api/alerts/{linked['id']}/resolve").json()["resolved_by"] == 2

        def total(**params):
            return client.get("/api/alerts", params=params).json()["total"]

        assert total(status="resolved") == 1
        assert total(status="open", severity="low", camera_id="camera-02") == 1
        assert total(camera_id="camera-03") == 0
        assert client.get("/api/alerts/00000000-0000-4000-8000-000000000000").status_code == 404
        assert client.get("/api/alerts", params={"limit": 1, "offset": 1}).json()["items"][0]["id"] in {
            alert["id"] for alert in alerts["items"]
        }


def test_head_counts_are_kept_per_camera_and_newest_wins(tmp_path):
    settings = migrated(tmp_path, "occupancy.db")
    with TestClient(create_app(settings)) as client:
        def report(camera, inside, observed_at):
            return client.put(f"/api/ai/cameras/{camera}/occupancy", json={
                "cattle_inside": inside, "cattle_outside": 1, "observed_at": observed_at,
            })

        assert report("camera-02", 7, "2026-10-31T15:30:00Z").status_code == 204
        report("camera-01", 12, "2026-10-31T15:30:05Z")
        report("camera-01", 3, "2026-10-31T15:30:00Z")
        report("camera-02", 8, "2026-10-31T15:30:01Z")
        store = client.app.state.occupancy_store
        counts = [(item.camera_id, item.cattle_inside) for item in store.list()]
        assert counts == [("camera-01", 12), ("camera-02", 8)]
        assert store.list()[0].observed_at.tzinfo is not None

    with TestClient(create_app(settings)) as client:
        sign_in(client)
        live = client.get("/api/metrics/live").json()
        # Received moments ago, so still fresh after the restart.
        assert (live["cattle_inside"], live["cattle_outside"], live["open_alerts"]) == (20, 2, 0)


def test_the_video_queue_holds_against_the_database(tmp_path, video_analysis_payload):
    from datetime import UTC, datetime, timedelta

    from app.services.video_upload_store import JOB_LEASE, MAX_ATTEMPTS
    from tests.test_video_uploads import FakeStorage

    settings = migrated(tmp_path, "videos.db")
    moment = [datetime.now(UTC)]
    with TestClient(create_app(settings)) as client:
        storage = FakeStorage()
        client.app.state.video_storage = storage
        client.app.state.clock = lambda: moment[0]
        sign_in(client)

        def upload(name):
            ticket = client.post("/api/videos/uploads", json={
                "file_name": name, "size_bytes": 512 * 1024 ** 2, "content_type": "video/webm",
            }).json()["upload"]
            storage.objects[f"videos/{ticket['id']}.webm"] = ticket["size_bytes"]
            assert client.post(f"/api/videos/uploads/{ticket['id']}/complete").json()["status"] == "queued"
            moment[0] += timedelta(seconds=1)
            return ticket["id"]

        doomed, finished = upload("doomed.webm"), upload("finished.webm")
        for attempt in range(1, MAX_ATTEMPTS + 1):
            job = client.post("/api/ai/video-jobs/claim").json()
            assert (job["job_id"], job["attempt"]) == (doomed, attempt)
            moment[0] += JOB_LEASE + timedelta(seconds=1)
        # The exhausted video is failed on the way and the next one is handed out.
        assert client.post("/api/ai/video-jobs/claim").json()["job_id"] == finished
        client.put(f"/api/ai/video-jobs/{finished}/progress", json={"frames_processed": 50, "frames_total": 125})
        assert client.post(f"/api/ai/video-jobs/{finished}/complete", json=video_analysis_payload).status_code == 200
        assert client.post(f"/api/ai/video-jobs/{finished}/fail", json={"error": "late"}).status_code == 409
        assert client.post("/api/ai/video-jobs/claim").status_code == 204

    with TestClient(create_app(settings)) as client:
        sign_in(client, "luis@finca.example")
        listing = client.get("/api/videos/uploads").json()
        assert [(item["id"], item["status"]) for item in listing["items"]] == [
            (finished, "completed"), (doomed, "failed"),
        ]
        done = listing["items"][0]
        assert done["analysis_id"] is not None and done["frames_total"] == 125
        assert datetime.fromisoformat(done["finished_at"]).tzinfo is not None
        assert client.get("/api/videos/uploads", params={"status": "failed"}).json()["total"] == 1
        missing = "00000000-0000-4000-8000-000000000000"
        assert client.get(f"/api/videos/uploads/{missing}").status_code == 404
        assert client.put(f"/api/ai/video-jobs/{missing}/progress", json={"frames_processed": 1}).status_code == 404


def test_account_management_holds_against_the_database(tmp_path):
    settings = migrated(tmp_path, "accounts.db")
    with TestClient(create_app(settings)) as client:
        sign_in(client)
        owner_id = client.get("/api/auth/me").json()["id"]
        worker = client.post("/api/users", json={
            "email": "carla@finca.example", "password": "clave-inicial-1", "full_name": "Carla",
        }).json()
        assert client.post("/api/users", json={
            "email": "carla@finca.example", "password": "clave-inicial-1", "full_name": "Carla",
        }).status_code == 409
        assert client.patch(f"/api/users/{owner_id}", json={"role": "worker"}).status_code == 409
        assert client.patch(f"/api/users/{worker['id']}", json={"role": "owner"}).json()["role"] == "owner"
        assert client.patch(f"/api/users/{owner_id}", json={"is_active": False}).status_code == 200

    with TestClient(create_app(settings)) as client:
        signed_in = client.post("/api/auth/login", json={
            "email": "carla@finca.example", "password": "clave-inicial-1",
        }).json()
        client.headers.update({"Authorization": f"Bearer {signed_in['access_token']}"})
        assert signed_in["user"]["role"] == "owner"
        page = client.get("/api/users", params={"is_active": True}).json()
        assert [user["email"] for user in page["items"]] == ["carla@finca.example"]
        assert client.get(f"/api/users/{owner_id}").json()["is_active"] is False
        assert client.patch(f"/api/users/{worker['id']}", json={"role": "worker"}).status_code == 409

        assert client.put("/api/auth/me/password", json={
            "current_password": "clave-inicial-1", "new_password": "clave-nueva-123",
        }).status_code == 204

    with TestClient(create_app(settings)) as client:
        assert client.post("/api/auth/login", json={
            "email": "carla@finca.example", "password": "clave-nueva-123",
        }).status_code == 200


def test_a_farm_and_its_lots_survive_a_restart(tmp_path):
    settings = migrated(tmp_path, "farms.db")
    boundary = [
        {"latitude": 8.1, "longitude": -75.1}, {"latitude": 8.2, "longitude": -75.1},
        {"latitude": 8.2, "longitude": -75.2},
    ]
    with TestClient(create_app(settings)) as client:
        sign_in(client)
        farm = client.post("/api/farms", json={
            "name": "La Esperanza", "coordinates": {"latitude": 8.75, "longitude": -75.88},
        }).json()
        lots_url = f"/api/farms/{farm['id']}/lots"
        created = client.post(lots_url, json={
            "name": "North paddock", "kind": "paddock", "boundary": boundary, "camera_id": "camera-01",
        })
        assert created.status_code == 201
        client.post(lots_url, json={"name": "Stable", "kind": "stable", "active": False})

    with TestClient(create_app(settings)) as client:
        sign_in(client, "otra@finca.example")
        stored = client.get(f"/api/farms/{farm['id']}").json()
        assert stored["coordinates"] == {"latitude": 8.75, "longitude": -75.88}
        assert stored["lot_count"] == 2
        lots = client.get(lots_url, params={"active": True}).json()
        assert lots["total"] == 1
        lot = lots["items"][0]
        # The camera name round-trips through the numeric foreign key and the
        # polygon through JSON.
        assert lot["camera_id"] == "camera-01"
        assert lot["boundary"] == boundary


def test_farm_rules_hold_against_the_database(tmp_path):
    settings = migrated(tmp_path, "farm-rules.db")
    with TestClient(create_app(settings)) as client:
        sign_in(client)
        farm = client.post("/api/farms", json={
            "name": "La Esperanza", "location": "Montería", "coordinates": {"latitude": 1, "longitude": 2},
        }).json()
        assert client.post("/api/farms", json={"name": "La Esperanza"}).status_code == 409
        client.post("/api/farms", json={"name": "El Roble"})
        assert client.patch(f"/api/farms/{farm['id']}", json={"name": "El Roble"}).status_code == 409

        cleared = client.patch(f"/api/farms/{farm['id']}", json={"coordinates": None}).json()
        assert cleared["coordinates"] is None and cleared["location"] == "Montería"

        lot = client.post(f"/api/farms/{farm['id']}/lots", json={"name": "Pen", "kind": "pen"}).json()
        client.post(f"/api/farms/{farm['id']}/lots", json={"name": "Pen 2", "kind": "pen"})
        assert client.post(f"/api/farms/{farm['id']}/lots", json={"name": "Pen", "kind": "pen"}).status_code == 409
        assert client.patch(f"/api/lots/{lot['id']}", json={"name": "Pen 2"}).status_code == 409
        moved = client.patch(f"/api/lots/{lot['id']}", json={"camera_id": "camera-07", "kind": "stable"}).json()
        assert moved["camera_id"] == "camera-07" and moved["kind"] == "stable"
        assert client.get(f"/api/lots/{lot['id']}").json() == moved

        assert client.delete(f"/api/farms/{farm['id']}").status_code == 409
        for item in client.get(f"/api/farms/{farm['id']}/lots").json()["items"]:
            assert client.delete(f"/api/lots/{item['id']}").status_code == 204
        assert client.delete(f"/api/farms/{farm['id']}").status_code == 204
        assert client.get("/api/farms").json()["total"] == 1


def test_lot_measurements_survive_a_restart(tmp_path):
    settings = migrated(tmp_path, "lot-dimensions.db")
    with TestClient(create_app(settings)) as client:
        sign_in(client)
        farm = client.post("/api/farms", json={"name": "La Esperanza"}).json()
        lots_url = f"/api/farms/{farm['id']}/lots"
        bare = client.post(lots_url, json={"name": "Lote 1"}).json()
        measured = client.post(lots_url, json={"name": "Corral", "length_meters": 40, "width_meters": 25}).json()
        resized = client.patch(f"/api/lots/{measured['id']}", json={"area_square_meters": 1200}).json()

    with TestClient(create_app(settings)) as client:
        sign_in(client, "otra@finca.example")
        assert client.get(f"/api/lots/{bare['id']}").json()["kind"] == "paddock"
        stored = client.get(f"/api/lots/{measured['id']}").json()
        assert stored == resized
        assert (stored["length_meters"], stored["width_meters"], stored["area_square_meters"]) == (None, None, 1200)


def test_the_event_severity_survives_a_restart(tmp_path, event_payload):
    settings = migrated(tmp_path, "levels.db")
    with TestClient(create_app(settings)) as client:
        created = client.post("/api/ai/events", json=event_payload | {"confidence": 0.95})
        assert created.json()["level"] == "high"

    with TestClient(create_app(settings)) as client:
        # Read from the stored column, not recomputed on the way out.
        assert client.get("/api/events").json()["items"][0]["level"] == "high"
