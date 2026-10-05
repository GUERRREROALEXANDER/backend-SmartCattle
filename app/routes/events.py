from fastapi import APIRouter, Depends, Query, Request, Response

from app.core.security import verify_ingest_key, verify_read_key
from app.schemas.event import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    AIEventCreate,
    Event,
    EventList,
)
from app.services.event_store import EventStore

router = APIRouter(tags=["events"])


def get_event_store(request: Request) -> EventStore:
    return request.app.state.event_store


@router.get(
    "/api/events", response_model=EventList, dependencies=[Depends(verify_read_key)]
)
def list_events(
    store: EventStore = Depends(get_event_store),
    limit: int = Query(
        DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Events per page"
    ),
    offset: int = Query(0, ge=0, description="Events to skip before this page"),
) -> EventList:
    return EventList(
        items=store.list(limit=limit, offset=offset),
        total=store.count(),
        limit=limit,
        offset=offset,
    )


@router.post(
    "/api/ai/events",
    response_model=Event,
    status_code=201,
    dependencies=[Depends(verify_ingest_key)],
    responses={200: {"description": "This ai_event_id was already stored; nothing was created"}},
)
def create_event(
    data: AIEventCreate, response: Response, store: EventStore = Depends(get_event_store)
) -> Event:
    event, created = store.add(data)
    if not created:
        response.status_code = 200
    return event
