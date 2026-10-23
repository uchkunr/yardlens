import asyncio
import base64
import logging
import time
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from yardlens_api.api.deps import Container, ContainerDep, SessionDep
from yardlens_api.core.enums import SourceType
from yardlens_api.core.errors import AppError, ConflictError, NotFoundError, SourceError
from yardlens_api.db.models import Camera
from yardlens_api.schemas.camera import (
    CameraCreate,
    CameraRead,
    CameraRuntime,
    CameraTestRequest,
    CameraTestResult,
    CameraUpdate,
    CountingLine,
    LoadingZone,
)
from yardlens_api.schemas.settings import CameraDetection, detection_problem, effective_detection
from yardlens_api.services.camera_worker import CameraConfig, encode_jpeg
from yardlens_api.services.detector import available_models
from yardlens_api.services.events import delete_snapshots_for_camera, remove_snapshot_files
from yardlens_api.services.loading_store import session_snapshot_paths, to_session_read
from yardlens_api.services.sources import open_reader, resolve_source, validate_source

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cameras", tags=["cameras"])


class InvalidSourceError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


HIDDEN = "••••"  # stands in for a camera password in API responses


def mask_url(url: str) -> str:
    """The URL with its password hidden: camera passwords never leave the server."""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    return urlunsplit(parts._replace(netloc=f"{parts.username or ''}:{HIDDEN}@{host}"))


def unmask_url(url: str, stored: str | None) -> str:
    """A masked URL sent back unchanged means "keep the stored one"."""
    return stored if stored and url == mask_url(stored) else url


def _own_detection(camera: Camera) -> CameraDetection | None:
    return CameraDetection.model_validate(camera.detection) if camera.detection else None


def camera_config(camera: Camera) -> CameraConfig:
    return CameraConfig(
        id=camera.id,
        name=camera.name,
        source_type=SourceType(camera.source_type),
        url=camera.url,
        inference_fps=camera.inference_fps,
        line=CountingLine.model_validate(camera.line) if camera.line else None,
        zone=LoadingZone.model_validate(camera.zone) if camera.zone else None,
        plate_url=camera.plate_url,
        detection=_own_detection(camera),
    )


def _sync(container: Container, camera: Camera) -> None:
    """Start, update or stop the camera, then drop models no camera needs any more."""
    container.cameras.sync(camera_config(camera), enabled=camera.enabled)
    general = container.settings_store.detection().model
    container.models.retain(container.cameras.models_in_use() - {general})


async def _validate_detection(container: Container, own: CameraDetection | None) -> None:
    """The camera's own settings must work with its model: known classes, cargo among them."""
    if own is None:
        return
    settings = effective_detection(container.settings_store.detection(), own)
    if own.model is not None and own.model not in available_models(container.settings.models_dir):
        raise InvalidSourceError(f"Unsupported model '{own.model}'")
    if settings.model == container.detector.status.model:
        names = container.detector.class_names
    else:
        try:
            names = (await asyncio.to_thread(container.models.get, settings.model)).class_names
        except Exception as exc:
            logger.exception("Model load failed", extra={"model": settings.model})
            raise InvalidSourceError(f"Could not load model: {exc}") from exc
    problem = detection_problem(settings, names)
    if problem is not None:
        raise InvalidSourceError(problem)


def to_camera_read(camera: Camera, container: Container) -> CameraRead:
    state = container.cameras.runtime(camera.id)
    return CameraRead(
        id=camera.id,
        name=camera.name,
        source_type=SourceType(camera.source_type),
        url=mask_url(camera.url),
        enabled=camera.enabled,
        inference_fps=camera.inference_fps,
        line=CountingLine.model_validate(camera.line) if camera.line else None,
        zone=LoadingZone.model_validate(camera.zone) if camera.zone else None,
        plate_url=mask_url(camera.plate_url) if camera.plate_url else None,
        detection=_own_detection(camera),
        created_at=camera.created_at,
        updated_at=camera.updated_at,
        runtime=CameraRuntime(
            status=state.status,
            last_error=state.last_error,
            last_frame_at=state.last_frame_at,
            measured_fps=state.measured_fps,
            frame_width=state.frame_width,
            frame_height=state.frame_height,
            visible=state.visible,
            line_in=state.line_in,
            line_out=state.line_out,
            loading=to_session_read(state.loading, camera.name) if state.loading else None,
            activity=state.activity,
            target_fps=state.target_fps,
            hardware_decoding=state.hardware_decoding,
            plate_stream_open=state.plate_stream_open,
        ),
    )


