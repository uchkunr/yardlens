from datetime import UTC, datetime, timedelta

import numpy as np

from yardlens_api.core.enums import EventType, SessionStatus
from yardlens_api.schemas.camera import LoadingZone, ZonePoint
from yardlens_api.services.analyzer import TrackedObject
from yardlens_api.services.loading import LoadingTracker, SessionChange, point_in_polygon
from yardlens_api.services.notifier import event_caption
from yardlens_api.services.plates import (
    PlateRead,
    PlateVotes,
    format_plate,
    is_uzbek_plate,
    normalize_plate,
)

# The bottom half of a 100x100 frame.
ZONE = LoadingZone(
    points=[
        ZonePoint(x=0.0, y=0.5),
        ZonePoint(x=1.0, y=0.5),
        ZonePoint(x=1.0, y=1.0),
        ZonePoint(x=0.0, y=1.0),
    ]
)
START = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
TRUCK = TrackedObject(
    tracker_id=1, class_id=7, class_name="truck", confidence=0.9, box=(20, 40, 80, 90)
)


def at(seconds: float) -> datetime:
    return START + timedelta(seconds=seconds)


def tick(
    tracker: LoadingTracker,
    seconds: float,
    *,
    truck: bool = True,
    loaded: int = 0,
    unloaded: int = 0,
):
    parked = tracker.parked([TRUCK], 100, 100) if truck else []
    return tracker.update(
        t=seconds, now=at(seconds), parked=parked, loaded=loaded, unloaded=unloaded
    )


def test_parked_means_the_box_meets_the_ground_inside_the_zone() -> None:
    tracker = LoadingTracker(1, ZONE)
    assert tracker.parked([TRUCK], 100, 100) == [TRUCK]
    up_the_road = TrackedObject(2, 7, "truck", 0.9, (20, 5, 80, 45))
    assert tracker.parked([up_the_road], 100, 100) == []
    assert point_in_polygon(0.5, 0.75, [(0, 0.5), (1, 0.5), (1, 1), (0, 1)])
    assert not point_in_polygon(0.5, 0.25, [(0, 0.5), (1, 0.5), (1, 1), (0, 1)])


def test_a_vehicle_must_stand_in_the_zone_before_a_session_opens() -> None:
    tracker = LoadingTracker(1, ZONE, arrive_after=15)
    for second in range(15):
        assert tick(tracker, second) is None
    update = tick(tracker, 15)
    assert update is not None
    assert update.change is SessionChange.OPENED
    # The arrival time is when the vehicle pulled in, not when we became sure of it.
    assert update.session.arrived_at == at(0)
    assert update.session.vehicle_class == "truck"


def test_a_vehicle_driving_through_does_not_open_a_session() -> None:
    tracker = LoadingTracker(1, ZONE, arrive_after=15)
    for second in range(10):
        tick(tracker, second)
    for second in range(10, 40):
        assert tick(tracker, second, truck=False) is None
    assert tracker.session is None


def test_cargo_is_counted_and_the_session_closes_after_a_quiet_minute() -> None:
    tracker = LoadingTracker(1, ZONE, arrive_after=15, depart_after=60)
    for second in range(16):
        tick(tracker, second)
    first = tick(tracker, 20, loaded=2)
    assert first is not None
    assert first.change is SessionChange.UPDATED
    tick(tracker, 25, loaded=1, unloaded=1)
    tick(tracker, 30)

    # The truck leaves at 30 s; nothing closes until a full minute of quiet.
    assert tick(tracker, 89, truck=False) is None
    closed = tick(tracker, 90, truck=False)
    assert closed is not None
    assert closed.change is SessionChange.CLOSED
    session = closed.session
    assert session.status is SessionStatus.COMPLETED
    assert (session.loaded, session.unloaded) == (3, 1)
    assert session.loading_started_at == at(20)
    assert session.loading_finished_at == at(25)
    assert session.departed_at == at(30)
    assert tracker.session is None


def test_a_worker_hiding_the_truck_or_a_break_does_not_split_the_session() -> None:
    tracker = LoadingTracker(1, ZONE, arrive_after=15, depart_after=60)
    for second in range(16):
        tick(tracker, second)
    opened = tracker.session
    assert opened is not None
    # Hidden for 40 s, then seen again: still the same session.
    for second in range(16, 56):
        assert tick(tracker, second, truck=False) is None
    tick(tracker, 56, loaded=1)
    assert tracker.session is not None
    assert tracker.session.id == opened.id


def test_cargo_with_no_vehicle_seen_still_opens_a_session() -> None:
    tracker = LoadingTracker(1, ZONE)
    update = tick(tracker, 3, truck=False, loaded=1)
    assert update is not None
    assert update.change is SessionChange.OPENED
    assert update.session.loaded == 1
    assert update.session.vehicle_class is None


def test_close_ends_the_open_session_at_the_last_activity() -> None:
    tracker = LoadingTracker(1, ZONE, arrive_after=15)
    for second in range(20):
        tick(tracker, second)
    closed = tracker.close(at(25), 25)
    assert closed is not None
    assert closed.change is SessionChange.CLOSED
    assert closed.session.departed_at == at(19)


def _read(text: str, confidence: float = 0.9) -> PlateRead:
    return PlateRead(text=text, confidence=confidence, crop=np.zeros((10, 30, 3), np.uint8))


def test_plates_are_normalized_and_formatted_the_uzbek_way() -> None:
    assert normalize_plate(" 01 a 123-bc ") == "01A123BC"
    assert format_plate("01A123BC") == "01 A 123 BC"
    assert format_plate("01123ABC") == "01 123 ABC"
    assert format_plate("KZ777ABC02") == "KZ777ABC02"
    assert is_uzbek_plate("01A123BC")
    assert not is_uzbek_plate("01A12BC")


def test_plate_votes_settle_on_the_most_frequent_reading() -> None:
    votes = PlateVotes()
    for text in ("01A123BC", "01A123BC", "01A128BC", "01A123BC", "01A123BC", "01A123BC"):
        votes.add(_read(text))
    assert votes.best == "01A123BC"
    assert votes.settled
    # Too short to be a plate.
    assert not PlateVotes().add(_read("01A"))


def test_an_uzbek_reading_outweighs_a_malformed_one() -> None:
    votes = PlateVotes()
    votes.add(_read("01A123B", confidence=0.95))
    votes.add(_read("01A123BC", confidence=0.8))
    assert votes.best == "01A123BC"


def test_plate_reads_reach_the_session_and_stop_once_settled() -> None:
    tracker = LoadingTracker(1, ZONE, arrive_after=15)
    tick(tracker, 0)
    assert tracker.wants_plate(0)
    # Read before the session opened: kept for it.
    assert tracker.add_plate_reads([_read("01A123BC")], 0) is None
    for second in range(1, 16):
        tick(tracker, second)
    assert tracker.session is not None
    assert tracker.session.plate == "01A123BC"
    for second in range(16, 21):
        tick(tracker, second)
        tracker.add_plate_reads([_read("01A123BC")], second)
    assert not tracker.wants_plate(21)


def test_loading_captions_carry_plate_count_and_duration() -> None:
    caption = event_caption(
        "ru",
        EventType.LOADING_COMPLETED,
        "truck",
        "Склад",
        {"plate": "01A123BC", "loaded": 412, "unloaded": 0, "duration_seconds": 1740},
    )
    assert caption.splitlines() == [
        "Погрузка завершена · грузовик",
        "Номер: 01 A 123 BC",
        "Погружено: 412",
        "Время на площадке: 29 мин",
        "Камера: Склад",
    ]
