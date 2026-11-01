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
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings
from app.core.security import decode_access_token
from app.schemas.auth import UserRole
from app.services.alert_store import AlertStore
from app.services.animal_store import AnimalStore
from app.services.broadcaster import Broadcaster
from app.services.camera_store import CameraStore
from app.services.catalog_store import CatalogStore
from app.services.event_store import EventStore
from app.services.farm_store import FarmStore
from app.services.login_throttle import LoginThrottle
from app.services.occupancy_store import OccupancyStore
from app.services.user_store import StoredUser, UserStore
from app.services.video_analysis_store import VideoAnalysisStore
from app.services.video_storage import VideoStorage
from app.services.video_upload_store import VideoUploadStore
from app.services.zone_store import ZoneStore

#: Returns the current time, always timezone-aware and in UTC. Injected so
#: tests can move it without patching datetime.now globally.
Clock = Callable[[], datetime]

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

#: auto_error=False so a missing header reaches our own handler. The default
#: raises a 403 before any of this runs, which is the wrong status and gives no
#: WWW-Authenticate header.
bearer_scheme = HTTPBearer(auto_error=False)


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def get_event_store(request: Request) -> EventStore:
    store: EventStore = request.app.state.event_store
    return store


def get_animal_store(request: Request) -> AnimalStore:
    store: AnimalStore = request.app.state.animal_store
    return store


def get_camera_store(request: Request) -> CameraStore:
    store: CameraStore = request.app.state.camera_store
    return store


def get_video_analysis_store(request: Request) -> VideoAnalysisStore:
    store: VideoAnalysisStore = request.app.state.video_analysis_store
    return store


def get_zone_store(request: Request) -> ZoneStore:
    store: ZoneStore = request.app.state.zone_store
    return store


def get_alert_store(request: Request) -> AlertStore:
    store: AlertStore = request.app.state.alert_store
    return store


def get_broadcaster(request: Request) -> Broadcaster:
    broadcaster: Broadcaster = request.app.state.broadcaster
    return broadcaster


def get_occupancy_store(request: Request) -> OccupancyStore:
    store: OccupancyStore = request.app.state.occupancy_store
    return store


def get_video_upload_store(request: Request) -> VideoUploadStore:
    store: VideoUploadStore = request.app.state.video_upload_store
    return store


def require_video_storage(request: Request) -> VideoStorage:
    """The bucket, or 503 when this deployment has not configured one."""
    storage: VideoStorage | None = request.app.state.video_storage
    if storage is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Video storage is not configured on this server",
        )
    return storage


def get_farm_store(request: Request) -> FarmStore:
    store: FarmStore = request.app.state.farm_store
    return store


def get_user_store(request: Request) -> UserStore:
    store: UserStore = request.app.state.user_store
    return store


def get_catalog_store(request: Request) -> CatalogStore:
    store: CatalogStore = request.app.state.catalog_store
    return store


def get_login_throttle(request: Request) -> LoginThrottle:
    throttle: LoginThrottle = request.app.state.login_throttle
    return throttle


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


def require_jwt_secret(settings: Settings = Depends(get_app_settings)) -> str:
    """The signing secret, or 503 when the deployment has not configured one.

    503 and not 500: nothing is broken, the feature is switched off, and the
    caller should be told it may work later rather than that the server failed.
    """
    if settings.jwt_secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured on this server",
        )
    return settings.jwt_secret.get_secret_value()


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    secret: str = Depends(require_jwt_secret),
    store: UserStore = Depends(get_user_store),
    clock: Clock = Depends(get_clock),
) -> StoredUser:
    """Resolves the bearer token to a user, or raises 401.

    The user is loaded on every request rather than trusted from the token.
    The claims are signed, so they cannot be forged, but they are also a
    snapshot: an account deactivated after its token was issued would keep
    working until the token expired.
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid access token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or not credentials.credentials:
        raise unauthorized
    user = user_for_token(credentials.credentials, secret, store, clock())
    if user is None:
        raise unauthorized
    return user


def user_for_token(token: str, secret: str, store: UserStore, now: datetime) -> StoredUser | None:
    """The active account a token names, or None.

    Shared by the HTTP dependency and the WebSocket handshake so the two
    cannot drift into accepting different tokens.
    """
    claims = decode_access_token(token, secret=secret, now=now)
    if claims is None:
        return None
    try:
        user_id = int(claims["sub"])
    except (KeyError, TypeError, ValueError):
        return None
    user = store.by_id(user_id)
    if user is None or not user.is_active:
        return None
    return user


CurrentUser = Annotated[StoredUser, Depends(current_user)]


def require_owner(user: CurrentUser) -> StoredUser:
    """Rejects a worker with 403.

    403 and not 404: the caller is authenticated and known, so hiding the
    endpoint's existence buys nothing and makes a permissions problem look
    like a wrong URL.
    """
    if user.role is not UserRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires the owner role",
        )
    return user


OwnerUser = Annotated[StoredUser, Depends(require_owner)]
