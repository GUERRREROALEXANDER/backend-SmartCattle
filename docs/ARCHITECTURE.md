# SmartCattle-Backend — Architecture Decisions

Maintained by the project architect. Records what was kept from the previous
prototype, what was discarded, and why.

## 1. Audit of the previous prototype

The prototype was a single Flask application that mixed the API, YOLO
inference and camera capture, and also served the static frontend.

| Previous file | Classification | Reason |
|---|---|---|
| `backend/app.py` → `GET /` | **C. Refactor** | The welcome endpoint idea is kept, rewritten in FastAPI. |
| `backend/app.py` → `/api/backend-status` | **C. Refactor** | Becomes `GET /health` (liveness) and `GET /api/status` (service status). |
| `backend/app.py` → `create_app()` | **C. Refactor** | The application factory pattern is kept: it enables isolated tests. |
| `backend/app.py` → `/api/detectar` | **B. Move to SmartCattle-AI** | Decodes images with OpenCV and runs YOLO. |
| `backend/app.py` → `/api/camara/fotograma` | **B. Move to SmartCattle-AI** | Opens the camera with `cv2.VideoCapture`. |
| `backend/app.py` → `SAFE_ZONE`, `YOLO_*`, `CAMERA_SOURCE` | **B. Move to SmartCattle-AI** | Vision configuration, not API configuration. |
| `backend/app.py` → serving `index.html`, CSS, JS | **D. Remove** | Belongs to SmartCattle-Frontend. |
| `backend/app.py` → `X-Content-Type-Options` header | **D. Not used for now** | With no static content served, its value is marginal. |
| `backend/detector.py` | **B. Move to SmartCattle-AI** | YOLO inference (ultralytics). |
| `backend/camera.py` | **B. Move to SmartCattle-AI** | Video capture loop. |
| `backend/rules.py` | **B. Move to SmartCattle-AI** | Geometric rules over detection boxes. **Its output (the `ganado_fuera_zona` event) defines the `POST /api/ai/events` contract.** |
| `backend/test_backend.py` | **B / D** | Image, camera and zone tests go with the AI. The idea of injecting fake dependencies is kept. |
| `requirements.txt` | **D. Remove** | flask, ultralytics, opencv, numpy and gunicorn do not apply. |
| `render.yaml`, `gunicorn.conf.py`, `wsgi.py` | **D. Not used** | Tied to Flask + YOLO + frontend. The new deployment uses only `uvicorn`. |
| `test_deployment.py` | **D. Remove** | Tests frontend file serving. |
| `.gitignore` | **C. Refactor** | Useful base (`.env`, `__pycache__`, `.venv`, `*.pt`). |
| `frontend/` | **Out of scope** | SmartCattle-Frontend repository. |

## 2. Backend responsibility

In scope: REST API, receiving and validating AI events, querying animals and
events, configuration, CORS. Later: PostgreSQL, notifications,
authentication and camera management.

Out of scope: YOLO, OpenCV, model weights, camera capture, frame or video
processing, HTML/CSS/JS.

## 3. Structure

```
app/
  main.py            create_app(): CORS, routers, error handler
  core/config.py     Settings (environment variables)
  routes/            health.py, animals.py, events.py
  schemas/           animal.py, event.py, status.py (Pydantic contracts)
  services/          event_store.py (in-memory store)
tests/
```

`app/api/` and `services/ai_service.py` from the initial proposal are
omitted. They would have no real content today because the backend does not
call the AI service yet. They will be added when that call exists.

## 4. `POST /api/ai/events` contract

Derived from the real output of the prototype's `rules.py`:

| Field | Type | Rule | Justification |
|---|---|---|---|
| `event_type` | enum | `cattle_out_of_zone` | The only event the prototype produces. The enum grows when the AI produces others. |
| `camera_id` | str | 1–64 characters | Event source; needed for future camera management. |
| `detected_object` | str | 1–64 characters | Detected YOLO class (`cow`). |
| `confidence` | float | 0 ≤ x ≤ 1 | Detection confidence. |
| `timestamp` | datetime | timezone-aware | Detection time according to the AI (`rules.py` already uses UTC). |

Undeclared fields are rejected (`extra="forbid"`).