async def _get_camera(session: SessionDep, camera_id: int) -> Camera:
    camera = await session.get(Camera, camera_id)
    if camera is None:
        raise NotFoundError(f"Camera {camera_id} not found")
    return camera


def _validate(container: Container, source_type: SourceType, url: str) -> None:
    try:
        validate_source(source_type, url, base_dir=container.settings.data_dir)
    except SourceError as exc:
        raise InvalidSourceError(str(exc)) from exc


def _validate_plate_url(plate_url: str | None) -> str | None:
    """Empty means none; otherwise it has to be an RTSP stream."""
    if not plate_url:
        return None
    try:
        validate_source(SourceType.RTSP, plate_url)
    except SourceError as exc:
        raise InvalidSourceError(f"Main stream: {exc}") from exc
    return plate_url


async def _commit(session: SessionDep) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("A camera with this name already exists") from exc


@router.get("", response_model=list[CameraRead])
async def list_cameras(container: ContainerDep, session: SessionDep) -> list[CameraRead]:
    cameras = (await session.scalars(select(Camera).order_by(Camera.id))).all()
    return [to_camera_read(camera, container) for camera in cameras]


@router.post("", response_model=CameraRead, status_code=status.HTTP_201_CREATED)
async def create_camera(
    payload: CameraCreate, container: ContainerDep, session: SessionDep
) -> CameraRead:
    _validate(container, payload.source_type, payload.url)
    plate_url = _validate_plate_url(payload.plate_url)
    await _validate_detection(container, payload.detection)
    camera = Camera(
        name=payload.name,
        source_type=payload.source_type.value,
        url=payload.url,
        enabled=payload.enabled,
        inference_fps=payload.inference_fps,
        line=payload.line.model_dump() if payload.line else None,
        zone=payload.zone.model_dump() if payload.zone else None,
        plate_url=plate_url,
        detection=payload.detection.model_dump(exclude_none=True) if payload.detection else None,
    )
    session.add(camera)
    await _commit(session)
    await session.refresh(camera)
    _sync(container, camera)
    return to_camera_read(camera, container)


@router.post("/test", response_model=CameraTestResult)
async def test_camera(
    payload: CameraTestRequest, container: ContainerDep, session: SessionDep
) -> CameraTestResult:
    """Try to open the source and grab one frame within the timeout."""
    if payload.camera_id is not None:
        camera = await session.get(Camera, payload.camera_id)
        if camera is not None:
            payload = payload.model_copy(update={"url": unmask_url(payload.url, camera.url)})
    await session.close()
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_grab_preview, container, payload),
            timeout=payload.timeout_seconds + 5.0,
        )
    except TimeoutError:
        result = CameraTestResult(
            success=False, message="Timed out waiting for a frame", elapsed_ms=0
        )
    return result.model_copy(update={"elapsed_ms": int((time.monotonic() - started) * 1000)})


