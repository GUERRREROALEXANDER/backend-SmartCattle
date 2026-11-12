# Deployment

Three ways to run the backend, from quickest to production.

## 1. Local, everything in containers

Requires Docker Desktop.

```sh
docker compose up --build
```

| What | Where |
| --- | --- |
| API and Swagger | http://localhost:8000/docs |
| Video storage (S3, moto) | http://localhost:9000; kept in memory, so videos are gone after `docker compose down` or a restart |
| PostgreSQL | `localhost:5432`, database, user and password `smartcattle` |

Migrations run when the API container starts. The AI service on the same PC
uses `http://localhost:8000` and the key `local-ai-key`. Every credential in
`docker-compose.yml` is for development only.

Check the video path end to end against the running stack:

```sh
python -m scripts.smoke_video_upload http://localhost:8000 local-ai-key
```

`docker compose down --volumes` deletes the database and the videos.

## 2. Production: Render + Neon + Cloudflare R2

This is the setup `render.yaml` describes, and it fits the free tiers.

1. **Database.** Create a Neon project and copy its connection string
   (`postgresql://…?sslmode=require`).
2. **Video bucket.** In Cloudflare, go to R2, create a bucket (for example
   `smartcattle-videos`), and create an API token with *Object Read & Write*
   on that bucket only. Note the access key id, the secret and the S3 endpoint
   `https://<account-id>.r2.cloudflarestorage.com`.
3. **Bucket CORS.** Browsers upload straight to R2, so R2 must allow it.
   In the bucket's settings, under *CORS policy*:

   ```json
   [
     {
       "AllowedOrigins": ["https://smartcattle-frontend.onrender.com", "http://localhost:5173"],
       "AllowedMethods": ["PUT", "GET", "HEAD"],
       "AllowedHeaders": ["Content-Type"],
       "MaxAgeSeconds": 3600
     }
   ]
   ```

   Without it the upload fails in the browser with a CORS error, even though
   the same URL works from curl.
4. **Render.** Go to **New → Blueprint** and select this repository. Fill in
   `DATABASE_URL` and the four `VIDEO_STORAGE_*` values. Render generates
   `JWT_SECRET` and `AI_API_KEY`; copy `AI_API_KEY` into the AI service's
   configuration on the farm PC.
5. **First account.** The first account registered becomes the owner
   ([AUTHENTICATION.md](AUTHENTICATION.md)). Register it yourself right after
   the first deploy.

Render runs `alembic upgrade head` before every start, so schema changes
deploy with the code.

### Limits to know

- **One process.** The live stream's broadcaster is in memory, so the
  service runs one worker on one instance. More instances need Redis pub/sub
  or PostgreSQL `LISTEN/NOTIFY` behind `app/services/broadcaster.py`
  ([ARCHITECTURE.md](ARCHITECTURE.md) section 11).
- **Free instances sleep.** Render's free plan stops the service after about
  15 minutes without requests, and the next request waits for it to start.
  The AI service's heartbeats keep it awake while cameras are running.
- **The login lockout is per process** and resets on restart.

## 3. Any other container host

The `Dockerfile` runs anywhere that runs containers (Railway, Fly.io, Google
Cloud Run, a VPS). It listens on `$PORT` (default 8000), runs as a non-root
user, has a `HEALTHCHECK` on `/health`, and applies migrations on start
when `DATABASE_URL` is set. Pass the same environment variables as in
`.env.example`, and keep one instance for the reason above.

## 4. Live video from anywhere

Everything here runs on the farm PC, which is the machine that can reach the
cameras. The router needs no open ports. `JWT_SECRET` must be set on the backend.

1. Install [MediaMTX](https://github.com/bluenviron/mediamtx/releases) (tested with v1.21.3).
2. Copy `deploy/mediamtx.yml` next to it, then fill in the backend's address, the
   dashboard's address and one `paths` entry per camera. Each entry is named after
   the camera's id in `GET /api/cameras/saved`, and its `source` holds the camera's
   real address with its password. Keep this copy out of the repository.
3. Start it: `mediamtx mediamtx.yml`.
4. Open a tunnel to it, separate from the AI service's:
   `cloudflared tunnel --url http://127.0.0.1:8888`.
5. Include the tunnel's address as `media_url` in the PC's report:

   ```sh
   curl -X PUT https://YOUR-BACKEND.onrender.com/api/ai/farm-pc \
     -H 'Content-Type: application/json' -H 'X-API-Key: FARM-PC-KEY' \
     -d '{"url":"https://AI-TUNNEL.trycloudflare.com","media_url":"https://VIDEO-TUNNEL.trycloudflare.com"}'
   ```

   A report without `media_url` stops offering video, and so do 90 seconds without
   a report, so it belongs in the same report the AI service already repeats. A
   quick tunnel's address changes when it restarts; the next report corrects it.

Restart MediaMTX after replacing `JWT_SECRET`: it fetches the public key once, and
refuses every ticket until it has the new one.

Upgrading MediaMTX can turn on new listeners. v1.21 added MoQ on every interface,
which is why the template names each protocol it switches off. After an upgrade,
check its startup log lists only `[HLS]`.

## 5. WhatsApp notices

The sender runs apart from this repository, on any machine that stays on. It only
makes outgoing connections, so it needs no tunnel. Messages wait while it is off:
up to an hour for an alert and ten minutes for a verification code.

1. Link a number with `node vincular.mjs`, scanning the QR from WhatsApp.
2. Create its `.env` from `.env.example`, with this backend's URL and `AI_API_KEY`.
3. Start it with `npm start`, and keep it running.

Each person then proves their number through `/api/auth/me/whatsapp/verification`
and `/verify`. Only detections from cameras saved in an account notify anyone.
Use a dedicated number: Baileys is unofficial and Meta can block it.

## Restrictions on the internet

render.yaml turns these on. Per-address limits (`app/guard.py`) answer 429 with Retry-After; `/health` and the AI service with its key are exempt.

- `PUBLIC_REGISTRATION=false`: after the first account, which is the owner, only the owner creates accounts (`POST /api/users`). **Register the owner immediately after the first deploy on an empty database.**
- `REQUIRE_AUTH_FOR_READS=true`: the herd, events, cameras and metrics need a session.
- `API_DOCS_ENABLED=false`: no `/docs` or `/openapi.json`.
- `TRUST_PROXY_HEADERS=true`: only behind Render and Cloudflare, which set the client's address.
- `/ws/live`: 200 sockets, 20 per address.
