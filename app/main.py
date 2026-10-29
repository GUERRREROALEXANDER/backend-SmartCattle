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
from app.routes import animals, auth, cameras, events, health, metrics, videos
from app.services.animal_store import InMemoryAnimalStore, SqlAnimalStore
from app.services.camera_store import InMemoryCameraStore, SqlCameraStore
from app.services.event_store import InMemoryEventStore, SqlEventStore
from app.services.login_throttle import LoginThrottle
from app.services.user_store import InMemoryUserStore, SqlUserStore
from app.services.video_analysis_store import InMemoryVideoAnalysisStore, SqlVideoAnalysisStore

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
    else:
        # The schema is created by Alembic migrations (alembic upgrade head), never here.
        app.state.engine = create_db_engine(settings.sqlalchemy_url)
        sessions = create_session_factory(app.state.engine)
        app.state.event_store = SqlEventStore(sessions)
        app.state.camera_store = SqlCameraStore(sessions)
        app.state.video_analysis_store = SqlVideoAnalysisStore(sessions)
        app.state.user_store = SqlUserStore(sessions)
        app.state.animal_store = SqlAnimalStore(sessions)
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
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type", "X-API-Key", "Authorization"],
    )
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(animals.router)
    app.include_router(events.router)
    app.include_router(cameras.router)
    app.include_router(videos.router)
    app.include_router(metrics.router)

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
    async def handle_unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled application error", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app


app = create_app()
