import asyncio
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from yardlens_api.api.deps import ContainerDep, SessionDep
from yardlens_api.core.enums import CameraStatus
from yardlens_api.core.errors import NotFoundError, UnavailableError
from yardlens_api.db.models import Camera

router = APIRouter(prefix="/cameras", tags=["streams"])

_BOUNDARY = "yardlensframe"
_POLL_SECONDS = 0.05
_RENDER_WAKE_SECONDS = 2.0
_NO_STORE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


async def _require_camera(session: SessionDep, camera_id: int) -> Camera:
    camera = await session.get(Camera, camera_id)
    if camera is None:
        raise NotFoundError(f"Camera {camera_id} not found")
    return camera


@router.get(
    "/{camera_id}/stream.mjpeg",
    response_class=StreamingResponse,
    responses={200: {"content": {f"multipart/x-mixed-replace; boundary={_BOUNDARY}": {}}}},
)
async def stream_mjpeg(
    camera_id: int, request: Request, container: ContainerDep, session: SessionDep
) -> StreamingResponse:
    """Annotated live feed as MJPEG. Frames are pushed as soon as inference produces them."""
    await _require_camera(session, camera_id)
    await session.close()

    async def frames() -> AsyncIterator[bytes]:
        last_seq = -1
        while not await request.is_disconnected():
            worker = container.cameras.worker(camera_id)
            if worker is None:
                await asyncio.sleep(1.0)
                continue
            seq, jpeg = worker.latest_jpeg()
            if jpeg is None or seq == last_seq:
                await asyncio.sleep(_POLL_SECONDS)
                continue
            last_seq = seq
            yield (
                (
                    f"--{_BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                    f"Content-Length: {len(jpeg)}\r\n\r\n"
                ).encode()
                + jpeg
                + b"\r\n"
            )

    return StreamingResponse(
        frames(),
        media_type=f"multipart/x-mixed-replace; boundary={_BOUNDARY}",
        headers=_NO_STORE,
    )


@router.get(
    "/{camera_id}/snapshot.jpg",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}, 503: {"description": "No frame yet"}},
)
async def snapshot(
    camera_id: int, container: ContainerDep, session: SessionDep, raw: bool = False
) -> Response:
    """Latest annotated frame; ``?raw=true`` returns the unannotated frame (line editor)."""
    await _require_camera(session, camera_id)
    worker = container.cameras.worker(camera_id)
    if worker is None:
        raise UnavailableError("Camera is disabled")
    if raw:
        jpeg = await asyncio.to_thread(worker.latest_raw_jpeg)
    else:
        _, jpeg = worker.latest_jpeg()
        # Rendering pauses while nobody watches; the request above restarts it, so an online
        # camera has a fresh frame within a frame or two.
        deadline = time.monotonic() + _RENDER_WAKE_SECONDS
        while (
            jpeg is None
            and worker.state().status is CameraStatus.ONLINE
            and time.monotonic() < deadline
        ):
            await asyncio.sleep(_POLL_SECONDS)
            _, jpeg = worker.latest_jpeg()
    if jpeg is None:
        raise UnavailableError("No frame received yet")
    return Response(content=jpeg, media_type="image/jpeg", headers=_NO_STORE)