def _grab_preview(container: Container, payload: CameraTestRequest) -> CameraTestResult:
    try:
        source = resolve_source(
            payload.source_type, payload.url, base_dir=container.settings.data_dir
        )
        reader = open_reader(source, timeout=payload.timeout_seconds, snapshot_interval=0.0)
    except SourceError as exc:
        return CameraTestResult(success=False, message=str(exc), elapsed_ms=0)
    except Exception as exc:
        logger.exception("Camera test failed unexpectedly")
        return CameraTestResult(success=False, message=f"Unexpected error: {exc}", elapsed_ms=0)
    try:
        deadline = time.monotonic() + payload.timeout_seconds
        frame = None
        while frame is None and time.monotonic() < deadline:
            frame = reader.read()
    except Exception as exc:
        logger.exception("Camera test failed while reading")
        return CameraTestResult(
            success=False, message=f"Could not read a frame: {exc}", elapsed_ms=0
        )
    finally:
        reader.close()
    if frame is None:
        return CameraTestResult(
            success=False,
            message="Connected, but no frame was received",
            resolved_url=mask_url(source.uri),
            elapsed_ms=0,
        )
    preview = base64.b64encode(encode_jpeg(frame, quality=75, max_width=960)).decode("ascii")
    return CameraTestResult(
        success=True,
        message="Connection successful",
        resolved_url=mask_url(source.uri),
        width=int(frame.shape[1]),
        height=int(frame.shape[0]),
        elapsed_ms=0,
        preview=f"data:image/jpeg;base64,{preview}",
    )


@router.get("/{camera_id}", response_model=CameraRead)
async def get_camera(camera_id: int, container: ContainerDep, session: SessionDep) -> CameraRead:
    return to_camera_read(await _get_camera(session, camera_id), container)


@router.patch("/{camera_id}", response_model=CameraRead)
async def update_camera(
    camera_id: int, payload: CameraUpdate, container: ContainerDep, session: SessionDep
) -> CameraRead:
    camera = await _get_camera(session, camera_id)
    changes = payload.model_dump(exclude_unset=True)
    source_type = SourceType(changes.get("source_type", camera.source_type))
    url = unmask_url(str(changes.get("url", camera.url)), camera.url)
    if "source_type" in changes or "url" in changes:
        _validate(container, source_type, url)

    if payload.name is not None:
        camera.name = payload.name
    if payload.source_type is not None:
        camera.source_type = payload.source_type.value
    if payload.url is not None:
        camera.url = url
    if payload.enabled is not None:
        camera.enabled = payload.enabled
    if payload.inference_fps is not None:
        camera.inference_fps = payload.inference_fps
    if "line" in changes:
        camera.line = payload.line.model_dump() if payload.line else None
    if "zone" in changes:
        camera.zone = payload.zone.model_dump() if payload.zone else None
    if "plate_url" in changes:
        plate_url = payload.plate_url and unmask_url(payload.plate_url, camera.plate_url)
        camera.plate_url = _validate_plate_url(plate_url)
    if "detection" in changes:
        await _validate_detection(container, payload.detection)
        camera.detection = (
            payload.detection.model_dump(exclude_none=True) if payload.detection else None
        )
    await _commit(session)
    await session.refresh(camera)
    _sync(container, camera)
    return to_camera_read(camera, container)


@router.post("/{camera_id}/enable", response_model=CameraRead)
async def enable_camera(camera_id: int, container: ContainerDep, session: SessionDep) -> CameraRead:
    return await _set_enabled(camera_id, enabled=True, container=container, session=session)


@router.post("/{camera_id}/disable", response_model=CameraRead)
async def disable_camera(
    camera_id: int, container: ContainerDep, session: SessionDep
) -> CameraRead:
    return await _set_enabled(camera_id, enabled=False, container=container, session=session)


async def _set_enabled(
    camera_id: int, *, enabled: bool, container: Container, session: SessionDep
) -> CameraRead:
    camera = await _get_camera(session, camera_id)
    camera.enabled = enabled
    await session.commit()
    await session.refresh(camera)
    _sync(container, camera)
    return to_camera_read(camera, container)


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_camera(camera_id: int, container: ContainerDep, session: SessionDep) -> Response:
    camera = await _get_camera(session, camera_id)
    container.cameras.remove(camera_id)
    await delete_snapshots_for_camera(session, camera_id, container.settings.snapshots_dir)
    await remove_snapshot_files(
        container.settings.snapshots_dir, await session_snapshot_paths(session, camera_id)
    )
    await session.delete(camera)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
