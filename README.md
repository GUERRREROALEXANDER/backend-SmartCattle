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
  models.py                shared cameras/events/users/animals/zones and backend-owned video analysis tables
  dependencies.py          every provider the routes resolve out of app.state
  middleware.py            request id, access log
  core/config.py
  core/security.py         password hashing and access tokens
  routes/health.py
  routes/auth.py
  routes/animals.py
  routes/zones.py
  routes/events.py
  routes/cameras.py
  routes/videos.py
  routes/metrics.py
  schemas/auth.py
  schemas/animal.py
  schemas/zone.py
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
render.yaml                Render Blueprint (backend + frontend static site; PostgreSQL on Neon)
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

Without `DATABASE_URL` everything is kept in memory and lost on restart. With it, events, cameras and video analyses are stored in PostgreSQL. The shared schema is owned by the [database repository](https://github.com/walterjojoa/BASEDATOSSmartCattle); `database/schema.sql` and `database/migrations/` are copies of it (commit `6d97845`). The application never creates tables at startup. `alembic upgrade head`:

1. `0001_shared_schema` applies `database/schema.sql` only if the database has no `cameras` table yet. An existing shared database is left as it is.
2. `0002_camera_status` adds the nullable camera status columns the backend needs to `cameras`: `reported_status`, `last_report_at`, `last_online_at`, `last_error`, `frame_width`, `frame_height`, `fps` and `stream_url`.
3. `0003`–`0010` create the backend-owned tables: `video_analyses`, `video_analysis_alerts`, `farms`, `lots`, `security_alerts`, `camera_occupancy` and `video_uploads`, and make sure `users`, `animals` and `zones` exist.
4. `0011` and `0012` complete the shared schema: `schema.sql` describes its tables, but its indexes, views (`v_api_events`, `v_camera_health`, `v_active_herd`), comments, the `prune_events` retention function and later columns arrived as the database repository's numbered migrations. `0012` applies them all; each is safe to run twice, so on a database that already has them it changes nothing.
5. `0013` indexes every foreign key of the backend tables, the database repository's rule.
6. `0014` adds `length_meters` and `width_meters` to `lots`.
7. `0015` adds eight catalog tables (`event_types`, `severity_levels`, `alert_statuses`, `camera_statuses`, `lot_kinds`, `user_roles`, `animal_statuses`, `video_upload_statuses`): every coded value with an English and a Spanish label, served by `GET /api/catalogs`. A test fails if a catalog and the enum the API validates against disagree.
8. `0016` adds the history tables: `occupancy_samples` (the head count per camera, one row per minute), `camera_status_changes` (one row each time a camera's reported status changes) and `audit_log` (who did what).
9. `0017` adds seven report views for anyone querying PostgreSQL directly: `v_alerts_daily`, `v_alert_response_times`, `v_events_daily`, `v_occupancy_hourly`, `v_camera_status_periods`, `v_farm_overview` and `v_video_processing`. Days follow the session time zone: run `SET TIME ZONE 'America/Bogota';` first for Colombian days.

A fresh PostgreSQL database ends with 24 tables, 10 views and 71 indexes. CI checks this on every push: the persistence tests run against PostgreSQL there, not only SQLite, and every view is queried.

**History growth.** `occupancy_samples` grows by at most 1 440 rows per camera per day, roughly 60 MB per camera per year with its indexes. Nothing prunes it yet; on Neon's free 0.5 GB plan, delete old rows once a year (`DELETE FROM occupancy_samples WHERE minute < now() - interval '1 year'`).

Alembic also creates its own `alembic_version` table. `alembic downgrade base` removes the backend-owned tables and the status columns; the shared tables and their rows stay.

When the database repository adds a migration, copy it into `database/migrations/` and update the commit above.

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

Endpoints marked **owner** need an account with that role; the ones marked
signed in need any account. The `X-API-Key` ones are called by the AI service.
See [docs/AUTHENTICATION.md](docs/AUTHENTICATION.md) and
[docs/ROLES.md](docs/ROLES.md).

| Method | Path | Response |
| --- | --- | --- |
| GET | `/` | Service name, version, and docs path |
| GET | `/health` | `{"status":"ok"}` deployment health check |
| GET | `/api/status` | Version, AI configuration flag, storage type (`memory` or `postgresql`), and whether authentication is configured |
| GET | `/api/cameras/{camera_id}` | One camera's effective status, or a JSON 404 if it has never reported |
| POST | `/api/auth/register` | Creates an account and returns a bearer token; the first account is the owner |
| POST | `/api/auth/login` | Returns a bearer token for an existing account |
| GET | `/api/auth/me` | The signed-in account |
| PUT | `/api/auth/me/password` | Changes the caller's password; needs the current one (400 if wrong, throttled like login) |
| GET | `/api/users` | Accounts (**owner**); filters `role`, `is_active`; paging `limit` (1–200) and `offset` |
| POST | `/api/users` | Creates an account with a role and initial password (**owner**); 409 if the email is taken |
| GET | `/api/users/{id}` | One account (**owner**) |
| PATCH | `/api/users/{id}` | Changes name, role or active flag (**owner**); 409 if it would leave no active owner |
| GET | `/api/animals` | The herd, optionally filtered with `?status=active\|inactive\|lost` |
| GET | `/api/animals/{tag}` | One animal, or a JSON 404 |
| POST | `/api/animals` | Registers an animal (**owner**); 409 if the ear tag is taken |
| PATCH | `/api/animals/{tag}` | Partial update (**owner**); omitted fields keep their values |
| POST | `/api/animals/{tag}/deactivate` | Withdraws an animal from the active herd (**owner**). There is no DELETE |
| PUT | `/api/animals/{tag}/detection` | AI service records a sighting (`X-API-Key`) |
| GET | `/api/animals/{tag}/exists` | Whether a tag is taken, for the registration form (signed in) |
| GET | `/api/zones` | Configured safe zones, optionally `?camera_id=` (signed in) |
| GET | `/api/zones/{id}` | One zone, or a JSON 404 (signed in) |
| POST | `/api/zones/camera/{camera_id}` | Configures a safe zone (**owner**) |
| PATCH | `/api/zones/{id}` | Partial update (**owner**); the geometry is replaced as a whole |
| DELETE | `/api/zones/{id}` | Removes a zone (**owner**) |
| GET | `/api/alerts` | Security alerts, newest first (signed in). Filters `status`, `severity`, `camera_id`; paging `limit` (1–200) and `offset` |
| GET | `/api/alerts/{id}` | One alert, or a JSON 404 (signed in) |
| POST | `/api/alerts/{id}/acknowledge` | Open → acknowledged, recording who and when (signed in); 409 otherwise |
| POST | `/api/alerts/{id}/resolve` | Open or acknowledged → resolved (signed in); 409 if already resolved |
| WS | `/ws/live` | Live dashboard stream; see [Live stream](#live-stream) |
| GET | `/api/farms` | Registered farms with their `lot_count` (signed in) |
| POST | `/api/farms` | Registers a farm owned by the caller (**owner**); 409 if the caller already has one with that name |
| GET | `/api/farms/{id}` | One farm, or a JSON 404 (signed in) |
| PATCH | `/api/farms/{id}` | Partial update (**the farm's owner**); omitted fields keep their value, `null` clears an optional one |
| DELETE | `/api/farms/{id}` | Deletes the farm (**the farm's owner**); 409 while it still has lots |
| GET | `/api/farms/{id}/lots` | The farm's lots, optionally `?active=true\|false` (signed in) |
| POST | `/api/farms/{id}/lots` | Adds a paddock, stable, pen or pasture (**the farm's owner**); only `name` is required, `kind` defaults to `paddock`. `length_meters` and `width_meters` go together and give the area; 409 on a duplicate name in that farm |
| GET | `/api/lots/{id}` | One lot, or a JSON 404 (signed in) |
| PATCH | `/api/lots/{id}` | Partial update (**the farm's owner**). New length and width bring their area; a new area alone clears them |
| DELETE | `/api/lots/{id}` | Removes a lot (**the farm's owner**) |
| GET | `/api/events` | Event feed, newest received first. Filters: `camera_id`, `level`, `event_type`; paging: `limit` (1–200, default 50) and `offset` |
| POST | `/api/ai/events` | Validates and stores an AI event; returns 201 with UUID and UTC receipt time |
| GET | `/api/cameras` | Cameras that reported status, sorted by id, with their effective status |
| PUT | `/api/ai/cameras/{camera_id}/status` | AI heartbeat: stores the camera status; returns the camera |
| POST | `/api/videos/analyses` | Stores a simulated video analysis (**signed in**); returns 201 with the analysis and alerts |
| GET | `/api/videos/analyses?limit=20` | Latest analyses first; `limit` defaults to 20 and accepts 1–100 |
| GET | `/api/videos/analyses/{id}` | One analysis with its alerts, or a JSON 404 |
| GET | `/api/metrics/summary?since=<ISO>` | Event rates and latest-analysis animal estimates; `since` is optional and timezone-aware |
| GET | `/api/metrics/live` | Live counters from the stores: cameras online, cattle inside/outside from the AI's current counts, open alerts, events in the last hour, `system_status` (signed in) |
| GET | `/api/ai/cameras/{camera_id}/zones` | The camera's active safe zones for the AI service (`X-API-Key`), which polls it so a zone drawn in the dashboard applies without a restart |
| PUT | `/api/ai/cameras/{camera_id}/source` | Where the camera is reached, with no credentials, and whether it is still in use (`X-API-Key`); 204. Stored in `cameras.source` and `cameras.active`; not returned by `GET /api/cameras` |
| PUT | `/api/ai/cameras/{camera_id}/occupancy` | AI head count for one camera plus optional normalised boxes (`X-API-Key`); 204. Counts are stored, boxes are only pushed live |
| POST | `/api/videos/uploads` | Registers a video (≤ 1 GiB; mp4, mov, mkv, webm) and returns a presigned URL to upload it to (**signed in**); 413 too large, 415 wrong type |
| POST | `/api/videos/uploads/{id}/complete` | Confirms the file is in the bucket and queues it (uploader or owner) |
| GET | `/api/videos/uploads?status=&limit=&offset=` | Uploads newest first, with processing progress (**signed in**) |
| GET | `/api/videos/uploads/{id}` | One upload (**signed in**) |
| POST | `/api/ai/video-jobs/claim` | The AI worker takes the oldest queued video and gets a download URL (`X-API-Key`); 204 when there is nothing to do |
| PUT | `/api/ai/video-jobs/{id}/progress` | Frames processed plus that frame's boxes; renews the job lease (`X-API-Key`) |
| POST | `/api/ai/video-jobs/{id}/complete` | Delivers the analysis, same body as `POST /api/videos/analyses` (`X-API-Key`) |
| POST | `/api/ai/video-jobs/{id}/fail` | Gives up on a video the worker cannot process (`X-API-Key`) |
| GET | `/api/catalogs` | Every coded value with English and Spanish labels, for selects and badges (public) |
| GET | `/api/reports/occupancy?camera_id=&start=&end=` | Head count per camera and minute (**signed in**) |
| GET | `/api/reports/camera-status?camera_id=&start=&end=` | Each change in a camera's reported status (**signed in**) |
| GET | `/api/reports/alerts?start=&end=&tz=` | Alerts per day by severity and status, average minutes to acknowledge and resolve (**signed in**) |
| GET | `/api/reports/events?start=&end=&tz=` | Detections per day and camera, by severity (**signed in**) |
| PUT | `/api/ai/cameras/{camera_id}/detection-log` | AI counts by posture for one camera (`X-API-Key` or the farm PC's key); 204. Keeps the last report of each minute |
| GET | `/api/reports/detections?camera_id=&start=&end=` | The logged counts per camera and minute (**signed in**) |
| GET | `/api/reports/daily?day=&tz=` | One day of activity, health notices and alerts for the caller's account; `day` defaults to today in `tz` (**signed in**) |
| GET | `/api/audit-log?actor_id=&action=&target_type=&target_id=&start=&end=&limit=&offset=` | Who did what, newest first (**owner**) |

### Reports and history

The report endpoints default to the last 7 days and accept at most 92; `start` is inclusive, `end` exclusive, and a time without a zone is read as UTC. Days are cut in `tz`, an IANA name that defaults to `America/Bogota`, so an alert at 22:00 in Colombia counts on that day and not on the next one in UTC. An empty or wider window, or an unknown zone, is a 422.

`GET /api/reports/daily` takes one calendar day instead of a window; a future day is a 422. Posture figures are head-minutes: one animal seen for one minute counts 1. `complete` stays false until the day ends. A camera needs 120 observed minutes before it can raise a health notice: `prolonged_lying` above a lying share of 0.8, `little_lying` below 0.2. Treat those two values as starting points to calibrate with a vet. A notice suggests a visit and diagnoses nothing.

If the database cannot be reached, any endpoint answers 503 with `{"detail": ..., "request_id": ...}` and a `Retry-After` header, after waiting at most 10 seconds for a connection.

The audit log records sign-ins (failed ones with the address typed), account changes, alert handling, and farm, lot, zone, animal and video-upload changes. It stores which fields changed, never passwords. It is written after the action, so a failure to write it is logged and does not turn a completed action into an error.

### Video uploads

The file never passes through this API. The browser uploads it straight to
the bucket (Cloudflare R2 in production, an S3 stand-in in docker-compose), which keeps a 1 GB
upload off Render's request limits:

```js
const ticket = await api.post("/videos/uploads", {
  file_name: file.name, size_bytes: file.size, content_type: file.type, lot_id,
});
await fetch(ticket.upload_url, { method: ticket.upload_method, headers: ticket.upload_headers, body: file });
await api.post(`/videos/uploads/${ticket.upload.id}/complete`);
```

`fetch` reports no upload progress; use `XMLHttpRequest.upload.onprogress` for
a progress bar. After `/complete` the status goes `queued` → `processing` →
`completed` (with `analysis_id`) or `failed` (with `error`), and each change
arrives as a `video.updated` message. The bucket's CORS rules must allow `PUT`
from the frontend's origin.

The AI worker polls `POST /api/ai/video-jobs/claim`, downloads from
`download_url`, and sends progress at least every 10 minutes. A worker that
goes quiet for longer loses the job to the next one; after 3 attempts the video
is marked failed. Without `VIDEO_STORAGE_*` configured, these endpoints return
503.

### Video analyses and metrics

For a frontend `API_BASE` of `http://localhost:8000/api`, `${API_BASE}/videos/analyses` resolves to `http://localhost:8000/api/videos/analyses`. If the configured base is only `http://localhost:8000`, append `/api/videos/analyses` instead. The metrics route follows the same rule: `/api/metrics/summary`.

`POST /api/videos/analyses` accepts `video_name`, `duration_sec`, `frames_analyzed`, `max_cattle`, `max_people`, `alerts`, `avg_confidence` and `outside_ratio`. Counts, track ids and seconds must be non-negative; confidence and ratio must be finite numbers from 0 to 1. Names are trimmed and limited to 200 characters, and at most 500 alerts are accepted. Each alert has `track_id`, `cls` (`cattle` or `person`) and `time_sec`. Unknown fields are rejected. The response adds a UUID `id`, an integer alert `id`, `alert_count` and `created_at`.

Every cattle alert also creates a `cattle_out_of_zone` event with `camera_id: "video-upload"`, `detected_object: "cow"` and the analysis average confidence. The existing schema permits that camera name, so no shared column is changed; a camera row is created if needed but does not appear in `GET /api/cameras` without a status report. The shared `events.source` keeps its default. Person alerts do not create events. Because `time_sec` has no recording wall-clock reference, all generated events use the analysis `created_at` as their detection timestamp; the relative time remains in `video_analysis_alerts`.

`GET /api/metrics/summary` returns `animals_inside`, `animals_outside`, `alerts_today`, `alerts_since` and `detections_per_minute`. Its assumptions are:

- Animal counts are null until an analysis exists. Otherwise, distinct cattle `track_id` values in the latest analysis estimate the outside count (capped at `max_cattle`), and the remainder is inside. They are not live individual-animal locations.
- Alert counts use API-ingested event detection timestamps in UTC. `alerts_today` starts at 00:00 UTC; `alerts_since` is null without `since` and otherwise counts timestamps from `since` through the current time.
- `detections_per_minute` is the number of stored API events in the trailing 60 minutes divided by 60. It is an event rate, not a per-frame YOLO detection rate. Future-dated events are excluded.

### Live stream

`/ws/live` pushes what happens on the farm to every open dashboard, so the
frontend does not poll. Browsers cannot set headers on a WebSocket, and a token
in the URL would end up in access logs, so the token goes in the first message:

```js
const socket = new WebSocket("wss://smartcattle-backend.onrender.com/ws/live");
socket.onopen = () => socket.send(JSON.stringify({ type: "auth", token: accessToken }));
socket.onmessage = ({ data }) => {
  const { type, data: payload, sent_at } = JSON.parse(data);
};
```

| Message `type` | `data` | Sent when |
| --- | --- | --- |
| `hello` | `{user_id, role}` | Authentication succeeded |
| `event.created` | the `Event` | `POST /api/ai/events` stored a detection |
| `alert.created` | the `Alert` | That detection raised a security alert |
| `alert.updated` | the `Alert` | Someone acknowledged or resolved an alert |
| `camera.status` | the `Camera` | The AI service reported a camera's state |
| `camera.removed` | `{id}` | A camera was removed from the dashboard |
| `occupancy.updated` | `{camera_id, cattle_inside, cattle_outside, observed_at, received_at}` | The AI reported a newer head count |
| `detections` | `{camera_id, observed_at, detections: [{track_id, label, confidence, bbox, inside_zone}]}` | That count carried boxes; `bbox` is `[x1, y1, x2, y2]` from 0 to 1, so the video overlay and the 3D view scale it to their own size and can flash `inside_zone: false` animals |
| `video.updated` | the `VideoUpload` | An upload was queued, claimed, completed or failed |
| `video.progress` | `{upload_id, frames_processed, frames_total, fps, video_time_sec, detections}` | The AI worker processed more of a video; `video_time_sec` places the boxes on the playback |
| `pong` | `null` | The client sent `{"type": "ping"}` |

Close codes: `4401` when the first message is missing (5 s), malformed or
carries an invalid token, or the account is deactivated; `4503` when
`JWT_SECRET` is not configured. Reconnect with a fresh token after `4401`.

A slow client loses its oldest queued messages (256 per connection) instead of
slowing ingestion. Re-read `GET /api/alerts` after reconnecting to catch up.

### Security alerts

Every detection posted to `POST /api/ai/events` raises one open alert, with
the event's severity. Alerts move `open → acknowledged → resolved`; an open
alert can also be resolved directly, as when a false alarm is dismissed.
Workers can handle alerts: that is the everyday job, not farm configuration.

Events created by `POST /api/videos/analyses` raise no alert. They describe
recorded footage, and an alarm for something that already happened would
train people to ignore alarms.

### Event feed

`GET /api/events` pages and filters. `total` is the number of events matching
the filters, **not** the number returned, so a client can tell whether another
page exists without asking twice:

```
GET /api/events?camera_id=camera-01&level=high&limit=20&offset=0
```

`level` is `low`, `medium` or `high`, derived by the backend from the
detection confidence (at or above 0.9 is high, below 0.6 is low). It is **not
accepted from the caller**: the AI service reports what it saw and how sure it
is, and how urgent that makes it is a decision about this farm, so keeping it
on one side of the boundary is what guarantees every event is ranked by the
same rule.

An offset past the end returns an empty `items` with the unchanged `total`.
The store keeps at most 1000 events, so that is the ceiling on what any
sequence of pages can reach.

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
| `/api/status` | `{"status":"ok","version":"<version>","ai_service":{"configured":false},"storage":"memory","auth_enabled":false}` (`configured` and `auth_enabled` may be `true`) |
| `/api/animals` | `{"items":[{"tag":"A-441","name":"Lucera","status":"active","camera_id":"camera-01","last_detection":null,"created_at":"<UTC>","updated_at":"<UTC>"}],"total":1}` (empty: `{"items":[],"total":0}`) |
| `/api/events` | `{"items":[{"event_type":"cattle_out_of_zone","camera_id":"camera-01","detected_object":"cow","confidence":0.95,"timestamp":"2026-10-03T15:30:00Z","bbox":[120.0,80.5,340.0,300.0],"id":"<uuid>","received_at":"2026-10-03T15:30:01Z","level":"high"}],"total":1}` (empty: `{"items":[],"total":0}`; `bbox` may be `null`) |
| `/api/cameras` | `{"items":[{"id":"camera-01","reported_status":"online","status":"online","last_report_at":"<UTC>","last_online_at":"<UTC>","last_error":null,"frame_width":1280,"frame_height":720,"fps":14.8}],"total":1}` (no camera yet: `{"items":[],"total":0}`) |
| `/api/videos/analyses` | `{"items":[{"id":"<uuid>","video_name":"pasture-test.mp4","duration_sec":12.5,"frames_analyzed":125,"max_cattle":4,"max_people":1,"alerts":[{"track_id":7,"cls":"cattle","time_sec":3.2,"id":1}],"avg_confidence":0.91,"outside_ratio":0.25,"alert_count":1,"created_at":"<UTC>"}],"total":1}` |
| `/api/metrics/summary` | `{"animals_inside":3,"animals_outside":1,"alerts_today":2,"alerts_since":null,"detections_per_minute":0.03333333333333333}` (`since` adds an integer `alerts_since`; animal counts may be null) |

Validation errors (422) use `{"detail":[{"type":"<error type>","loc":["body","<field>"],"msg":"<message>"}]}`. Error objects may also contain safe `ctx` details, but never the submitted `input`. Errors 401, 404, and 500 use a string in `detail`. Timestamps are ISO 8601 with a timezone. `/api/events` is newest first and retains at most 1000 events. Confidence must be a finite number between 0 and 1 inclusive.

## API documentation

Open `/docs` for Swagger UI or `/redoc` for ReDoc. **Authorize** offers both
schemes, and which one an endpoint needs is not interchangeable:

- `HTTPBearer` — paste the `access_token` from `/api/auth/login`. For the herd,
  the zones and saving a video analysis.
- `APIKeyHeader` — `X-API-Key`, for the endpoints the AI service calls.

The event schema includes descriptions and a complete example.

## Environment variables

Settings load from `.env` and the environment; environment variables take precedence. The backend starts without any variables defined. Settings are cached; restart after changes.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ALLOWED_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173,http://localhost:3000,http://127.0.0.1:5500` | Comma-separated HTTP(S) origins; spaces, trailing slashes, and empty entries are removed. `*` is rejected. |
| `SMARTCATTLE_AI_URL` | Empty | Optional HTTP(S) URL for future integration; empty means unset. No outgoing AI requests are implemented. |
| `AI_API_KEY` | Empty | Optional shared key for event and camera status ingestion; empty means unset. Set it in production. |
| `DATABASE_URL` | Empty | PostgreSQL URL. Empty means in-memory storage. Never exposed by any endpoint. |
| `CAMERA_OFFLINE_AFTER_SECONDS` | `60` | A camera without a report for longer than this (10–3600) is shown as `offline`. |
| `JWT_SECRET` | Empty | Signs the access tokens. **Empty means authentication is off:** `/api/auth/*` answers 503 and nothing is protected. Rejected below 32 bytes; changing it signs everyone out. |
| `ACCESS_TOKEN_MINUTES` | `60` | Access token lifetime (5–1440). There is no refresh token, so this is the whole session. |
| `MAX_LOGIN_ATTEMPTS` | `5` | Failed logins per address (3–50) before it is refused. Counted per process; a restart clears it. |
| `LOGIN_LOCKOUT_SECONDS` | `300` | How long a locked address is refused (30–3600). |

When `AI_API_KEY` is unset, ingestion is open for development. CORS allows GET, POST and PUT, accepts Content-Type and X-API-Key, and does not allow credentials. Status responses never expose the AI URL, key or database URL.

For production, set `ALLOWED_ORIGINS=https://your-frontend-domain.example`.

## Deploying on Render

Step-by-step setup for Render, Neon and Cloudflare R2, the local Docker stack and other container hosts is in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). To run everything locally with PostgreSQL and video storage: `docker compose up --build`.

`render.yaml` is a Render Blueprint that creates the web service `smartcattle-backend`; PostgreSQL is hosted on [Neon](https://neon.com). In Render: **New → Blueprint**, select this repository, paste the Neon connection string (`postgresql://...?sslmode=require`) into `DATABASE_URL`, then fill `ALLOWED_ORIGINS` with the frontend URL. Migrations create the tables, so do not also run the database repository's `schema.sql` on the same database. `DATABASE_URL` is entered by hand and `AI_API_KEY` is generated; copy the key into the AI service's
`SMARTCATTLE_API_KEY` on the local PC. `JWT_SECRET` is generated too, so the
deployment never starts with authentication off -- but note that replacing it
by hand signs every user out, because tokens are verified against it and there
is no session table. Each deploy runs `alembic upgrade head` before starting
Uvicorn.

The first account registered on a fresh deployment becomes the owner; see
[docs/AUTHENTICATION.md](docs/AUTHENTICATION.md).

## Tests

```sh
ruff check .   # lint
mypy           # strict type check on app/ and data_structures/
pytest -q      # tests
```

CI runs the same three on every push, plus an Alembic round trip against an
empty database. See [CONTRIBUTING.md](CONTRIBUTING.md).

Tests cover endpoints, authentication and roles, password hashing and token
handling, the login throttle, the herd and safe zone registries, event
filtering and paging, event/camera/video validation, video-to-event mapping,
summary metric windows, shared-key protection, the published OpenAPI contract,
CORS, request correlation, configuration, error handling, per-app isolation,
the 1000-event storage limit, the data structure examples, and Alembic
migrations plus SQL persistence on a temporary SQLite database.

The suite runs bcrypt at its minimum cost through an autouse fixture, because
the work factor is the point in production and buys nothing in a test.
`tests/test_security.py` pins the production value by reading the source, so
lowering it here cannot hide a change to the real one. To also run them against a real PostgreSQL database (use an empty test database, never the shared one: the first run applies `database/schema.sql`, and the test deletes the rows it creates):

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

The backend assigns `id`, `received_at` and `level`. Detection timestamps must include a timezone. Camera and object names are trimmed and must contain 1–64 characters. Confidence must be a finite number between 0 and 1 inclusive. The optional `bbox` is `[x1, y1, x2, y2]` in frame pixels: four finite numbers ≥ 0 with `x1 < x2` and `y1 < y2`. Extra fields and unknown event types are rejected.

**Do not send `level`.** The request is rejected if you do. The AI service
reports what it saw and how sure it is; the backend derives the severity from
the confidence, so every event on the farm is ranked by one rule rather than
two.

### Reporting an individual animal

Once the vision service can identify an individual and not only the class
`cow`, it records the sighting against the ear tag:

```sh
curl -X PUT http://localhost:8000/api/animals/A-441/detection   -H 'Content-Type: application/json'   -H 'X-API-Key: example-shared-key'   -d '{"camera_id":"camera-01","detected_at":"2026-10-29T15:30:00Z"}'
```

Same `X-API-Key` as the events endpoint, because the caller is the same
service. It updates `animals.last_detection` and, when `camera_id` is given,
where the animal was last seen. An unknown tag is a 404: the backend does not
invent an animal from a sighting, since the herd is registered by the farm
staff.

This endpoint is not used yet. It exists because the column it writes has been
in the shared schema from the start, and the contract belongs next to the rest
of the animal routes rather than being invented later under time pressure.

### Reading the safe zone

The zone the AI service compares detections against now lives in the database
and is served by `GET /api/zones?camera_id=camera-01`. It needs a user token,
not the AI key -- which is a gap: the vision service has no account. Until that
is resolved it keeps using its local configuration, and the endpoint exists for
the frontend's zone editor.

Coordinates are normalised from 0 to 1, not pixels, so the same zone holds
whatever resolution the service processes at. `outline` is an optional polygon;
when it is absent the rectangle applies.

## Data structure examples

Independent academic examples demonstrate how data structures could apply to SmartCattle using only the Python standard library. They are not used by the API; see [the examples guide](data_structures/README.md) for details.

| Structure | Principle | SmartCattle application |
|---|---|---|
| List/Array | Indexed access and traversal | Currently detected animals |
| Stack | LIFO | Recent event history |
| Queue | FIFO | Pending events |
| Singly linked list | Nodes with one link | Event sequence |
| Doubly linked list | Nodes with two links (`prev`, `next`) | Event history reviewed in both directions |
| Doubly circular list | A ring with two links | Camera rotation in the live view |

The last two exist because a specific screen needs what one link cannot give:
the event review moves backwards, and the camera carousel has no end. Each
module's docstring states what it buys and what it costs -- a ring, notably,
has no traversal that terminates by itself.

Run from the repository root:

```sh
python data_structures/array_smartcattle.py
python data_structures/stack_smartcattle.py
python data_structures/queue_smartcattle.py
python data_structures/linked_list_smartcattle.py
python data_structures/doubly_linked_list_smartcattle.py
python data_structures/circular_list_smartcattle.py
```

## Current state

`render.yaml` is a Render Blueprint that creates the web service `smartcattle-backend` and the static site `smartcattle-frontend`, built from the frontend repository; PostgreSQL is hosted on [Neon](https://neon.com). In Render: **New → Blueprint**, select this repository, paste the Neon connection string (`postgresql://...?sslmode=require`) into `DATABASE_URL` and apply. Migrations create the tables, so do not also run the database repository's `schema.sql` on the same database. `ALLOWED_ORIGINS` and `VITE_API_BASE_URL` assume the default `*.onrender.com` names; if Render adds a suffix because a name is taken, update both. `AI_API_KEY` is generated; copy the key into the AI service's
`SMARTCATTLE_API_KEY` on the local PC. `JWT_SECRET` is generated too, so the
deployment never starts with authentication off -- but note that replacing it
by hand signs every user out, because tokens are verified against it and there
is no session table. Each deploy runs `alembic upgrade head` before starting
Uvicorn.

The first account registered on a fresh deployment becomes the owner; see
[docs/AUTHENTICATION.md](docs/AUTHENTICATION.md).
