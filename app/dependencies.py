"""Shared FastAPI dependencies.

Everything the routes resolve out of ``app.state`` lives here. Before this
module each router declared the providers it happened to need first, so
cameras.py imported the API-key check from events.py, metrics.py imported the
clock from videos.py, and ``get_clock`` existed twice in identical copies.
Route modules now depend on this module instead of on each other.

``app.state`` is untyped by design in Starlette, so these functions are also
the single place where that ``Any`` is turned back into a real type.
"""

import secrets
from collections.abc import Callable
from datetime import datetime

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from app.core.config import Settings
from app.services.camera_store import CameraStore
from app.services.event_store import EventStore
from app.services.video_analysis_store import VideoAnalysisStore

#: Returns the current time, always timezone-aware and in UTC. Injected so
#: tests can move it without patching datetime.now globally.
Clock = Callable[[], datetime]

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def get_event_store(request: Request) -> EventStore:
    store: EventStore = request.app.state.event_store
    return store


def get_camera_store(request: Request) -> CameraStore:
    store: CameraStore = request.app.state.camera_store
    return store


def get_video_analysis_store(request: Request) -> VideoAnalysisStore:
    store: VideoAnalysisStore = request.app.state.video_analysis_store
    return store


def verify_api_key(
    settings: Settings = Depends(get_app_settings),
    api_key: str | None = Security(api_key_header),
) -> None:
    """Rejects ingestion requests without the shared key, when one is configured.

    With AI_API_KEY unset the endpoints stay open; create_app logs a warning
    about it at startup.
    """
    if settings.ai_api_key is None:
        return
    expected = settings.ai_api_key.get_secret_value()
    if api_key is None or not secrets.compare_digest(api_key.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
