from fastapi import APIRouter, Depends

from app.dependencies import get_event_store, verify_api_key
from app.schemas.event import AIEventCreate, Event, EventList
from app.services.event_store import EventStore

router = APIRouter(tags=["events"])


@router.get("/api/events", response_model=EventList)
def list_events(store: EventStore = Depends(get_event_store)) -> EventList:
    items = store.list()
    return EventList(items=items, total=len(items))


@router.post("/api/ai/events", response_model=Event, status_code=201, dependencies=[Depends(verify_api_key)])
def create_event(data: AIEventCreate, store: EventStore = Depends(get_event_store)) -> Event:
    return store.add(data)
