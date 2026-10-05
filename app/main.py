import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.core.config import Settings, get_settings
from app.routes import animals, events, health
from app.services.event_store import EventStore, InMemoryEventStore
from app.services.mysql_event_store import MySqlEventStore, create_pool

logger = logging.getLogger(__name__)


def build_event_store(settings: Settings) -> EventStore:
    """Pick the storage backend declared in EVENT_STORAGE."""
    if settings.event_storage == "mysql":
        return MySqlEventStore(create_pool(settings))
    return InMemoryEventStore()


def create_app(settings: Settings | None = None, event_store: EventStore | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()

    # The store is built on startup, not at import time: importing this module
    # must never open a database connection, or the test suite and any tooling
    # that merely imports the app would require a running MySQL.
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.event_store = event_store or build_event_store(settings)
        logger.info("Event storage ready: %s", settings.event_storage)
        yield

    app = FastAPI(
        title="SmartCattle Backend", version=__version__,
        description="REST API for receiving AI cattle events and querying animals and events.",
        lifespan=lifespan,
    )
    app.state.settings = settings

    # Registered before CORSMiddleware so it runs inside the CORS layer: error
    # responses built here still receive the headers the browser requires.
    # Starlette's own handler for Exception sits outside CORS, which leaves the
    # frontend with an opaque CORS failure instead of the 500.
    @app.middleware("http")
    async def handle_unexpected_error(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        try:
            return await call_next(request)
        except Exception:
            logger.exception("Unhandled application error")
            return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
        allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-API-Key"],
    )
    app.include_router(health.router)
    app.include_router(animals.router)
    app.include_router(events.router)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Rejected values are never echoed back. Non-finite floats such as NaN
        # have no JSON representation, so including them turned a 422 into a 500.
        detail = [
            {"type": error["type"], "loc": list(error["loc"]), "msg": error["msg"]}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": detail})

    return app


app = create_app()
