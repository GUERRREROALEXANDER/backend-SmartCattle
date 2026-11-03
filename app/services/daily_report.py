"""Turns one day of stored detections and alerts into the daily report.

Works on what the stores return and touches neither storage nor HTTP, so both
store implementations produce the same report and the rules can be tested alone.
"""

from collections import Counter
from collections.abc import Iterable
from datetime import date, datetime

from app.core.time_window import Window
from app.schemas.alert import Alert, AlertStatus
from app.schemas.detection import DetectionLogEntry
from app.schemas.event import EventLevel
from app.schemas.report import (
    ActivitySummary,
    AlertSummary,
    CameraActivity,
    DailyReport,
    HealthNotice,
    HealthNoticeKind,
)

#: Below two hours of footage a share says more about when the camera was on than about the herd.
MIN_OBSERVED_MINUTES_FOR_HEALTH = 120
#: Starting points, not veterinary thresholds: they only decide when to suggest a look, and a
#: camera that only runs by day reads low because cattle do most of their lying at night.
PROLONGED_LYING_SHARE = 0.8
LITTLE_LYING_SHARE = 0.2

_NOTICE_MESSAGES = {
    HealthNoticeKind.PROLONGED_LYING: "Cattle spent most of the observed time lying down. Worth checking on them.",
    HealthNoticeKind.LITTLE_LYING: "Cattle spent little of the observed time lying down. Worth checking on them.",
}


class ActivityTally:
    """Accumulates entries; one per camera and one for the whole herd."""

    def __init__(self) -> None:
        self._postures: Counter[str] = Counter()
        self._cattle_per_minute: Counter[datetime] = Counter()
        self._people_per_minute: Counter[datetime] = Counter()

    def add(self, entry: DetectionLogEntry) -> None:
        self._postures.update({
            "lying": entry.cattle_lying, "standing": entry.cattle_standing,
            "walking": entry.cattle_walking, "unknown": entry.cattle_unknown,
        })
        # Per minute rather than per entry, so the herd's peak adds up its cameras
        # at the same moment. Adding zero still records that the minute was watched.
        seen = entry.cattle_lying + entry.cattle_standing + entry.cattle_walking + entry.cattle_unknown
        self._cattle_per_minute[entry.minute] += seen
        self._people_per_minute[entry.minute] += entry.people

    @property
    def observed_minutes(self) -> int:
        return len(self._cattle_per_minute)

    @property
    def lying_share(self) -> float | None:
        known = self._postures["lying"] + self._postures["standing"] + self._postures["walking"]
        return self._postures["lying"] / known if known else None

    def summary(self) -> ActivitySummary:
        share = self.lying_share
        return ActivitySummary(
            observed_minutes=self.observed_minutes,
            lying_minutes=self._postures["lying"],
            standing_minutes=self._postures["standing"],
            walking_minutes=self._postures["walking"],
            unknown_posture_minutes=self._postures["unknown"],
            peak_cattle=max(self._cattle_per_minute.values(), default=0),
            peak_people=max(self._people_per_minute.values(), default=0),
            minutes_with_people=sum(1 for people in self._people_per_minute.values() if people > 0),
            lying_share=None if share is None else round(share, 3),
        )


class DailyReportBuilder:
    """Builds the report of one day; the caller has already limited the data to what the account may see."""

    def __init__(self, day: date, time_zone: str, span: Window, now: datetime) -> None:
        self._day = day
        self._time_zone = time_zone
        self._span = span
        self._now = now

    def build(self, entries: Iterable[DetectionLogEntry], alerts: list[Alert], events_total: int) -> DailyReport:
        herd = ActivityTally()
        per_camera: dict[str, ActivityTally] = {}
        for entry in entries:
            herd.add(entry)
            per_camera.setdefault(entry.camera_id, ActivityTally()).add(entry)
        cameras = sorted(per_camera.items())
        return DailyReport(
            day=self._day, time_zone=self._time_zone, start=self._span.start, end=self._span.end,
            complete=self._now >= self._span.end, generated_at=self._now,
            herd=herd.summary(),
            cameras=[CameraActivity(camera_id=camera, **tally.summary().model_dump()) for camera, tally in cameras],
            health=[notice for camera, tally in cameras if (notice := self._notice(camera, tally)) is not None],
            alerts=self._alerts(alerts),
            events_total=events_total,
        )

    @staticmethod
    def _notice(camera_id: str, tally: ActivityTally) -> HealthNotice | None:
        share = tally.lying_share
        if share is None or tally.observed_minutes < MIN_OBSERVED_MINUTES_FOR_HEALTH:
            return None
        if share > PROLONGED_LYING_SHARE:
            kind = HealthNoticeKind.PROLONGED_LYING
        elif share < LITTLE_LYING_SHARE:
            kind = HealthNoticeKind.LITTLE_LYING
        else:
            return None
        return HealthNotice(
            camera_id=camera_id, kind=kind, lying_share=round(share, 3),
            observed_minutes=tally.observed_minutes, message=_NOTICE_MESSAGES[kind],
        )

    @staticmethod
    def _alerts(alerts: list[Alert]) -> AlertSummary:
        counts = Counter(alert.severity.value for alert in alerts)
        counts.update(alert.status.value for alert in alerts)
        return AlertSummary(
            total=len(alerts),
            low=counts[EventLevel.LOW.value], medium=counts[EventLevel.MEDIUM.value],
            high=counts[EventLevel.HIGH.value],
            open=counts[AlertStatus.OPEN.value], acknowledged=counts[AlertStatus.ACKNOWLEDGED.value],
            resolved=counts[AlertStatus.RESOLVED.value],
        )
