import logging
import math

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.core.config import Settings, get_settings
from app.routes import animals, events, health
from app.services.event_store import InMemoryEventStore

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()
    app = FastAPI(
        title="SmartCattle Backend", version=__version__,
        description="REST API for receiving AI cattle events and querying animals and events. Events are stored in memory.",
    )
    app.state.settings = settings
    app.state.event_store = InMemoryEventStore()
    if settings.ai_api_key is None:
        logger.warning("AI_API_KEY is not set; POST /api/ai/events accepts unauthenticated requests")
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
        allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-API-Key"],
    )
    app.include_router(health.router)
    app.include_router(animals.router)
    app.include_router(events.router)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
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
        logger.exception("Unhandled application error", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app


app = create_app()
