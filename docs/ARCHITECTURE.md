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

In scope: REST API, receiving and validating AI events, persisting them in
MySQL, querying animals and events, configuration, CORS. Later: notifications,
authentication and camera management.

Out of scope: YOLO, OpenCV, model weights, camera capture, frame or video
processing, HTML/CSS/JS.

## 3. Structure

```
app/
  main.py            create_app(): CORS, routers, error handlers, lifespan
  core/config.py     Settings (environment variables)
  core/security.py   API-key dependencies shared by the routes
  routes/            health.py, animals.py, events.py
  schemas/           animal.py, event.py, status.py (Pydantic contracts)
  services/          event_store.py (EventStore protocol + memory store)
                     mysql_event_store.py (MySQL store + connection pool)
db/schema.sql        database and events table
db/migrations/       one-off changes for databases that already exist
tests/
```

`app/api/` and `services/ai_service.py` from the initial proposal are
omitted. They would have no real content today because the backend does not
call the AI service yet. They will be added when that call exists.

## 4. `POST /api/ai/events` contract

Derived from the real output of the prototype's `rules.py`:

| Field | Type | Rule | Justification |
|---|---|---|---|
| `ai_event_id` | UUID | required, unique | Generated once per detection by the AI and reused on every retry. Makes ingestion idempotent. |
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

### Idempotency

A client cannot distinguish "the request never arrived" from "the reply was
lost", so any correct AI client retries after a timeout. Losing a
`cattle_out_of_zone` alert is worse than sending it twice, so the client will
retry and the server has to absorb it.

`ai_event_id` is the mechanism. The AI generates it once per detection and
reuses it on every retry, so the backend can tell a retry apart from a second
animal. The first call returns 201; a later call with the same value returns 200
with the stored event and creates nothing. The stored event wins: a retry
carrying different field values does not overwrite it.

Deduplicating by payload instead would lose data. Two animals crossing the same
line in the same second, in front of the same camera, produce byte-identical
bodies, and the contract has no bounding box or animal identity to separate
them. The AI is the only party that knows whether a detection is new.

The field was made required rather than optional: an optional key still
duplicates whenever the client omits it, and the contract had no consumer yet,
so this was the cheapest moment to change it.

`UNIQUE KEY uq_events_ai_event_id` enforces it in the database, not in Python:
twenty simultaneous retries resolve to one insert and nineteen reads, which a
read-then-write check in application code could not guarantee.

## 5. Persistence

MySQL 8 was chosen over the PostgreSQL named in the first draft: both fit, and
MySQL is what the team already runs locally. Nothing in the design depends on
the engine.

The routes depend on the `EventStore` protocol in `app/services/event_store.py`,
which declares only `add()` and `list()`. Two implementations satisfy it
structurally, without inheritance:

| Implementation | Selected by | Purpose |
|---|---|---|
| `MySqlEventStore` | `EVENT_STORAGE=mysql` (default) | Persistent storage |
| `InMemoryEventStore` | `EVENT_STORAGE=memory` | Development and the unit test suite |

The switch is not indecision: it keeps the 33 API tests independent of a
running database, so they pass on any machine and in CI.

Decisions inside `MySqlEventStore`:

- **Parameterized statements only.** Values are bound through `%s` placeholders,
  never formatted into the SQL. A `camera_id` of `x'; DROP TABLE events;--` is
  stored as literal text.
- **UTC everywhere.** MySQL `DATETIME` carries no offset. `AIEventCreate`
  normalizes `timestamp` to UTC on input, the store drops the offset before the
  insert and re-attaches UTC on read. Without this, two cameras reporting
  different offsets would corrupt chronological order.
- **A connection pool, not a connection.** The routes are sync functions, so
  FastAPI runs them in a threadpool and several requests execute concurrently.
  One shared connection would corrupt; the pool gives each thread its own. It is
  the database counterpart of the `Lock` in the memory store.
- **Explicit transactions.** `autocommit` is off: `add()` commits on success and
  rolls back on failure, so a failed insert never leaves a partial row.
- **Paging in the SELECT.** `list(limit, offset)` returns one page and `count()`
  reports the total separately, so `total` tells a client how many pages exist
  instead of repeating the page size. `LIMIT` is capped at 1000 by the route, so
  an unbounded table cannot produce an unbounded response. The ordering is
  `received_at DESC, id DESC`: without the tiebreaker, two rows sharing a
  `received_at` could swap between queries and land on two pages or on none.
- **The store is built in the lifespan handler, not at import.** Importing
  `app.main` must never open a connection, otherwise the test suite and any
  tooling that merely imports the app would require a running MySQL.

Offset paging was chosen over keyset paging. It is what `limit` and `offset`
query parameters express directly, and a client that walks pages while events
arrive sees drift: a new event takes position 0 and pushes everything down, so
the last row of page 1 reappears first on page 2. Keyset paging (asking for rows
older than the last one seen) is immune and is the upgrade path, at the cost of
an opaque cursor the client must carry. For an operator reviewing recent alerts,
drift at a page boundary is not worth that complexity yet.

Animals: the prototype does not identify individual animals, so
`GET /api/animals` returns an empty collection and the `Animal` schema is
minimal (`id`, `tag`). No `animals` table exists yet.

## 6. Security

- CORS: explicit origins from `ALLOWED_ORIGINS`, never `*`. No credentials (no cookies).
- **Two independent keys**, both in the `X-API-Key` header, both compared with
  `secrets.compare_digest` and held in `SecretStr`:

  | Key | Authorizes | Holder |
  |---|---|---|
  | `AI_API_KEY` | `POST /api/ai/events` | SmartCattle-AI |
  | `READ_API_KEY` | `GET /api/events`, `GET /api/animals` | The frontend's server |

  Neither key grants the other's endpoints. A key left unset leaves its
  endpoints open, which is for local development only; both **must** be set in
  production. The dependencies live in `app/core/security.py` so no route
  imports another route to reach them.
- `/`, `/health` and `/api/status` stay public. Deployment platforms poll
  `/health` without credentials, and none of the three exposes stored data.
- One key does not replace user authentication. `READ_API_KEY` is server to
  server: a browser cannot hold a secret, so the frontend must proxy reads
  through its own server. Letting page scripts query this API directly needs
  real accounts and tokens, which is out of scope here.
- `/api/status` reports whether the AI service is configured but never exposes its URL. Database credentials are held in `SecretStr` and are unwrapped only when handed to the connector.
- Unhandled errors → 500 with a generic message and no stack trace. The handler is a middleware registered **before** `CORSMiddleware` so it runs inside the CORS layer: Starlette's own handler for `Exception` sits outside it, and the 500 would reach the browser without CORS headers, surfacing as an opaque CORS failure instead of a server error.
- Validation failures (422) report `type`, `loc` and `msg` but never echo the rejected value. Echoing it both reflected attacker-supplied content and crashed on non-finite floats: `NaN` has no JSON representation, so a `confidence` of `NaN` turned a 422 into a 500.

## 7. Dependencies

| Package | Reason |
|---|---|
| fastapi | HTTP framework and automatic `/docs` |
| uvicorn | ASGI server |
| pydantic-settings | Typed configuration from environment and `.env` |
| mysql-connector-python | MySQL driver with a built-in connection pool |
| pytest, httpx (dev) | Tests; `TestClient` requires httpx |

No ORM. Statements are written by hand so the SQL stays visible and reviewable;
the store is small enough that an ORM would add a layer without removing work.

Development dependencies live in `requirements-dev.txt` so the deployment
installs only what it needs.
