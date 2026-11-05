from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import (
    Clock,
    Visible,
    get_alert_store,
    get_broadcaster,
    get_camera_store,
    get_clock,
    get_event_store,
    get_user_store,
    get_whatsapp_store,
    reporter_account,
)
from app.schemas.event import AIEventCreate, Event, EventLevel, EventList, EventType
from app.services.alert_store import AlertStore
from app.services.broadcaster import Broadcaster
from app.services.camera_store import CameraStore
from app.services.event_store import EventQuery, EventStore
from app.services.user_store import UserStore
from app.services.whatsapp_notices import queue_alert_notice
from app.services.whatsapp_store import WhatsappStore

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
    scope: Visible,
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
        camera_ids=scope.camera_ids,
    )
    page = store.page(query)
    return EventList(items=page.items, total=page.total)


@router.post("/api/ai/events", response_model=Event, status_code=201)
def create_event(
    data: AIEventCreate,
    reporter: int | None = Depends(reporter_account),
    store: EventStore = Depends(get_event_store),
    alerts: AlertStore = Depends(get_alert_store),
    cameras: CameraStore = Depends(get_camera_store),
    broadcaster: Broadcaster = Depends(get_broadcaster),
    whatsapp: WhatsappStore = Depends(get_whatsapp_store),
    users: UserStore = Depends(get_user_store),
    clock: Clock = Depends(get_clock),
) -> Event:
    """Stores a live detection, raises its security alert and pushes both to open dashboards.

    The alert is raised here and not in the event store, because only this
    endpoint carries live detections. Events derived from an uploaded video
    (POST /api/videos/analyses) go into the same feed but describe recorded
    footage; alerting on them would sound an alarm for something that already
    happened.
    """
    # A farm PC only speaks for its own cameras.
    if reporter is not None and cameras.owner_of(data.camera_id) != reporter:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    now = clock()
    event = store.add(data, now)
    alert = alerts.raise_for(event, now)
    account = cameras.owner_of(event.camera_id)
    broadcaster.publish("event.created", event, now, account_id=account)
    broadcaster.publish("alert.created", alert, now, account_id=account)
    queue_alert_notice(
        alert, detected_at=event.timestamp, whatsapp=whatsapp, users=users, cameras=cameras, now=now,
    )
    return event
