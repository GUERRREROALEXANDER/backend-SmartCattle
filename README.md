# SmartCattle Backend

## What is SmartCattle?

SmartCattle is a university Software Engineering project aimed at intelligent cattle monitoring using cameras and computer vision. The project is divided into three independent repositories: SmartCattle-Frontend, SmartCattle-Backend (this repository), and SmartCattle-AI.

## Repository responsibility

This repository provides the REST API, validates incoming AI events, exposes animal and event collections, and configures CORS. The animal collection is currently empty.

## What does not belong here?

Camera capture, image or video processing, OpenCV, YOLO, ultralytics, numpy, model weights, and frontend HTML/CSS/JS belong to other repositories. Flask and gunicorn are not used. No database is implemented.

## Architecture

```text
SmartCattle-Frontend
        │
        │ REST / HTTP
        ▼
SmartCattle-Backend   ◄── POST /api/ai/events ──┐
        │                                       │
        │ REST / HTTP (planned)                 │
        ▼                                       │
SmartCattle-AI  ────────────────────────────────┘
   OpenCV + YOLO
        │
        ▼
 Camera / Video
```

This diagram describes the project boundaries and intended integration. The backend does not call the AI service yet. AI event ingestion is implemented as `AI → Backend` through `POST /api/ai/events`.

See [Architecture decisions and event contract](docs/ARCHITECTURE.md).

## Technologies

Python 3.13, FastAPI, Uvicorn, and Pydantic Settings. Tests use pytest and httpx through FastAPI's TestClient.

## Structure

```text
app/
  __init__.py
  main.py
  core/config.py
  routes/health.py
  routes/animals.py
  routes/events.py
  schemas/animal.py
  schemas/event.py
  schemas/status.py
  services/event_store.py
docs/ARCHITECTURE.md
tests/
  conftest.py
  test_health.py
  test_animals.py
  test_events.py
  test_config.py
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

In PowerShell, use `$env:PORT` for an environment variable. Run a single Uvicorn process: memory storage is not shared between workers.

## Endpoints

| Method | Path | Response |
| --- | --- | --- |
| GET | `/` | Service name, version, and docs path |
| GET | `/health` | `{"status":"ok"}` deployment health check |
| GET | `/api/status` | Version, AI configuration flag, and storage type |
| GET | `/api/animals` | Empty animal collection with total zero |
| GET | `/api/events` | Stored events, most recently received first, and total |
| POST | `/api/ai/events` | Validates and stores an AI event; returns 201 with UUID and UTC receipt time |

Invalid event bodies return 422. If a shared key is configured, a missing or incorrect key returns 401 with `Invalid or missing API key`. Unhandled errors return 500 with `Internal server error` without a trace in the response.

## API documentation

Open `/docs` for Swagger UI or `/redoc` for ReDoc. In `/docs`, use **Authorize** to supply `X-API-Key`. The event schema includes descriptions and a complete example.

## Environment variables

Settings load from `.env` and the environment; environment variables take precedence. The backend starts without any variables defined. Settings are cached; restart after changes.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ALLOWED_ORIGINS` | `http://localhost:3000,http://localhost:5173,http://127.0.0.1:5500` | Comma-separated origins; spaces and empty entries are removed. `*` is rejected. |
| `SMARTCATTLE_AI_URL` | Empty | Optional HTTP(S) URL for future integration; empty means unset. No outgoing AI requests are implemented. |
| `AI_API_KEY` | Empty | Optional shared key for event ingestion; empty means unset. Set it in production. |

When `AI_API_KEY` is unset, event ingestion is open for development. CORS allows GET and POST, accepts Content-Type and X-API-Key, and does not allow credentials. Status responses never expose the AI URL or key.

## Tests

```sh
python -m pytest -q
```

Tests cover endpoints, event validation, shared-key protection, CORS, configuration, error handling, per-app isolation, and the 1000-event storage limit.

## Future SmartCattle-AI integration

The AI service can send this event to the existing ingestion endpoint:

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

The backend assigns `id` and `received_at`. Detection timestamps must include a timezone. Camera and object names are trimmed and must contain 1–64 characters. Confidence must be between 0 and 1. Extra fields and unknown event types are rejected.

## Future PostgreSQL integration

Replace `InMemoryEventStore` with PostgreSQL-backed storage exposing `add()` and `list()`, and update app initialization. Routes receive their store from app state. No PostgreSQL dependency or connection exists today.

## Current state

Events are stored in memory and are lost on restart. Only the latest 1000 received events are retained; the oldest are discarded. Data is not shared between processes. There is no database, user authentication, or notification system. The optional ingestion key is service-level protection. Individual animal identification and outgoing AI calls are not implemented.
