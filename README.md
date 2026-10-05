# SmartCattle Backend

## What is SmartCattle?

SmartCattle is a university Software Engineering project aimed at intelligent cattle monitoring using cameras and computer vision. The project is divided into three independent repositories: SmartCattle-Frontend, SmartCattle-Backend (this repository), and SmartCattle-AI.

## Repository responsibility

This repository provides the REST API, validates incoming AI events, stores them in MySQL, exposes animal and event collections, and configures CORS. The animal collection is currently empty.

## What does not belong here?

Camera capture, image or video processing, OpenCV, YOLO, ultralytics, numpy, model weights, and frontend HTML/CSS/JS belong to other repositories. Flask and gunicorn are not used.

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

Python 3.13, FastAPI, Uvicorn, Pydantic Settings, and MySQL 8 through mysql-connector-python. Tests use pytest and httpx through FastAPI's TestClient.

## Structure

```text
app/
  __init__.py
  main.py
  core/config.py
  core/security.py
  routes/health.py
  routes/animals.py
  routes/events.py
  schemas/animal.py
  schemas/event.py
  schemas/status.py
  services/event_store.py
  services/mysql_event_store.py
data_structures/
db/schema.sql
db/migrations/
docs/ARCHITECTURE.md
tests/
  conftest.py
  test_health.py
  test_animals.py
  test_events.py
  test_config.py
  test_mysql_event_store.py
pyproject.toml
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

For deployment, install only `requirements.txt`.

## Database

MySQL 8 must be running. Create the database and the `events` table once:

```sh
mysql -u root -p < db/schema.sql
```

Then set `MYSQL_PASSWORD` in `.env`. The schema is safe to run again: every
statement is guarded by `IF NOT EXISTS`.

An `events` table created before idempotent ingestion lacks `ai_event_id`.
`CREATE TABLE IF NOT EXISTS` leaves an existing table untouched, so apply the
migration once:

```sh
mysql -u root -p smartcattle < db/migrations/0001_add_ai_event_id.sql
```

To work without a database during development, set `EVENT_STORAGE=memory`.
Events then live in RAM and are lost on restart.

## Running

Local development:

```sh
uvicorn app.main:app --reload
```

Cloud deployment, with `PORT` supplied by the platform (POSIX shell):

```sh
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

In PowerShell, use `$env:PORT` for an environment variable. With MySQL storage, several Uvicorn processes may run side by side: they share the same data. With `EVENT_STORAGE=memory`, run a single process, because memory storage is not shared between workers.

## Endpoints

| Method | Path | Key | Response |
| --- | --- | --- | --- |
| GET | `/` | none | Service name, version, and docs path |
| GET | `/health` | none | `{"status":"ok"}` deployment health check |
| GET | `/api/status` | none | Version, AI configuration flag, and storage type |
| GET | `/api/animals` | `READ_API_KEY` | Empty animal collection with total zero |
| GET | `/api/events` | `READ_API_KEY` | One page of stored events, most recently received first, plus the total |
| POST | `/api/ai/events` | `AI_API_KEY` | Validates and stores an AI event; 201 with UUID and UTC receipt time, or 200 when the `ai_event_id` was already stored |

Both keys travel in the `X-API-Key` header and are independent: the ingestion
key does not grant reads and the read key does not grant writes. A missing or
incorrect key returns 401 with `Invalid or missing API key`. A key that is not
configured leaves its endpoints open, which is for local development only.

Invalid event bodies return 422 reporting `type`, `loc` and `msg`; the rejected
value is never echoed back. Unhandled errors return 500 with
`Internal server error` and no trace in the response.

### Paging through events

`GET /api/events` takes `limit` (1–1000, default 100) and `offset` (0 or more).
Values outside those ranges return 422.

```sh
curl -H 'X-API-Key: example-read-key' \
  'http://localhost:8000/api/events?limit=50&offset=100'
```

```json
{ "items": [ ... 50 events ... ], "total": 250, "limit": 50, "offset": 100 }
```

`total` is how many events exist, not how many this page holds, so a client can
tell how many pages remain: here, `250` events in pages of `50` means five
pages. `items` is empty once `offset` passes `total`.

## API documentation

Open `/docs` for Swagger UI or `/redoc` for ReDoc. In `/docs`, use **Authorize** to supply `X-API-Key`. The event schema includes descriptions and a complete example.

## Environment variables

