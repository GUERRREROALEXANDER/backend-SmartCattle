from fastapi import APIRouter, Depends

from app import __version__
from app.core.config import Settings
from app.core.security import get_app_settings
from app.schemas.status import AIServiceStatus, HealthResponse, StatusResponse, WelcomeResponse

router = APIRouter(tags=["health"])


@router.get("/", response_model=WelcomeResponse)
def welcome() -> WelcomeResponse:
    return WelcomeResponse(name="SmartCattle Backend", version=__version__, docs="/docs")


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


# Public on purpose: deployment platforms poll it without credentials, and it
# exposes no data beyond the version and which storage backend is active.
@router.get("/api/status", response_model=StatusResponse)
def status(settings: Settings = Depends(get_app_settings)) -> StatusResponse:
    return StatusResponse(
        status="ok", version=__version__,
        ai_service=AIServiceStatus(configured=settings.smartcattle_ai_url is not None),
        storage=settings.event_storage,
    )
