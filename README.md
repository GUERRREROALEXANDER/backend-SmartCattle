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
  models.py                cameras and events tables
  core/config.py
  routes/health.py
  routes/animals.py
  routes/events.py
  routes/cameras.py
  schemas/animal.py
  schemas/camera.py
  schemas/event.py
  schemas/status.py
  services/event_store.py  memory and SQL event stores
  services/camera_store.py memory and SQL camera stores
migrations/                Alembic environment and revisions
alembic.ini
render.yaml                Render Blueprint (backend + PostgreSQL + frontend static site)
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

Without `DATABASE_URL` everything is kept in memory and lost on restart. With it, events and cameras are stored in PostgreSQL. The schema is created **only** by Alembic migrations, never at application startup.

Local PostgreSQL (replace the placeholders; never commit real credentials):

```powershell
psql -U postgres -c "CREATE DATABASE smartcattle"
# in .env:
# DATABASE_URL=postgresql://USER:PASSWORD@localhost:5432/smartcattle
alembic upgrade head
uvicorn app.main:app --reload
```

`postgres://` and `postgresql://` URLs (the form Render provides) are rewritten to the psycopg 3 driver automatically. Roll back with `alembic downgrade base`.

| Table | Content |
| --- | --- |
| `cameras` | One row per camera that ever reported status: last reported status, last report time, last time online, last error, resolution and FPS |
| `events` | AI events (`cattle_out_of_zone`), with optional bounding box |

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

`render.yaml` is a Render Blueprint that creates a free PostgreSQL database `smartcattle-db` and the web service `smartcattle-backend`. In Render: **New → Blueprint**, select this repository, then fill `ALLOWED_ORIGINS` with the frontend URL. `DATABASE_URL` comes from the database and `AI_API_KEY` is generated; copy the key into the AI service's `SMARTCATTLE_API_KEY` on the local PC. Each deploy runs `alembic upgrade head` before starting Uvicorn.

## Tests

```sh
python -m pytest -q
```

Tests cover endpoints, event and camera status validation, shared-key protection, CORS, configuration, error handling, per-app isolation, the 1000-event storage limit, and Alembic migrations plus SQL persistence on a temporary SQLite database. To also run them against a real PostgreSQL database (it is migrated up and back down, so use an empty test database):

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

`render.yaml` is a Render Blueprint that creates a free PostgreSQL database `smartcattle-db`, the web service `smartcattle-backend` and the static site `smartcattle-frontend`, built from the frontend repository. In Render: **New → Blueprint**, select this repository and apply. `ALLOWED_ORIGINS` and `VITE_API_BASE_URL` assume the default `*.onrender.com` names; if Render adds a suffix because a name is taken, update both. `DATABASE_URL` comes from the database and `AI_API_KEY` is generated; copy the key into the AI service's `SMARTCATTLE_API_KEY` on the local PC. Each deploy runs `alembic upgrade head` before starting Uvicorn.