Fields left out for now:
- `zone`: the prototype has a single safe zone; a `zone_id` will be added when there are several.
- `description`: free text the frontend can derive from `event_type`.
- bounding box: vision data with no current consumer in the backend.

The response (201) returns the event with an `id` (UUID) and `received_at`,
both assigned by the backend.

## 5. Persistence

`EventStore` and `CameraStore` define the methods used by routes. Each has an
in-memory implementation (development and tests, data lost on restart) and a
SQL implementation (`SqlEventStore`, `SqlCameraStore`) used when
`DATABASE_URL` is set. See section 8 for the PostgreSQL decisions.

Animals: the prototype does not identify individual animals, so
`GET /api/animals` returns an empty collection and the `Animal` schema is
minimal (`id`, `tag`).

## 6. Security

- CORS: explicit origins from `ALLOWED_ORIGINS`, never `*`. No credentials (no cookies).
- `POST /api/ai/events` accepts an optional shared key `AI_API_KEY` in the `X-API-Key` header. If it is not set, the endpoint is open (development only). In production it **must** be set, because the endpoint is public.
- App creation logs a warning when `AI_API_KEY` is unset; the key value is never logged.
- Validation errors return 422 without echoing submitted input values.
- `/api/status` reports whether the AI service is configured but never exposes its URL.
- Unhandled errors → 500 with a generic message and no stack trace.

## 7. Dependencies

| Package | Reason |
|---|---|
| fastapi | HTTP framework and automatic `/docs` |
| uvicorn | ASGI server |
| pydantic-settings | Typed configuration from environment and `.env` |
| sqlalchemy | ORM and connection pool for PostgreSQL |
| alembic | Versioned schema migrations |
| psycopg[binary] | PostgreSQL driver (psycopg 3) |
| pytest, httpx (dev) | Tests; `TestClient` requires httpx |

Development dependencies live in `requirements-dev.txt` so the deployment
installs only what it needs.

## 8. PostgreSQL and camera status

Decided 2026-10-06 while connecting the real camera pipeline.

**Who writes to the database.** This backend, among other team services. The
AI service runs on a PC on the farm network (the camera is only reachable
there) and pushes data over HTTPS; it never receives database credentials.

**Shared schema (updated 2026-10-07).** The database belongs to the team's
[database repository](https://github.com/walterjojoa/BASEDATOSSmartCattle)
(`cameras`, `zones`, `users`, `animals`, `events`, `alerts`);
`database/schema.sql` is a copy. The backend adapts to it instead of owning
its own tables. It uses:

| Table | How the backend uses it |
|---|---|
| `events` | Persists `POST /api/ai/events`. The API UUID is `ai_event_id` (unique), `received_at` is the receipt time; both columns were added to the shared schema for this backend. |
| `cameras` | The API camera id is `cameras.name`. A first status report or event creates the row, because `events.camera_id` is a foreign key to `cameras.id`. The backend adds the nullable status columns. |

The backend does not write `zones`, `users`, `animals` or `alerts` yet.

Not stored, on purpose: **per-frame detections.** YOLO runs several times per
second; storing every detection would flood the database with data nobody
reads. The AI sends only relevant events (cattle outside the safe zone), with
the bounding box.

**Schema management.** `alembic upgrade head` runs before Uvicorn on Render.
The first revision applies `database/schema.sql` only to an empty database and
never alters an existing one; the second adds the camera status columns. The
application never calls `create_all`. On SQLite, which the tests use, the
first revision creates the `cameras` and `events` columns the backend maps.

**Effective camera status.** The AI reports `online`, `error` or `offline`
through `PUT /api/ai/cameras/{id}/status` every few seconds. `GET /api/cameras`
computes the effective status on read: if the last report is older than
`CAMERA_OFFLINE_AFTER_SECONDS` (default 60), the camera is `offline`. A
stored `online` is therefore never shown once the AI stops confirming it
(AI crashed, PC off, network down). The staleness rule is a pure function
with unit tests.

**Secrets.** `DATABASE_URL` is a `SecretStr` and never appears in
responses or logs. Camera error messages are capped at 300 characters and
rejected if they contain `://`, so a stream URL with embedded credentials
cannot be stored and later shown in the frontend.

**Video.** The backend does not proxy or store video. Live video, when the
camera stream is confirmed, goes from a local media server on the farm PC to
the browser (see the AI repository README); the backend only stores state.
