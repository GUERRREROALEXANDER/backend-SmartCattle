# SmartCattle Backend

## What is SmartCattle?

SmartCattle is a university Software Engineering project aimed at intelligent cattle monitoring using cameras and computer vision. The project is divided into three independent repositories: SmartCattle-Frontend, SmartCattle-Backend (this repository), and SmartCattle-AI.

## Repository responsibility

This repository provides the REST API, validates incoming AI events and camera status reports, persists them in PostgreSQL, exposes animal, event and camera collections, and configures CORS. The animal collection is currently empty.

## What does not belong here?

Camera capture, image or video processing, OpenCV, YOLO, ultralytics, numpy, model weights, and frontend HTML/CSS/JS belong to other repositories. Flask and gunicorn are not used.

## Architecture

```text
 Render (Internet)                         Local PC (farm network)
┌──────────────────────────┐              ┌──────────────────────────┐
│ SmartCattle-Frontend     │              │ SmartCattle-AI           │
│        │ REST            │    HTTPS     │ OpenCV + YOLO            │
│        ▼                 │ ◄─────────── │  POST /api/ai/events     │
│ SmartCattle-Backend      │              │  PUT  /api/ai/cameras/…  │
│        │ SQLAlchemy      │              │        ▲ RTSP (LAN only) │
│        ▼                 │              └────────┼─────────────────┘
│ PostgreSQL               │                       │
└──────────────────────────┘                IP camera (IMOU)
```

The AI service runs next to the camera because the camera is only reachable on the local network; Render cannot open a connection to it. The AI never talks to PostgreSQL: it pushes events and camera status to this backend over HTTPS. The backend does not call the AI service.

See [Architecture decisions and event contract](docs/ARCHITECTURE.md).

## Technologies

Python 3.13, FastAPI, Uvicorn, Pydantic Settings, SQLAlchemy 2, Alembic and psycopg 3 (PostgreSQL). Tests use pytest and httpx through FastAPI's TestClient.

## Structure

```text
app/
  __init__.py
  main.py                  create_app(): picks SQL or memory stores
  db.py                    SQLAlchemy Base, engine and sessions
  models.py                shared cameras/events and backend-owned video analysis tables
  core/config.py
  routes/health.py
  routes/animals.py
  routes/events.py
  routes/cameras.py
  routes/videos.py
  routes/metrics.py
  schemas/animal.py
  schemas/camera.py
  schemas/event.py
  schemas/video_analysis.py
  schemas/metrics.py
  schemas/status.py
  services/event_store.py  memory and SQL event stores
  services/camera_store.py memory and SQL camera stores
  services/video_analysis_store.py memory and SQL analysis stores
database/schema.sql        Copy of the shared database schema
migrations/                Alembic environment and revisions
alembic.ini
render.yaml                Render Blueprint (backend + frontend static site)
data_structures/
docs/ARCHITECTURE.md
tests/
requirements.txt
requirements-dev.txt
.env.example
```

## Installation

Run commands from the repository root with Python 3.13 installed.

Windows PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Linux:

```sh
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

The `.env` file is optional. For deployment, install only `requirements.txt`.

## Running

Local development:

```sh
uvicorn app.main:app --reload
```

Cloud deployment, with `PORT` supplied by the platform (POSIX shell):

```sh
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

In PowerShell, use `$env:PORT` for an environment variable. Without `DATABASE_URL`, run a single Uvicorn process: memory storage is not shared between workers.

## Database

