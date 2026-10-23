"""Workers: managed from the dashboard (``router``), and the endpoints workers themselves use
with their token (``agent_router``)."""

import asyncio
import contextlib
import hashlib
import logging
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import cast

from fastapi import APIRouter, Header, Response, WebSocket, status
from fastapi.responses import FileResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from yardlens_api.api.deps import Container, ContainerDep, SessionDep
from yardlens_api.api.security import client_address
from yardlens_api.core.errors import AppError, ConflictError, NotFoundError
from yardlens_api.db.models import Worker
from yardlens_api.schemas.settings import ClusterSettings
from yardlens_api.schemas.worker_protocol import (
    CLOSE_PROTOCOL_ERROR,
    CLOSE_UNSUPPORTED,
    PROTOCOL_VERSION,
    WORKER_MESSAGES,
    Hello,
    ProtocolError,
    unpack,
)
from yardlens_api.schemas.workers import (
    ClusterRead,
    LocalProcessingUpdate,
    WorkerCreate,
    WorkerCreated,
    WorkerInfo,
    WorkerRead,
    WorkerUpdate,
)
from yardlens_api.services.cluster import Cluster
from yardlens_api.services.detector import available_models
from yardlens_api.services.remote import RemoteNode
from yardlens_api.services.workers import authenticate, ca_pin, issue_token, worker_commands

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workers", tags=["workers"])
agent_router = APIRouter(prefix="/workers", tags=["workers"])

HELLO_TIMEOUT_SECONDS = 10.0


class UnauthorizedWorkerError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED


def to_worker_read(worker: Worker, cluster: Cluster) -> WorkerRead:
    node = cluster.node(worker.id)
    if node is None:
        return WorkerRead(
            id=worker.id,
            name=worker.name,
            online=False,
            created_at=worker.created_at,
            last_seen_at=worker.last_seen_at,
            address=worker.address,
            version=worker.version,
            info=WorkerInfo.model_validate(worker.info) if worker.info else None,
            clock_offset_seconds=None,
            load=None,
            cameras=cluster.cameras_on(worker.id),
        )
    return WorkerRead(
        id=worker.id,
        name=worker.name,
        online=True,
        created_at=worker.created_at,
        last_seen_at=datetime.now(UTC),
        address=node.address,
        version=node.info.version,
        info=node.info,
        clock_offset_seconds=round(node.clock_offset.total_seconds(), 3),
        load=node.load,
        cameras=cluster.cameras_on(worker.id),
    )


async def _overview(container: Container, session: SessionDep) -> ClusterRead:
    workers = (await session.scalars(select(Worker).order_by(Worker.id))).all()
    cluster = container.cameras
    return ClusterRead(
        local_processing=cluster.local_enabled,
        local_cameras=cluster.cameras_on(None),
        unplaced_cameras=cluster.unplaced(),
        workers=[to_worker_read(worker, cluster) for worker in workers],
    )


async def _get_worker(session: SessionDep, worker_id: int) -> Worker:
    worker = await session.get(Worker, worker_id)
    if worker is None:
        raise NotFoundError(f"Worker {worker_id} not found")
    return worker


@router.get("", response_model=ClusterRead)
async def list_workers(container: ContainerDep, session: SessionDep) -> ClusterRead:
    """Every worker with its load and cameras, and what this server processes itself."""
    return await _overview(container, session)


@router.post("", response_model=WorkerCreated, status_code=status.HTTP_201_CREATED)
async def create_worker(
    payload: WorkerCreate, container: ContainerDep, session: SessionDep
) -> WorkerCreated:
    """Add a worker. The token, and the commands carrying it, are shown this once."""
    worker = Worker(name=payload.name, token_hash="")
    session.add(worker)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("A worker with this name already exists") from exc
    token, digest = issue_token(worker.id)
    worker.token_hash = digest
    await session.commit()
    await session.refresh(worker)
    config = container.settings
    commands = worker_commands(
        server=(config.worker_server_url or config.public_url).rstrip("/"),
        token=token,
        image=config.worker_image,
        version=version("yardlens-api"),
        pin=await asyncio.to_thread(ca_pin, config.ca_cert_path),
    )
    logger.info("Worker added", extra={"worker_id": worker.id, "worker": worker.name})
    return WorkerCreated(
        worker=to_worker_read(worker, container.cameras), token=token, commands=commands
    )


@router.patch("/{worker_id}", response_model=WorkerRead)
async def rename_worker(
    worker_id: int, payload: WorkerUpdate, container: ContainerDep, session: SessionDep
) -> WorkerRead:
    worker = await _get_worker(session, worker_id)
    worker.name = payload.name
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("A worker with this name already exists") from exc
    container.cameras.rename_worker(worker.id, worker.name)
    return to_worker_read(worker, container.cameras)