Settings load from `.env` and the environment; environment variables take precedence. The backend starts without any variables defined. Settings are cached; restart after changes.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ALLOWED_ORIGINS` | `http://localhost:3000,http://localhost:5173,http://127.0.0.1:5500` | Comma-separated origins; spaces and empty entries are removed. `*` is rejected. |
| `SMARTCATTLE_AI_URL` | Empty | Optional HTTP(S) URL for future integration; empty means unset. No outgoing AI requests are implemented. |
| `AI_API_KEY` | Empty | Shared key for writing events; empty means unset. Set it in production. |
| `READ_API_KEY` | Empty | Separate key for reading events and animals; empty means unset. Set it in production. Never embed it in browser code. |
| `EVENT_STORAGE` | `mysql` | `mysql` or `memory`. Any other value is rejected at startup. |
| `MYSQL_HOST` | `127.0.0.1` | The literal IPv4 address avoids `localhost` resolving to IPv6 on Windows. |
| `MYSQL_PORT` | `3306` | |
| `MYSQL_USER` | `root` | |
| `MYSQL_PASSWORD` | Empty | Stored as a secret: it never appears in logs or responses. |
| `MYSQL_DATABASE` | `smartcattle` | |
| `MYSQL_POOL_SIZE` | `5` | Connections kept open for the request threadpool. |

CORS allows GET and POST, accepts Content-Type and X-API-Key, and does not allow credentials. Status responses never expose the AI URL, either key, or any database credential.

`READ_API_KEY` is a server-side key. A browser cannot hold a secret, so the
frontend must request events through its own server rather than calling this API
from page scripts. Granting a browser direct access requires user
authentication, which is not implemented.

## Tests

```sh
pytest -q
```

Tests cover endpoints, event validation, shared-key protection, CORS, configuration, error handling, per-app isolation, the 1000-event memory limit, and the MySQL store. The MySQL tests build a throwaway `smartcattle_test` database from `db/schema.sql` and drop it afterwards; they skip themselves when MySQL is unreachable, so the rest of the suite runs without a database.

## Future SmartCattle-AI integration

The AI service can send this event to the existing ingestion endpoint:

```json
{
  "ai_event_id": "0f8bc0f2-8e4d-4c6e-9a3b-5d7c1e2f4a60",
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
  -H 'X-API-Key: example-ingest-key' \
  -d '{"ai_event_id":"0f8bc0f2-8e4d-4c6e-9a3b-5d7c1e2f4a60","event_type":"cattle_out_of_zone","camera_id":"camera-01","detected_object":"cow","confidence":0.95,"timestamp":"2026-10-03T15:30:00Z"}'
```

**`ai_event_id` is required, and the AI service owns it.** Generate it once per
detection and reuse the same value on every retry of that detection. The
endpoint is then idempotent: the first call returns 201, and any later call with
the same `ai_event_id` returns 200 with the stored event and creates nothing.
The stored event always wins, so a retry cannot overwrite it. Two detections in
the same second are two different `ai_event_id` values and are stored as two
events.

The backend assigns `id` and `received_at`. Detection timestamps must include a
timezone and are converted to UTC. Camera and object names are trimmed and must
contain 1–64 characters. Confidence must be between 0 and 1; `NaN` and
`Infinity` are rejected. Extra fields and unknown event types are rejected.

## Storage

`MySqlEventStore` persists events in the `events` table of the `smartcattle`
database. `InMemoryEventStore` remains available for development through
`EVENT_STORAGE=memory`. Both satisfy the `EventStore` protocol in
`app/services/event_store.py`, so the routes depend only on `add()` and
`list()` and never on the backend in use.

All instants are stored as UTC. MySQL `DATETIME` carries no offset, so
`AIEventCreate` normalizes `timestamp` to UTC on input and the store
re-attaches UTC on output. Incoming offsets other than UTC are accepted and
converted; the same instant always comes back the same way.

`GET /api/events` returns one page, newest first, ordered by `received_at` with
`id` breaking ties so a row cannot land on two pages or on none. Rows outside
the requested page stay in the table; `limit` bounds the response, it never
deletes anything.

Paging is offset-based, which drifts when events arrive mid-walk: a new event
takes position 0 and pushes everything down, so the row that was last on page 1
reappears first on page 2. Keyset paging (asking for rows older than the last
one seen) avoids this and is the upgrade path if it starts to matter.

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

Events persist in MySQL and survive restarts; several processes share the same
data. `GET /api/events` returns the latest 1000 rows and has no pagination yet.

Reads and writes require separate keys, ingestion is idempotent through
`ai_event_id`, and `GET /api/events` is paged.

Known gaps:

- **No user authentication.** `READ_API_KEY` is a server-side key, so the
  frontend needs its own server to reach the read endpoints. Letting a browser
  query the API directly requires real user accounts and tokens.
- **Paging is offset-based and drifts** when events arrive while a client walks
  the pages. Keyset paging is the fix if it matters.
- **No filtering.** Events cannot be narrowed by camera or date range, so a
  client reading one camera's history has to page through all of them. The
  `camera_id` index is already in place for it.
- No notification system. Individual animal identification and outgoing AI calls
  are not implemented. `GET /api/animals` returns an empty collection: no
  `animals` table exists.
