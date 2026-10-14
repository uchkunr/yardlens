"""Loading bays: a vehicle parks in the zone, cargo crosses the counting line, the vehicle leaves.

``LoadingTracker`` turns per-frame observations into loading sessions. Like ``FrameAnalyzer`` it has
no threads, I/O or clock of its own: the camera worker calls ``update`` once per inference tick
with monotonic time, which also makes it easy to test with synthetic input.

A session opens once a vehicle has stood in the zone for ``arrive_after`` seconds, or as soon as
cargo is counted with no vehicle seen yet, so no cargo is ever lost. It closes once neither a
vehicle nor cargo has been seen for ``depart_after`` seconds. Both delays ride out the everyday
gaps: a worker walking in front of the truck, a break in loading.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from yardlens_api.core.enums import SessionStatus
from yardlens_api.schemas.camera import LoadingZone
from yardlens_api.services.analyzer import TrackedObject
from yardlens_api.services.plates import Frame, PlateRead, PlateVotes

# A vehicle hidden for less than this (someone walking past) is still the same visit.
PRESENCE_GAP_SECONDS = 5.0
PLATE_READ_INTERVAL_SECONDS = 0.5


class SessionChange(StrEnum):
    OPENED = "opened"
    UPDATED = "updated"
    CLOSED = "closed"


@dataclass(slots=True)
class SessionState:
    """A loading session as the worker knows it. Copied (``replace``) before leaving the worker."""

    id: str
    camera_id: int
    arrived_at: datetime
    status: SessionStatus = SessionStatus.ACTIVE
    departed_at: datetime | None = None
    loading_started_at: datetime | None = None
    loading_finished_at: datetime | None = None
    loaded: int = 0
    unloaded: int = 0
    vehicle_class: str | None = None
    plate: str | None = None
    plate_confidence: float | None = None
    vehicle_snapshot_path: str | None = None
    plate_snapshot_path: str | None = None


@dataclass(frozen=True, slots=True)
class SessionUpdate:
    change: SessionChange
    session: SessionState


def point_in_polygon(x: float, y: float, polygon: Sequence[tuple[float, float]]) -> bool:
    """Even-odd ray casting; the polygon need not be convex."""
    inside = False
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def largest(objects: Sequence[TrackedObject]) -> TrackedObject | None:
    def area(obj: TrackedObject) -> float:
        x1, y1, x2, y2 = obj.box
        return (x2 - x1) * (y2 - y1)

    return max(objects, key=area, default=None)


class LoadingTracker:
    def __init__(
        self,
        camera_id: int,
        zone: LoadingZone,
        *,
        arrive_after: float = 15.0,
        depart_after: float = 60.0,
    ) -> None:
        self._camera_id = camera_id
        self.zone = zone
        self._arrive_after = arrive_after
        self._depart_after = depart_after
        self._session: SessionState | None = None
        self._presence_since: float | None = None
        self._last_vehicle_at: float | None = None
        self._last_cargo_at: float | None = None
        self._vehicle_class: str | None = None
        self._parked_now = False
        self._votes = PlateVotes()
        self._last_plate_attempt = float("-inf")

    @property
    def session(self) -> SessionState | None:
        return replace(self._session) if self._session else None

    @property
    def plate_crop(self) -> Frame | None:
        """The clearest image of the leading plate, for the session's plate snapshot."""
        return self._votes.crop

    def parked(
        self, vehicles: Sequence[TrackedObject], width: int, height: int
    ) -> list[TrackedObject]:
        """Vehicles standing in the zone, judged by where their box meets the ground."""
        polygon = [(point.x, point.y) for point in self.zone.points]
        return [
            obj
            for obj in vehicles
            if point_in_polygon((obj.box[0] + obj.box[2]) / 2 / width, obj.box[3] / height, polygon)
        ]

    def update(
        self,
        *,
        t: float,
        now: datetime,
        parked: Sequence[TrackedObject],
        loaded: int = 0,
        unloaded: int = 0,
    ) -> SessionUpdate | None:
        """Feed one inference tick taken at monotonic time ``t`` (wall clock ``now``)."""

        def wall(moment: float) -> datetime:
            return now - timedelta(seconds=t - moment)

        self._parked_now = bool(parked)
        vehicle = largest(parked)
        if vehicle is not None:
            if self._last_vehicle_at is None or t - self._last_vehicle_at > PRESENCE_GAP_SECONDS:
                self._presence_since = t
                if self._session is None:
                    self._votes = PlateVotes()  # a different vehicle; forget earlier readings
            self._last_vehicle_at = t
            self._vehicle_class = vehicle.class_name
        cargo = loaded > 0 or unloaded > 0
        if cargo:
            self._last_cargo_at = t

        change: SessionChange | None = None
        session = self._session
        if session is None:
            settled = (
                vehicle is not None
                and self._presence_since is not None
                and t - self._presence_since >= self._arrive_after
            )
            if not (settled or cargo):
                return None
            start = (
                self._presence_since
                if vehicle is not None and self._presence_since is not None
                else t
            )
            session = self._session = SessionState(
                id=uuid.uuid4().hex,
                camera_id=self._camera_id,
                arrived_at=wall(start),
                vehicle_class=self._vehicle_class if vehicle is not None else None,
            )
            self._apply_plate(session)
            change = SessionChange.OPENED

        if vehicle is not None and session.vehicle_class is None:
            session.vehicle_class = vehicle.class_name
            change = change or SessionChange.UPDATED
        if cargo:
            moment = wall(t)
            session.loaded += loaded
            session.unloaded += unloaded
            session.loading_started_at = session.loading_started_at or moment
            session.loading_finished_at = moment
            change = change or SessionChange.UPDATED

        last_activity = max(
            (
                moment
                for moment in (self._last_vehicle_at, self._last_cargo_at)
                if moment is not None
            ),
            default=t,
        )
        if t - last_activity >= self._depart_after:
            return self._close(wall(last_activity))
        return SessionUpdate(change, replace(session)) if change else None

    @property
    def occupied(self) -> bool:
        """A vehicle stands in the zone or a session is open."""
        return self._parked_now or self._session is not None

    @property
    def plate_pending(self) -> bool:
        """A vehicle is parked and its plate reading has not settled yet."""
        return self._parked_now and not self._votes.settled

    def wants_plate(self, t: float) -> bool:
        """Whether to read the plate of the vehicle parked now (until the reading settles)."""
        return self.plate_pending and t - self._last_plate_attempt >= PLATE_READ_INTERVAL_SECONDS

    def add_plate_reads(self, reads: Sequence[PlateRead], t: float) -> SessionUpdate | None:
        """Pool plate readings; returns an update when the open session's plate changed."""
        self._last_plate_attempt = t
        changed = False
        for read in reads:
            changed = self._votes.add(read) or changed
        session = self._session
        if session is None:
            return None
        before = (session.plate, session.plate_confidence)
        self._apply_plate(session)
        if not changed and before == (session.plate, session.plate_confidence):
            return None
        return SessionUpdate(SessionChange.UPDATED, replace(session))

    def set_snapshots(self, *, vehicle: str | None = None, plate: str | None = None) -> None:
        if self._session is None:
            return
        if vehicle is not None:
            self._session.vehicle_snapshot_path = vehicle
        if plate is not None:
            self._session.plate_snapshot_path = plate

    def close(self, now: datetime, t: float) -> SessionUpdate | None:
        """End the open session now (camera stopped or its zone removed)."""
        if self._session is None:
            return None
        last_activity = max(
            (
                moment
                for moment in (self._last_vehicle_at, self._last_cargo_at)
                if moment is not None
            ),
            default=t,
        )
        return self._close(now - timedelta(seconds=max(0.0, t - last_activity)))

    def _close(self, departed_at: datetime) -> SessionUpdate | None:
        session = self._session
        if session is None:
            return None
        session.status = SessionStatus.COMPLETED
        session.departed_at = max(departed_at, session.arrived_at)
        self._session = None
        self._presence_since = None
        self._last_vehicle_at = None
        self._last_cargo_at = None
        self._votes = PlateVotes()
        return SessionUpdate(SessionChange.CLOSED, replace(session))

    def _apply_plate(self, session: SessionState) -> None:
        if self._votes.best is not None:
            session.plate = self._votes.best
            session.plate_confidence = self._votes.confidence