@router.delete("/{worker_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_worker(worker_id: int, container: ContainerDep, session: SessionDep) -> Response:
    """Remove a worker: its token stops working at once and its cameras move elsewhere."""
    worker = await _get_worker(session, worker_id)
    await session.delete(worker)
    await session.commit()
    await container.cameras.forget_worker(worker_id)
    logger.info("Worker removed", extra={"worker_id": worker_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/rebalance", response_model=ClusterRead)
async def rebalance(container: ContainerDep, session: SessionDep) -> ClusterRead:
    """Spread the automatically placed cameras evenly over the nodes online now. Cameras that
    move start over (tracking, an open loading session carries on)."""
    await container.cameras.rebalance()
    return await _overview(container, session)


@router.put("/local", response_model=ClusterRead)
async def set_local_processing(
    payload: LocalProcessingUpdate, container: ContainerDep, session: SessionDep
) -> ClusterRead:
    """Whether this server processes cameras too, or leaves them all to the workers."""
    await container.settings_store.save_cluster(
        session, ClusterSettings(local_processing=payload.enabled)
    )
    await container.cameras.refresh_placements()
    return await _overview(container, session)


# -- for workers ---------------------------------------------------------------------------------

_digests: dict[tuple[str, int, int], str] = {}


def _sha256(path: Path) -> str:
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    digest = _digests.get(key)
    if digest is None:
        hasher = hashlib.sha256()
        with path.open("rb") as file:
            while chunk := file.read(1024 * 1024):
                hasher.update(chunk)
        digest = _digests[key] = hasher.hexdigest()
    return digest


@agent_router.get(
    "/models/{model}",
    response_class=FileResponse,
    responses={200: {"content": {"application/octet-stream": {}}}},
    include_in_schema=False,
)
async def download_model(
    model: str,
    container: ContainerDep,
    session: SessionDep,
    authorization: str | None = Header(None),
) -> FileResponse:
    """A model's weights for a worker, with their SHA-256 in ``X-Content-SHA256``. 404 for a
    stock model the server has not downloaded: the worker fetches that one itself."""
    if await authenticate(session, authorization) is None:
        raise UnauthorizedWorkerError("Unknown worker token")
    models_dir = container.settings.models_dir
    path = models_dir / model
    if model not in available_models(models_dir) or not path.is_file():
        raise NotFoundError(f"Model {model} not found")
    digest = await asyncio.to_thread(_sha256, path)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=model,
        headers={"X-Content-SHA256": digest},
    )


@agent_router.get(
    "/ca.pem",
    response_class=Response,
    responses={200: {"content": {"application/x-pem-file": {}}}},
)
async def certificate_authority(container: ContainerDep) -> Response:
    """The certificate authority behind this server's HTTPS. Public, like any CA certificate:
    workers check it against the fingerprint in their command; people install it so browsers
    trust the dashboard."""
    path = container.settings.ca_cert_path
    if path is None or not path.is_file():
        raise NotFoundError("This server has no certificate authority of its own")
    return Response(
        content=await asyncio.to_thread(path.read_bytes),
        media_type="application/x-pem-file",
        headers={"Content-Disposition": 'attachment; filename="yardlens-ca.pem"'},
    )


@agent_router.websocket("/connect")
async def connect(websocket: WebSocket) -> None:
    """The worker channel (see ``schemas/worker_protocol.py``)."""
    container = cast("Container", websocket.app.state.container)
    async with container.session_factory() as session:
        worker = await authenticate(session, websocket.headers.get("authorization"))
    if worker is None:
        logger.warning(
            "Worker refused: unknown token", extra={"address": client_address(websocket)}
        )
        await websocket.close()  # before accepting: the handshake fails with 403
        return
    await websocket.accept()
    try:
        received = await asyncio.wait_for(websocket.receive(), HELLO_TIMEOUT_SECONDS)
        frame = received.get("text") or received.get("bytes")
        if frame is None:
            raise ProtocolError("No hello")
        hello, _ = unpack(WORKER_MESSAGES, frame)
        if not isinstance(hello, Hello):
            raise ProtocolError("The first message must be hello")
    except (TimeoutError, ProtocolError) as exc:
        logger.warning("Worker sent no valid hello", extra={"worker_id": worker.id})
        with contextlib.suppress(RuntimeError):  # it may have gone already
            await websocket.close(code=CLOSE_PROTOCOL_ERROR, reason=str(exc)[:120])
        return
    if hello.protocol != PROTOCOL_VERSION:
        await websocket.close(
            code=CLOSE_UNSUPPORTED,
            reason=f"Server speaks protocol {PROTOCOL_VERSION}; use the same release",
        )
        return

    address = client_address(websocket)
    async with container.session_factory() as session:
        await session.execute(
            update(Worker)
            .where(Worker.id == worker.id)
            .values(
                address=address,
                version=hello.info.version,
                info=hello.info.model_dump(mode="json"),
                last_seen_at=datetime.now(UTC),
            )
        )
        await session.commit()
    node = RemoteNode(
        worker_id=worker.id, name=worker.name, websocket=websocket, hello=hello, address=address
    )
    cluster = container.cameras
    try:
        await cluster.attach(node)
        await node.run(lambda message, files: cluster.handle(node, message, files))
    finally:
        await cluster.detach(node)
