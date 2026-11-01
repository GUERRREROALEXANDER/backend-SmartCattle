import logging
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from app import __version__
from app.core.config import Settings, get_settings
from app.db import create_db_engine, create_session_factory
from app.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from app.routes import (
    alerts,
    animals,
    auth,
    cameras,
    catalogs,
    events,
    farms,
    health,
    live,
    metrics,
    reports,
    users,
    video_jobs,
    video_uploads,
    videos,
    zones,
)
from app.services.alert_store import InMemoryAlertStore, SqlAlertStore
from app.services.animal_store import InMemoryAnimalStore, SqlAnimalStore
from app.services.broadcaster import Broadcaster
from app.services.camera_store import InMemoryCameraStore, SqlCameraStore
from app.services.catalog_store import InMemoryCatalogStore, SqlCatalogStore
from app.services.event_store import InMemoryEventStore, SqlEventStore
from app.services.farm_store import InMemoryFarmStore, SqlFarmStore
from app.services.login_throttle import LoginThrottle
from app.services.occupancy_store import InMemoryOccupancyStore, SqlOccupancyStore
from app.services.user_store import InMemoryUserStore, SqlUserStore
from app.services.video_analysis_store import InMemoryVideoAnalysisStore, SqlVideoAnalysisStore
from app.services.video_storage import S3VideoStorage
from app.services.video_upload_store import InMemoryVideoUploadStore, SqlVideoUploadStore
from app.services.zone_store import InMemoryZoneStore, SqlZoneStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    # Render replaces an instance by starting the new one and stopping this
    # one; without the dispose its pooled PostgreSQL connections stay open on
    # the server until they time out, and the free Neon plan counts them.
    engine: Engine | None = getattr(app.state, "engine", None)
    if engine is not None:
        engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()
    app = FastAPI(
        title="SmartCattle Backend", version=__version__, lifespan=_lifespan,
        description=(
            "REST API for AI cattle events, camera status, simulated video analyses and summary metrics. "
            "Data is stored in PostgreSQL when DATABASE_URL is set, in memory otherwise."
        ),
    )
    app.state.settings = settings
    app.state.clock = lambda: datetime.now(UTC)
    app.state.engine = None
    if settings.sqlalchemy_url is None:
        app.state.event_store = InMemoryEventStore()
        app.state.camera_store = InMemoryCameraStore()
        app.state.video_analysis_store = InMemoryVideoAnalysisStore()
        app.state.user_store = InMemoryUserStore()
        app.state.animal_store = InMemoryAnimalStore()
        app.state.zone_store = InMemoryZoneStore()
        app.state.farm_store = InMemoryFarmStore()
        app.state.alert_store = InMemoryAlertStore()
        app.state.occupancy_store = InMemoryOccupancyStore()
        app.state.video_upload_store = InMemoryVideoUploadStore()
        app.state.catalog_store = InMemoryCatalogStore()
    else:
        # The schema is created by Alembic migrations (alembic upgrade head), never here.
        app.state.engine = create_db_engine(settings.sqlalchemy_url)
        sessions = create_session_factory(app.state.engine)
        app.state.event_store = SqlEventStore(sessions)
        app.state.camera_store = SqlCameraStore(sessions)
        app.state.video_analysis_store = SqlVideoAnalysisStore(sessions)
        app.state.user_store = SqlUserStore(sessions)
        app.state.animal_store = SqlAnimalStore(sessions)
        app.state.zone_store = SqlZoneStore(sessions)
        app.state.farm_store = SqlFarmStore(sessions)
        app.state.alert_store = SqlAlertStore(sessions)
        app.state.occupancy_store = SqlOccupancyStore(sessions)
        app.state.video_upload_store = SqlVideoUploadStore(sessions)
        app.state.catalog_store = SqlCatalogStore(sessions)
    app.state.video_storage = S3VideoStorage(settings) if settings.video_storage_enabled else None
    app.state.broadcaster = Broadcaster()
    # Per process, not per worker: the lockout is best effort, and Render's
    # free plan runs one worker anyway.
    app.state.login_throttle = LoginThrottle(
        max_attempts=settings.max_login_attempts,
        lockout=timedelta(seconds=settings.login_lockout_seconds),
    )
    if settings.ai_api_key is None:
        logger.warning("AI_API_KEY is not set; POST /api/ai/events accepts unauthenticated requests")
    if not settings.auth_enabled:
        logger.warning("JWT_SECRET is not set; /api/auth/* returns 503 and no endpoint is protected")
    if not settings.video_storage_enabled:
        logger.warning("VIDEO_STORAGE_* is not set; video uploads and video jobs return 503")
    # Added before CORS so it wraps it: a request rejected by CORS still gets
    # an id and a log line, which is what makes a CORS problem diagnosable
    # instead of a silent browser failure.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
        # PATCH and DELETE are needed by the herd and zone endpoints. Without
        # them the browser's preflight is refused and the request is never
        # sent, while curl and the Node test suite succeed -- neither enforces
        # CORS, so the endpoints look fine everywhere except in the product.
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-API-Key", "Authorization", REQUEST_ID_HEADER],
        # So the browser can read the id off the response and report it.
        expose_headers=[REQUEST_ID_HEADER],
    )
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(animals.router)
    app.include_router(events.router)
    app.include_router(cameras.router)
    app.include_router(alerts.router)
    app.include_router(live.router)
    app.include_router(zones.router)
    app.include_router(farms.router)
    app.include_router(video_uploads.router)
    app.include_router(video_jobs.router)
    app.include_router(videos.router)
    app.include_router(metrics.router)
    app.include_router(catalogs.router)
    app.include_router(reports.router)

    @app.exception_handler(RequestValidationError)
    # Starlette passes the request positionally; the handlers do not read it.
    async def handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        detail = []
        for error in exc.errors():
            item = {
                "type": error["type"],
                "loc": [part if isinstance(part, (str, int)) else str(part) for part in error["loc"]],
                "msg": error["msg"],
            }
            if "ctx" in error:
                item["ctx"] = {
                    key: value if isinstance(value, (str, bool, int)) or
                    (isinstance(value, float) and math.isfinite(value)) else str(value)
                    for key, value in error["ctx"].items()
                }
            detail.append(item)
        return JSONResponse(status_code=422, content={"detail": detail})

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.exception(
            "Unhandled application error", exc_info=exc, extra={"request_id": request_id},
        )
        # The id goes in the body as well as the header, so a user can quote it
        # from an error message without opening developer tools. The message
        # itself stays generic: an exception string can carry a connection
        # string or a row of data.
        content: dict[str, str] = {"detail": "Internal server error"}
        headers: dict[str, str] = {}
        if request_id is not None:
            content["request_id"] = request_id
            # Set here too. Starlette's ServerErrorMiddleware is outermost, so
            # this response is built outside RequestContextMiddleware and never
            # passes back through it -- the header it would have added is
            # missing on exactly the responses that most need correlating.
            headers[REQUEST_ID_HEADER] = request_id
        return JSONResponse(status_code=500, content=content, headers=headers)

    return app


app = create_app()
