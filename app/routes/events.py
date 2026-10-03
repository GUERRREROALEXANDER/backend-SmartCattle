import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from app.core.config import Settings
from app.routes.health import get_app_settings
from app.schemas.event import AIEventCreate, Event, EventList
from app.services.event_store import InMemoryEventStore

router = APIRouter(tags=["events"])
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_event_store(request: Request) -> InMemoryEventStore:
    return request.app.state.event_store


def verify_api_key(
    settings: Settings = Depends(get_app_settings),
    api_key: str | None = Security(api_key_header),
) -> None:
    if settings.ai_api_key is not None:
        expected = settings.ai_api_key.get_secret_value()
        if api_key is None or not secrets.compare_digest(api_key.encode("utf-8"), expected.encode("utf-8")):
            raise HTTPException(status_code=401, detail="Invalid or missing API key")


@router.get("/api/events", response_model=EventList)
def list_events(store: InMemoryEventStore = Depends(get_event_store)) -> EventList:
    items = store.list()
    return EventList(items=items, total=len(items))


@router.post("/api/ai/events", response_model=Event, status_code=201, dependencies=[Depends(verify_api_key)])
def create_event(data: AIEventCreate, store: InMemoryEventStore = Depends(get_event_store)) -> Event:
    return store.add(data)
