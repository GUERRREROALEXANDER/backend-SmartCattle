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

## Restrictions on the internet

render.yaml turns these on. Per-address limits (`app/guard.py`) answer 429 with Retry-After; `/health` and the AI service with its key are exempt.

- `PUBLIC_REGISTRATION=false`: after the first account, which is the owner, only the owner creates accounts (`POST /api/users`). **Register the owner immediately after the first deploy on an empty database.**
- `REQUIRE_AUTH_FOR_READS=true`: the herd, events, cameras and metrics need a session.
- `API_DOCS_ENABLED=false`: no `/docs` or `/openapi.json`.
- `TRUST_PROXY_HEADERS=true`: only behind Render and Cloudflare, which set the client's address.
- `/ws/live`: 200 sockets, 20 per address.
