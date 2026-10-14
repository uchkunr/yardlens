import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.conftest import FakeDetector
from yardlens_api.api.deps import Container
from yardlens_api.core.enums import CameraStatus, EventType
from yardlens_api.db.models import Camera, Event


async def _create_camera(client: httpx.AsyncClient, **overrides: object) -> dict[str, Any]:
    payload: dict[str, object] = {
        "name": "Gate",
        "source_type": "file",
        "url": "videos/clip.mp4",
        "inference_fps": 5,
    } | overrides
    response = await client.post("/api/cameras", json=payload)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_health(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["detector"] == {"model": "yolo26n.pt", "device": "cpu", "loaded": True}


@pytest.mark.usefixtures("video_file")
async def test_camera_crud(client: httpx.AsyncClient) -> None:
    created = await _create_camera(client, enabled=False)
    camera_id = created["id"]
    assert created["runtime"] == {
        "status": "disabled",
        "last_error": None,
        "last_frame_at": None,
        "measured_fps": 0.0,
        "frame_width": None,
        "frame_height": None,
        "visible": {},
        "line_in": 0,
        "line_out": 0,
        "loading": None,
        "activity": "active",
        "target_fps": 0.0,
        "hardware_decoding": False,
        "plate_stream_open": False,
    }
    assert created["plate_url"] is None

    listed = (await client.get("/api/cameras")).json()
    assert [camera["id"] for camera in listed] == [camera_id]

    line = {"x1": 0.0, "y1": 0.5, "x2": 1.0, "y2": 0.5}
    updated = await client.patch(
        f"/api/cameras/{camera_id}", json={"name": "North gate", "line": line}
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "North gate"
    assert updated.json()["line"] == line

    cleared = await client.patch(f"/api/cameras/{camera_id}", json={"line": None})
    assert cleared.json()["line"] is None

    zone = {"points": [{"x": 0.1, "y": 0.5}, {"x": 0.9, "y": 0.5}, {"x": 0.5, "y": 0.95}]}
    zoned = await client.patch(f"/api/cameras/{camera_id}", json={"zone": zone})
    assert zoned.json()["zone"] == zone
    too_few = await client.patch(
        f"/api/cameras/{camera_id}", json={"zone": {"points": zone["points"][:2]}}
    )
    assert too_few.status_code == 422
    unzoned = await client.patch(f"/api/cameras/{camera_id}", json={"zone": None})
    assert unzoned.json()["zone"] is None

    main_stream = "rtsp://10.0.0.5:554/Streaming/Channels/101"
    with_main = await client.patch(f"/api/cameras/{camera_id}", json={"plate_url": main_stream})
    assert with_main.json()["plate_url"] == main_stream
    not_rtsp = await client.patch(
        f"/api/cameras/{camera_id}", json={"plate_url": "http://10.0.0.5/stream"}
    )
    assert not_rtsp.status_code == 422
    assert not_rtsp.json()["detail"].startswith("Main stream: RTSP URL must start with")
    without_main = await client.patch(f"/api/cameras/{camera_id}", json={"plate_url": ""})
    assert without_main.json()["plate_url"] is None

    assert (await client.delete(f"/api/cameras/{camera_id}")).status_code == 204
    assert (await client.get(f"/api/cameras/{camera_id}")).status_code == 404


@pytest.mark.usefixtures("video_file")
async def test_camera_validation_errors(client: httpx.AsyncClient) -> None:
    await _create_camera(client, enabled=False)

    duplicate = await client.post(
        "/api/cameras", json={"name": "Gate", "source_type": "file", "url": "videos/clip.mp4"}
    )
    assert duplicate.status_code == 409

    bad_url = await client.post(
        "/api/cameras", json={"name": "Dock", "source_type": "rtsp", "url": "http://x/y"}
    )
    assert bad_url.status_code == 422
    assert "RTSP URL must start with" in bad_url.json()["detail"]

    bad_line = await client.post(
        "/api/cameras",
        json={
            "name": "Dock",
            "source_type": "file",
            "url": "videos/clip.mp4",
            "line": {"x1": 0.5, "y1": 0.5, "x2": 0.5, "y2": 0.5},
        },
    )
    assert bad_line.status_code == 422


@pytest.mark.usefixtures("video_file")
async def test_camera_connection_test_returns_preview(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/cameras/test", json={"source_type": "file", "url": "videos/clip.mp4"}
    )
    body = response.json()
    assert response.status_code == 200
    assert body["success"] is True
    assert (body["width"], body["height"]) == (160, 120)
    assert body["preview"].startswith("data:image/jpeg;base64,")


async def test_camera_connection_test_reports_failure(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/cameras/test", json={"source_type": "file", "url": "videos/nope.mp4"}
    )
    assert response.status_code == 200
    assert response.json()["success"] is False


@pytest.mark.usefixtures("video_file")
async def test_enabled_camera_streams_and_emits_events(
    client: httpx.AsyncClient, container: Container, detector: FakeDetector
) -> None:
    created = await _create_camera(client)
    camera_id = created["id"]

    async def snapshot_ready() -> httpx.Response:
        for _ in range(100):
            response = await client.get(f"/api/cameras/{camera_id}/snapshot.jpg")
            if response.status_code == 200:
                return response
            await asyncio.sleep(0.1)
        raise AssertionError("no annotated frame within 10s")

    snapshot = await snapshot_ready()
    assert snapshot.headers["content-type"] == "image/jpeg"
    assert snapshot.content.startswith(b"\xff\xd8")
    assert detector.calls > 0

    raw = await client.get(f"/api/cameras/{camera_id}/snapshot.jpg", params={"raw": True})
    assert raw.status_code == 200

    camera = (await client.get(f"/api/cameras/{camera_id}")).json()
    assert camera["runtime"]["status"] == CameraStatus.ONLINE

    types: set[str] = set()
    for _ in range(50):
        events = (await client.get("/api/events", params={"camera_id": camera_id})).json()
        types = {item["type"] for item in events["items"]}
        if {"camera_online", "object_entered"} <= types:
            break
        await asyncio.sleep(0.1)
    else:
        raise AssertionError(f"expected events not recorded: {types}")

    entered = next(item for item in events["items"] if item["type"] == "object_entered")
    assert entered["class_name"] == "car"
    assert entered["snapshot_url"].startswith("/media/snapshots/")
    image = await client.get(entered["snapshot_url"])
    assert image.status_code == 200

    disabled = await client.post(f"/api/cameras/{camera_id}/disable")
    assert disabled.json()["runtime"]["status"] == "disabled"
    assert container.cameras.live(camera_id) is None


async def _insert_events(container: Container, video_file: Path) -> int:
    now = datetime.now(UTC)
    async with container.session_factory() as session:
        camera = Camera(name="Yard", source_type="file", url=str(video_file), enabled=False)
        session.add(camera)
        await session.flush()
        rows = [
            Event(camera_id=camera.id, type="object_entered", class_name="car", created_at=now),
            Event(camera_id=camera.id, type="object_entered", class_name="truck", created_at=now),
            Event(camera_id=camera.id, type="object_entered", class_name="person", created_at=now),
            Event(camera_id=camera.id, type="line_crossed_in", class_name="car", created_at=now),
            Event(camera_id=camera.id, type="line_crossed_out", class_name="car", created_at=now),
            Event(
                camera_id=camera.id,
                type="object_entered",
                class_name="car",
                created_at=now - timedelta(days=2),
            ),
        ]
        session.add_all(rows)
        await session.commit()
        return camera.id


async def test_events_filters_and_pagination(
    client: httpx.AsyncClient, container: Container, video_file: Path
) -> None:
    camera_id = await _insert_events(container, video_file)

    page = (await client.get("/api/events", params={"page_size": 2})).json()
    assert page["total"] == 6
    assert len(page["items"]) == 2
    assert page["items"][0]["camera_name"] == "Yard"

    by_type = (
        await client.get(
            "/api/events",
            params=[("type", EventType.LINE_CROSSED_IN), ("type", "line_crossed_out")],
        )
    ).json()
    assert {item["type"] for item in by_type["items"]} == {"line_crossed_in", "line_crossed_out"}

    by_class = (
        await client.get("/api/events", params={"class_name": "car", "camera_id": camera_id})
    ).json()
    assert by_class["total"] == 4

    since = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    recent = (await client.get("/api/events", params={"since": since})).json()
    assert recent["total"] == 5

    invalid = await client.get(
        "/api/events",
        params={"since": since, "until": (datetime.now(UTC) - timedelta(days=3)).isoformat()},
    )
    assert invalid.status_code == 422


async def test_stats_overview(
    client: httpx.AsyncClient, container: Container, video_file: Path
) -> None:
    await _insert_events(container, video_file)
    stats = (await client.get("/api/stats/overview", params={"tz": "UTC"})).json()
    assert stats["vehicles_today"] == 2
    assert stats["objects_today"] == 3
    assert stats["line_in_today"] == 1
    assert stats["line_out_today"] == 1
    assert stats["cameras_total"] == 1
    assert len(stats["hourly"]) == 24
    assert sum(bucket["entered"] for bucket in stats["hourly"]) == 3
    assert {item["class_name"]: item["count"] for item in stats["per_class"]} == {
        "car": 1,
        "truck": 1,
        "person": 1,
    }

    bad_tz = await client.get("/api/stats/overview", params={"tz": "Mars/Base"})
    assert bad_tz.status_code == 422


async def test_settings_roundtrip_masks_token(client: httpx.AsyncClient) -> None:
    initial = (await client.get("/api/settings")).json()
    assert initial["telegram"]["bot_token_set"] is False
    assert initial["telegram"]["language"] == "ru"
    assert "car" in initial["available_classes"]

    response = await client.put(
        "/api/settings",
        json={
            "detection": {"model": "yolo26n.pt", "confidence": 0.5, "classes": ["car", "truck"]},
            "telegram": {"enabled": True, "bot_token": "123456:ABCDEFG", "chat_id": "42"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["detection"]["classes"] == ["car", "truck"]
    assert body["telegram"]["bot_token_set"] is True
    assert body["telegram"]["bot_token_hint"] == "••••DEFG"
    assert "123456:ABCDEFG" not in response.text

    kept = await client.put("/api/settings", json={"telegram": {"chat_id": "43", "language": "en"}})
    assert kept.json()["telegram"]["bot_token_set"] is True
    assert kept.json()["telegram"]["language"] == "en"

    invalid = await client.put(
        "/api/settings",
        json={"detection": {"model": "yolo26n.pt", "confidence": 0.5, "classes": ["dragon"]}},
    )
    assert invalid.status_code == 422


async def test_telegram_test_requires_credentials(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/settings/telegram/test", json={})
    assert response.json() == {"success": False, "message": "Bot token and chat ID are required"}


async def test_system_resources_and_performance_settings(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/system/resources")
    assert response.status_code == 200
    body = response.json()
    assert body["load"]["level"] == "normal"
    assert body["load"]["cpu_limit_percent"] == 75
    assert body["load"]["adaptive"] is True
    assert body["cpu"]["logical_cpus"] >= 1
    assert body["memory"]["total_bytes"] > 0
    assert body["inference"]["model"] == "yolo26n.pt"
    assert body["cameras"] == []

    settings = (await client.get("/api/settings")).json()
    assert settings["performance"] == {
        "cpu_limit_percent": 75,
        "adaptive_fps": True,
        "idle_fps": 1.0,
    }

    performance = {"cpu_limit_percent": 60, "adaptive_fps": False, "idle_fps": 0.5}
    saved = await client.put("/api/settings", json={"performance": performance})
    assert saved.status_code == 200
    assert saved.json()["performance"] == performance
    load = (await client.get("/api/system/resources")).json()["load"]
    assert load["cpu_limit_percent"] == 60
    assert load["adaptive"] is False

    too_low = await client.put("/api/settings", json={"performance": {"cpu_limit_percent": 10}})
    assert too_low.status_code == 422


@pytest.mark.usefixtures("video_file")
async def test_a_camera_can_have_detection_settings_of_its_own(client: httpx.AsyncClient) -> None:
    camera = await _create_camera(client, enabled=False)
    url = f"/api/cameras/{camera['id']}"
    assert camera["detection"] is None

    own = {"confidence": 0.5, "classes": ["truck", "suitcase"], "cargo_classes": ["suitcase"]}
    saved = await client.patch(url, json={"detection": own})
    assert saved.status_code == 200, saved.text
    assert saved.json()["detection"] == {"model": None, **own}

    for detection, message in (
        ({"classes": ["sack"]}, "Unknown classes: sack"),
        (
            {"classes": ["truck"], "cargo_classes": ["suitcase"]},
            "Cargo classes must be among the detected classes",
        ),
        ({"model": "missing.pt"}, "Unsupported model 'missing.pt'"),
    ):
        rejected = await client.patch(url, json={"detection": detection})
        assert rejected.status_code == 422
        assert rejected.json()["detail"] == message

    cleared = await client.patch(url, json={"detection": None})
    assert cleared.json()["detection"] is None

    classes = await client.get("/api/settings/models/yolo26n.pt/classes")
    assert classes.status_code == 200
    assert "truck" in classes.json()
