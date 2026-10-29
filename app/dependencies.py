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
from app.services.animal_store import AnimalStore
from app.services.camera_store import CameraStore
from app.services.event_store import EventStore
from app.services.login_throttle import LoginThrottle
from app.services.user_store import StoredUser, UserStore
from app.services.video_analysis_store import VideoAnalysisStore
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


def get_user_store(request: Request) -> UserStore:
    store: UserStore = request.app.state.user_store
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
    claims = decode_access_token(credentials.credentials, secret=secret, now=clock())
    if claims is None:
        raise unauthorized
    try:
        user_id = int(claims["sub"])
    except (KeyError, TypeError, ValueError) as error:
        raise unauthorized from error
    user = store.by_id(user_id)
    if user is None or not user.is_active:
        raise unauthorized
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
