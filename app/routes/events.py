from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.dependencies import Clock, get_clock, get_event_store, verify_api_key
from app.schemas.event import AIEventCreate, Event, EventLevel, EventList, EventType
from app.services.event_store import EventQuery, EventStore

router = APIRouter(tags=["events"])

#: Default page size. The feed shows a screenful; 50 covers it without the
#: client having to ask for a number.
DEFAULT_LIMIT = 50
#: Ceiling. The store keeps at most 1000 in memory, so a larger page could
#: never be filled anyway, and an unbounded limit is a way to make the server
#: serialise its whole history on request.
MAX_LIMIT = 200


@router.get("/api/events", response_model=EventList)
def list_events(
    camera_id: Annotated[str | None, Query(max_length=64)] = None,
    level: EventLevel | None = None,
    event_type: EventType | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
    store: EventStore = Depends(get_event_store),
) -> EventList:
    """The event feed, newest received first.

    `total` is the number of events matching the filters, not the number
    returned, so a client can page without asking twice. Returning the page
    length instead would make the last page look like the whole history.
    """
    query = EventQuery(
        camera_id=camera_id, level=level, event_type=event_type, limit=limit, offset=offset,
    )
    page = store.page(query)
    return EventList(items=page.items, total=page.total)


@router.post("/api/ai/events", response_model=Event, status_code=201, dependencies=[Depends(verify_api_key)])
def create_event(
    data: AIEventCreate,
    store: EventStore = Depends(get_event_store),
    clock: Clock = Depends(get_clock),
) -> Event:
    return store.add(data, clock())
