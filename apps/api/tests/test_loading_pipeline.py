"""End to end: a worker sees a parked truck and cargo crossing the line; the API reports it."""

import asyncio
from typing import Any

import httpx
import pytest
import supervision as sv

from tests.conftest import FakeDetector, make_detections
from yardlens_api.core.config import Settings

TRUCK_BOX = (10.0, 70.0, 70.0, 118.0)  # bottom-left; its bottom edge lies in the zone
SACK_STEPS = (10, 20, 30, 40, 50, 60, 70)  # top edge of a 20 px box sliding down past y = 60
FIRST_SACK_CALL = 5
SACKS = 3
TRUCK_LEAVES_AFTER_CALL = 40


def _loading_scene(call: int) -> sv.Detections:
    boxes: list[tuple[float, float, float, float]] = []
    names: list[str] = []
    if call <= TRUCK_LEAVES_AFTER_CALL:
        boxes.append(TRUCK_BOX)
        names.append("truck")
        step = call - FIRST_SACK_CALL
        if 0 <= step < SACKS * len(SACK_STEPS):
            top = SACK_STEPS[step % len(SACK_STEPS)]
            boxes.append((110.0, float(top), 140.0, float(top + 20)))
            names.append("suitcase")
    return make_detections(boxes, names)


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"loading_arrive_seconds": 1.0, "loading_depart_seconds": 1.5}
    )


@pytest.fixture
def detector() -> FakeDetector:
    return FakeDetector(_loading_scene)


@pytest.mark.usefixtures("video_file")
async def test_a_loaded_truck_becomes_one_completed_session(client: httpx.AsyncClient) -> None:
    detection = {
        "model": "yolo26n.pt",
        "confidence": 0.3,
        "classes": ["truck", "suitcase"],
        "cargo_classes": ["suitcase"],
    }
    assert (await client.put("/api/settings", json={"detection": detection})).status_code == 200
    created = await client.post(
        "/api/cameras",
        json={
            "name": "Склад",
            "source_type": "file",
            "url": "videos/clip.mp4",
            "inference_fps": 15,
            "line": {"x1": 0.0, "y1": 0.5, "x2": 1.0, "y2": 0.5},
            "zone": {
                "points": [
                    {"x": 0.0, "y": 0.75},
                    {"x": 0.6, "y": 0.75},
                    {"x": 0.6, "y": 1.0},
                    {"x": 0.0, "y": 1.0},
                ]
            },
        },
    )
    assert created.status_code == 201, created.text

    page: dict[str, Any] = {}
    for _ in range(300):
        page = (await client.get("/api/loading/sessions")).json()
        if page["total"] and page["items"][0]["status"] == "completed":
            break
        await asyncio.sleep(0.1)
    assert page["total"] == 1, page
    session = page["items"][0]
    assert session["status"] == "completed"
    assert session["vehicle_class"] == "truck"
    assert session["loaded"] + session["unloaded"] == SACKS
    assert session["vehicle_snapshot_url"] is not None

    # Events are batched separately from sessions, so they may land a moment later.
    events: list[dict[str, Any]] = []
    for _ in range(50):
        events = (await client.get("/api/events", params={"page_size": 200})).json()["items"]
        if any(event["type"] == "loading_completed" for event in events):
            break
        await asyncio.sleep(0.1)
    types = {event["type"] for event in events}
    assert {"vehicle_arrived", "loading_completed"} <= types
    # Cargo is tallied in the session, not reported one item at a time.
    assert not [event for event in events if event["class_name"] == "suitcase"]
    completed = next(event for event in events if event["type"] == "loading_completed")
    assert completed["details"]["session_id"] == session["id"]