Without `DATABASE_URL` everything is kept in memory and lost on restart. With it, events, cameras and video analyses are stored in PostgreSQL. The shared schema is owned by the [database repository](https://github.com/walterjojoa/BASEDATOSSmartCattle); `database/schema.sql` is a copy of it (commit `baa9ef4`). The application never creates tables at startup. `alembic upgrade head` does three things:

1. `0001_shared_schema` applies `database/schema.sql` only if the database has no `cameras` table yet. An existing shared database is left as it is.
2. `0002_camera_status` adds the nullable camera status columns the backend needs to `cameras`: `reported_status`, `last_report_at`, `last_online_at`, `last_error`, `frame_width`, `frame_height`, `fps` and `stream_url`.
3. `0003_video_analyses` creates the backend-owned `video_analyses` and `video_analysis_alerts` tables. It does not alter any existing table.

Alembic also creates its own `alembic_version` table. `alembic downgrade base` removes the video analysis tables and status columns; the shared tables and their rows stay.

Local PostgreSQL (replace the placeholders; never commit real credentials):

```powershell
psql -U postgres -c "CREATE DATABASE smartcattle"
# in .env:
# DATABASE_URL=postgresql://USER:PASSWORD@localhost:5432/smartcattle
alembic upgrade head
uvicorn app.main:app --reload
```

`postgres://` and `postgresql://` URLs (the form Render provides) are rewritten to the psycopg 3 driver automatically.

How the API maps to the shared tables:

| API | Database |
| --- | --- |
| Camera `id` (`camera-01`) | `cameras.name`. A camera named for the first time by a status report or an event gets a row. |
| `GET /api/cameras` | Only cameras with `last_report_at`; seed rows such as `Main camera` are not listed until the AI reports them. |
| Event `id` (UUID) | `events.ai_event_id`. `event_type` → `type`, `timestamp` → `detected_at`, `bbox` → `box`, `camera_id` → FK to `cameras.id`. |
| `GET /api/events` | Only events with `ai_event_id` and `received_at`, i.e. those ingested through this API. |
| `POST /api/videos/analyses` | A `video_analyses` row plus one `video_analysis_alerts` row per submitted alert. `alert_count` is derived from the array length. |
| `GET /api/videos/analyses` | Backend-owned analyses, newest `created_at` first, with their alerts. |

`events.confidence` is `REAL` in the shared schema, so values are read back rounded to six significant digits.

Per-frame detections are not stored: the AI only sends relevant events. See [ARCHITECTURE.md](docs/ARCHITECTURE.md#8-postgresql-and-camera-status).

## Endpoints

| Method | Path | Response |
| --- | --- | --- |
| GET | `/` | Service name, version, and docs path |
| GET | `/health` | `{"status":"ok"}` deployment health check |
| GET | `/api/status` | Version, AI configuration flag, and storage type (`memory` or `postgresql`) |
| GET | `/api/animals` | Empty animal collection with total zero |
| GET | `/api/events` | Stored events, most recently received first (at most 1000), and total |
| POST | `/api/ai/events` | Validates and stores an AI event; returns 201 with UUID and UTC receipt time |
| GET | `/api/cameras` | Cameras that reported status, sorted by id, with their effective status |
| PUT | `/api/ai/cameras/{camera_id}/status` | AI heartbeat: stores the camera status; returns the camera |
| POST | `/api/videos/analyses` | Stores a simulated video analysis; returns 201 with the analysis and alerts |
| GET | `/api/videos/analyses?limit=20` | Latest analyses first; `limit` defaults to 20 and accepts 1–100 |
| GET | `/api/videos/analyses/{id}` | One analysis with its alerts, or a JSON 404 |
| GET | `/api/metrics/summary?since=<ISO>` | Event rates and latest-analysis animal estimates; `since` is optional and timezone-aware |

### Video analyses and metrics

For a frontend `API_BASE` of `http://localhost:8000/api`, `${API_BASE}/videos/analyses` resolves to `http://localhost:8000/api/videos/analyses`. If the configured base is only `http://localhost:8000`, append `/api/videos/analyses` instead. The metrics route follows the same rule: `/api/metrics/summary`.

`POST /api/videos/analyses` accepts `video_name`, `duration_sec`, `frames_analyzed`, `max_cattle`, `max_people`, `alerts`, `avg_confidence` and `outside_ratio`. Counts, track ids and seconds must be non-negative; confidence and ratio must be finite numbers from 0 to 1. Names are trimmed and limited to 200 characters, and at most 500 alerts are accepted. Each alert has `track_id`, `cls` (`cattle` or `person`) and `time_sec`. Unknown fields are rejected. The response adds a UUID `id`, an integer alert `id`, `alert_count` and `created_at`.

Every cattle alert also creates a `cattle_out_of_zone` event with `camera_id: "video-upload"`, `detected_object: "cow"` and the analysis average confidence. The existing schema permits that camera name, so no shared column is changed; a camera row is created if needed but does not appear in `GET /api/cameras` without a status report. The shared `events.source` keeps its default. Person alerts do not create events. Because `time_sec` has no recording wall-clock reference, all generated events use the analysis `created_at` as their detection timestamp; the relative time remains in `video_analysis_alerts`.

`GET /api/metrics/summary` returns `animals_inside`, `animals_outside`, `alerts_today`, `alerts_since` and `detections_per_minute`. Its assumptions are:

- Animal counts are null until an analysis exists. Otherwise, distinct cattle `track_id` values in the latest analysis estimate the outside count (capped at `max_cattle`), and the remainder is inside. They are not live individual-animal locations.
- Alert counts use API-ingested event detection timestamps in UTC. `alerts_today` starts at 00:00 UTC; `alerts_since` is null without `since` and otherwise counts timestamps from `since` through the current time.
- `detections_per_minute` is the number of stored API events in the trailing 60 minutes divided by 60. It is an event rate, not a per-frame YOLO detection rate. Future-dated events are excluded.

### Camera status

The AI service reports each camera with `PUT /api/ai/cameras/{camera_id}/status` (same `X-API-Key` as events):

```json
{"status": "online", "frame_width": 1280, "frame_height": 720, "fps": 14.8, "observed_at": "2026-10-06T15:30:00Z"}
{"status": "error", "error": "Stream read timeout", "observed_at": "2026-10-06T15:31:00Z"}
```

`status` is `online`, `error` or `offline`. `error` is only accepted with `status: "error"`, holds at most 300 characters and may not contain a URL (`://`), so stream URLs with credentials can never be stored. `camera_id` must match `^[A-Za-z0-9_-]{1,64}$`.

Optional `stream_url` is the public `http(s)` base URL of the AI service video (it serves `/video.mjpg` and `/status`), for example a Cloudflare tunnel. It may not contain credentials, holds at most 300 characters, is returned by `GET /api/cameras` and is kept only while the camera reports `online`. The frontend uses it to show the video.

`GET /api/cameras` returns `status` (effective) and `reported_status`. If the last report is older than `CAMERA_OFFLINE_AFTER_SECONDS`, the effective status is `offline`: the backend cannot claim a camera is online unless the AI service keeps confirming it. Cameras appear only after their first report; an empty list means no camera is connected.

Invalid event bodies return 422. If a shared key is configured, a missing or incorrect key returns 401 with `Invalid or missing API key`. Unhandled errors return 500 with `Internal server error` without a trace in the response.

## Frontend integration

Set `BACKEND_BASE_URL` in the frontend to `http://localhost:8000` for local development. The GET responses have these JSON shapes:

| Endpoint | Response JSON |
| --- | --- |
| `/` | `{"name":"SmartCattle Backend","version":"<version>","docs":"/docs"}` |
| `/health` | `{"status":"ok"}` |
| `/api/status` | `{"status":"ok","version":"<version>","ai_service":{"configured":false},"storage":"memory"}` (`configured` may be `true`) |
| `/api/animals` | `{"items":[],"total":0}` |
| `/api/events` | `{"items":[{"event_type":"cattle_out_of_zone","camera_id":"camera-01","detected_object":"cow","confidence":0.95,"timestamp":"2026-10-03T15:30:00Z","bbox":[120.0,80.5,340.0,300.0],"id":"<uuid>","received_at":"2026-10-03T15:30:01Z"}],"total":1}` (empty: `{"items":[],"total":0}`; `bbox` may be `null`) |
| `/api/cameras` | `{"items":[{"id":"camera-01","reported_status":"online","status":"online","last_report_at":"<UTC>","last_online_at":"<UTC>","last_error":null,"frame_width":1280,"frame_height":720,"fps":14.8}],"total":1}` (no camera yet: `{"items":[],"total":0}`) |
| `/api/videos/analyses` | `{"items":[{"id":"<uuid>","video_name":"pasture-test.mp4","duration_sec":12.5,"frames_analyzed":125,"max_cattle":4,"max_people":1,"alerts":[{"track_id":7,"cls":"cattle","time_sec":3.2,"id":1}],"avg_confidence":0.91,"outside_ratio":0.25,"alert_count":1,"created_at":"<UTC>"}],"total":1}` |
| `/api/metrics/summary` | `{"animals_inside":3,"animals_outside":1,"alerts_today":2,"alerts_since":null,"detections_per_minute":0.03333333333333333}` (`since` adds an integer `alerts_since`; animal counts may be null) |

Validation errors (422) use `{"detail":[{"type":"<error type>","loc":["body","<field>"],"msg":"<message>"}]}`. Error objects may also contain safe `ctx` details, but never the submitted `input`. Errors 401, 404, and 500 use a string in `detail`. Timestamps are ISO 8601 with a timezone. `/api/events` is newest first and retains at most 1000 events. Confidence must be a finite number between 0 and 1 inclusive.

## API documentation

Open `/docs` for Swagger UI or `/redoc` for ReDoc. In `/docs`, use **Authorize** to supply `X-API-Key`. The event schema includes descriptions and a complete example.

## Environment variables

Settings load from `.env` and the environment; environment variables take precedence. The backend starts without any variables defined. Settings are cached; restart after changes.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ALLOWED_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173,http://localhost:3000,http://127.0.0.1:5500` | Comma-separated HTTP(S) origins; spaces, trailing slashes, and empty entries are removed. `*` is rejected. |
| `SMARTCATTLE_AI_URL` | Empty | Optional HTTP(S) URL for future integration; empty means unset. No outgoing AI requests are implemented. |
| `AI_API_KEY` | Empty | Optional shared key for event and camera status ingestion; empty means unset. Set it in production. |
| `DATABASE_URL` | Empty | PostgreSQL URL. Empty means in-memory storage. Never exposed by any endpoint. |
| `CAMERA_OFFLINE_AFTER_SECONDS` | `60` | A camera without a report for longer than this (10–3600) is shown as `offline`. |

When `AI_API_KEY` is unset, ingestion is open for development. CORS allows GET, POST and PUT, accepts Content-Type and X-API-Key, and does not allow credentials. Status responses never expose the AI URL, key or database URL.

For production, set `ALLOWED_ORIGINS=https://your-frontend-domain.example`.

## Deploying on Render

`render.yaml` is a Render Blueprint that creates the web service `smartcattle-backend`; it uses the team's shared PostgreSQL instead of creating a database. In Render: **New → Blueprint**, select this repository, then fill `ALLOWED_ORIGINS` with the frontend URL and `DATABASE_URL` with the shared database URL (the Internal Database URL if it is in the same Render account and region, otherwise the External one). `AI_API_KEY` is generated; copy the key into the AI service's `SMARTCATTLE_API_KEY` on the local PC. Each deploy runs `alembic upgrade head` before starting Uvicorn.

## Tests

```sh
python -m pytest -q
```

Tests cover endpoints, event/camera/video validation, video-to-event mapping, summary metric windows, shared-key protection, CORS, configuration, error handling, per-app isolation, the 1000-event storage limit, and Alembic migrations plus SQL persistence on a temporary SQLite database. To also run them against a real PostgreSQL database (use an empty test database, never the shared one: the first run applies `database/schema.sql`, and the test deletes the rows it creates):

```powershell
$env:TEST_DATABASE_URL = "postgresql://USER:PASSWORD@localhost:5432/smartcattle_test"
python -m pytest -q
```

## SmartCattle-AI integration

The AI service sends this event to the ingestion endpoint:

```json
{
  "event_type": "cattle_out_of_zone",
  "camera_id": "camera-01",
  "detected_object": "cow",
  "confidence": 0.95,
  "timestamp": "2026-10-03T15:30:00Z"
}
```

Example request in a POSIX shell (replace the sample key with the configured value):

```sh
curl -X POST http://localhost:8000/api/ai/events \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: example-shared-key' \
  -d '{"event_type":"cattle_out_of_zone","camera_id":"camera-01","detected_object":"cow","confidence":0.95,"timestamp":"2026-10-03T15:30:00Z"}'
```

The backend assigns `id` and `received_at`. Detection timestamps must include a timezone. Camera and object names are trimmed and must contain 1–64 characters. Confidence must be a finite number between 0 and 1 inclusive. The optional `bbox` is `[x1, y1, x2, y2]` in frame pixels: four finite numbers ≥ 0 with `x1 < x2` and `y1 < y2`. Extra fields and unknown event types are rejected.

## Data structure examples

Independent academic examples demonstrate how data structures could apply to SmartCattle using only the Python standard library. They are not used by the API; see [the examples guide](data_structures/README.md) for details.

| Structure | Principle | SmartCattle application |
|---|---|---|
| List/Array | Indexed access and traversal | Currently detected animals |
| Stack | LIFO | Recent event history |
| Queue | FIFO | Pending events |
| Linked list | Linked nodes | Event sequence |

Run from the repository root:

```sh
python data_structures/array_smartcattle.py
python data_structures/stack_smartcattle.py
python data_structures/queue_smartcattle.py
python data_structures/linked_list_smartcattle.py
```

## Current state

`render.yaml` is a Render Blueprint that creates the web service `smartcattle-backend` and the static site `smartcattle-frontend`, built from the frontend repository. The database is the team's shared PostgreSQL, not part of the blueprint. In Render: **New → Blueprint**, select this repository, enter `DATABASE_URL` when asked and apply. `ALLOWED_ORIGINS` and `VITE_API_BASE_URL` assume the default `*.onrender.com` names; if Render adds a suffix because a name is taken, update both. `AI_API_KEY` is generated; copy the key into the AI service's `SMARTCATTLE_API_KEY` on the local PC. Each deploy runs `alembic upgrade head` before starting Uvicorn.
