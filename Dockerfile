# syntax=docker/dockerfile:1

# --- Build: install dependencies into a virtualenv that is copied whole. ---
FROM python:3.13-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

# Requirements first, so code changes do not reinstall every dependency.
COPY requirements.txt .
RUN pip install -r requirements.txt

# --- Runtime: no compilers, no pip cache, not root. ---
FROM python:3.13-slim

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

RUN useradd --system --uid 10001 --no-create-home smartcattle

WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

USER smartcattle
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4)"]

# Migrations run before the API starts, as on Render; without DATABASE_URL the
# API keeps its in-memory stores and there is nothing to migrate.
#
# One Uvicorn worker on purpose: the live-stream broadcaster is per process,
# so a second worker would show each dashboard only half of the messages
# (docs/ARCHITECTURE.md section 11). Scale with instances behind a shared bus.
#
# --proxy-headers so client addresses come from the load balancer's
# X-Forwarded-For; the login throttle counts failures per address.
CMD ["sh", "-c", "if [ -n \"$DATABASE_URL\" ]; then alembic upgrade head; fi && exec uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\" --proxy-headers --forwarded-allow-ips='*'"]
