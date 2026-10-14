"""Where each camera runs: on this server or on a worker.

Routes talk to the cluster, never to a node. The local node is the camera manager inside the API
process; workers connect over a WebSocket (``RemoteNode``) and run the same camera code on their
own machines. A camera runs on exactly one node at a time:

* a camera pinned to a worker runs there, and waits while that worker is away;
* video files run on this server, since their paths are its paths;
* the rest are placed on the least-loaded node when they start, and stay put. Every move resets
  tracking, so cameras move only when their node is gone for good (a worker away for longer than
  the lease, this server's processing turned off) or when asked to (``rebalance``).

Placements are saved, so a restart moves nothing. Each has an epoch, new with every move: reports
from a worker under an older epoch (it lost the camera while cut off) are ignored. A worker cut
off from the server stops its cameras after ``WORKER_LEASE_SECONDS``, and the server hands them to
other nodes only after the longer ``SERVER_LEASE_SECONDS``, so no camera is processed twice.
"""

import asyncio
import logging
import re
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import Enum, auto
from functools import partial
from pathlib import Path
from typing import Literal, Protocol

from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yardlens_api.core.enums import CameraStatus, SourceType
from yardlens_api.db.models import CameraAssignment, Worker
from yardlens_api.schemas.worker_protocol import (
    CLOSE_REPLACED,
    CLOSE_UNAUTHORIZED,
    MAX_FILE_BYTES,
    Assign,
    Assignment,
    EventReport,
    Hello,
    LiveFrame,
    NodeSettings,
    Pong,
    RawFrame,
    Resources,
    SessionReport,
    SettingsUpdate,
    States,
    Status,
    Welcome,
    WorkerMessage,
)
from yardlens_api.services.camera_manager import CameraManager
from yardlens_api.services.camera_worker import CameraConfig, CameraWorker, RuntimeState
from yardlens_api.services.event_recorder import EventRecorder
from yardlens_api.services.loading import SessionState
from yardlens_api.services.loading_store import InterruptedSession, resumable_session
from yardlens_api.services.remote import RemoteNode
from yardlens_api.services.settings_store import SettingsStore

logger = logging.getLogger(__name__)

WORKER_LEASE_SECONDS = 60.0
SERVER_LEASE_SECONDS = 90.0
CLOSE_SERVER_RESTART = 1012

# Shown as the camera's error; the dashboard translates them.
WORKER_OFFLINE = "The worker processing this camera is offline"
NO_NODE = "No worker is available to process this camera"
FILE_NEEDS_SERVER = "Video files are processed on this server, and its processing is off"

# Photos a worker may send, by where they go under the snapshots directory.
_EVENT_IMAGE = re.compile(r"\d{4}/\d{2}/\d{2}/cam(?P<camera>\d+)-\d{6}-[0-9a-f]{8}\.jpg")
_SESSION_IMAGE = re.compile(
    r"sessions/\d{4}/\d{2}/\d{2}/(?P<session>[0-9a-f]{32})-(?:vehicle|plate)\.jpg"
)
_SESSION_ID = re.compile(r"[0-9a-f]{32}")


class LiveCamera(Protocol):
    """A running camera as the streaming routes see it, wherever it runs."""

    def latest_jpeg(self) -> tuple[int, bytes | None]:
        """The newest annotated frame; asking keeps the live view rendering."""
        ...

    async def raw_jpeg(self) -> bytes | None:
        """The newest frame without annotations (zone and line editors)."""
        ...

    def state(self) -> RuntimeState: ...


class _LocalLive:
    def __init__(self, worker: CameraWorker) -> None:
        self._worker = worker

    def latest_jpeg(self) -> tuple[int, bytes | None]:
        return self._worker.latest_jpeg()

    async def raw_jpeg(self) -> bytes | None:
        return await asyncio.to_thread(self._worker.latest_raw_jpeg)

    def state(self) -> RuntimeState:
        return self._worker.state()


class _RemoteLive:
    def __init__(self, node: RemoteNode, camera_id: int, state: Callable[[], RuntimeState]):
        self._node = node
        self._camera_id = camera_id
        self._state = state

    def latest_jpeg(self) -> tuple[int, bytes | None]:
        self._node.viewed(self._camera_id)
        return self._node.frame(self._camera_id)

    async def raw_jpeg(self) -> bytes | None:
        return await self._node.raw(self._camera_id)

    def state(self) -> RuntimeState:
        return self._state()


@dataclass(frozen=True, slots=True)
class Placement:
    worker_id: int | None
    """None: this server."""
    epoch: int


@dataclass(frozen=True, slots=True)
class RunningCamera:
    config: CameraConfig
    state: RuntimeState
    worker_id: int | None


@dataclass(slots=True)
class _Camera:
    config: CameraConfig
    enabled: bool
    pin: int | None


class _Nowhere(Enum):
    NOWHERE = auto()


NOWHERE = _Nowhere.NOWHERE
LOCAL = None
type Target = int | Literal[_Nowhere.NOWHERE] | None


@dataclass(frozen=True, slots=True)
class _Move:
    camera_id: int
    old: Placement | None
    new: Placement | None


def _state(status: CameraStatus, error: str | None = None) -> RuntimeState:
    return RuntimeState(
        status=status,
        last_error=error,
        last_frame_at=None,
        measured_fps=0.0,
        frame_width=None,
        frame_height=None,
    )


_DISABLED = _state(CameraStatus.DISABLED)
_CONNECTING = _state(CameraStatus.CONNECTING)


def _shifted(value: datetime | None, by: timedelta) -> datetime | None:
    return value + by if value is not None else None


def shift_session(state: SessionState, by: timedelta) -> SessionState:
    """The session with its times moved by ``by`` (from one node's clock to another's)."""
    if not by:
        return state
    return replace(
        state,
        arrived_at=state.arrived_at + by,
        departed_at=_shifted(state.departed_at, by),
        loading_started_at=_shifted(state.loading_started_at, by),
        loading_finished_at=_shifted(state.loading_finished_at, by),
    )


def _shift_state(state: RuntimeState, by: timedelta) -> RuntimeState:
    if not by:
        return state
    return replace(
        state,
        last_frame_at=_shifted(state.last_frame_at, by),
        loading=shift_session(state.loading, by) if state.loading else None,
    )


class Cluster:
    def __init__(
        self,
        local: CameraManager,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        recorder: EventRecorder,
        settings_store: SettingsStore,
        snapshots_dir: Path,
        names: dict[int, str],
        local_available: bool = True,
        local_capacity: Callable[[], float] = lambda: 1.0,
        save_images: Callable[[], bool] = lambda: True,
        lease_seconds: float = SERVER_LEASE_SECONDS,
    ) -> None:
        self.local = local
        self._session_factory = session_factory
        self._recorder = recorder
        self._settings = settings_store
        self._snapshots_dir = snapshots_dir
        self._names = names
        self._local_available = local_available
        self._local_capacity = local_capacity
        self._save_images = save_images
        self.lease_seconds = lease_seconds
        self._cameras: dict[int, _Camera] = {}
        self._placements: dict[int, Placement] = {}
        self._nodes: dict[int, RemoteNode] = {}
        self._worker_names: dict[int, str] = {}
        self._offline_since: dict[int, float] = {}
        # Loading sessions to carry on, found at startup or handed over by this server.
        self._resume: dict[int, InterruptedSession] = {}
        # Cameras a worker stopped for good (disabled): it still reports their sessions closing.
        self._retired: dict[int, Placement] = {}
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self._epoch = 0

    # -- lifecycle -------------------------------------------------------------------------------

    async def start(
        self,
        cameras: Iterable[tuple[CameraConfig, bool, int | None]],
        resumable: dict[int, InterruptedSession],
    ) -> None:
        """Load saved placements and start every camera. Workers that ran cameras before the
        restart get the lease to come back before their cameras move."""
        async with self._session_factory() as session:
            workers = (await session.execute(select(Worker.id, Worker.name))).all()
            rows = (await session.scalars(select(CameraAssignment))).all()
        self._worker_names = dict(workers)
        for row in rows:
            self._placements[row.camera_id] = Placement(row.worker_id, row.epoch)
            self._epoch = max(self._epoch, row.epoch)
        now = time.monotonic()
        for worker_id in {p.worker_id for p in self._placements.values()}:
            if worker_id is not None:
                self._mark_offline(worker_id, now)
        for config, enabled, pin in cameras:
            self._cameras[config.id] = _Camera(config, enabled, pin)
            self._names[config.id] = config.name
        self._resume = dict(resumable)
        await self._reconcile(set(self._cameras) | set(self._placements))

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for node in list(self._nodes.values()):
            # Workers keep their cameras running and reconnect when the server is back.
            node.close(CLOSE_SERVER_RESTART, "Server restarting")
        await self.local.shutdown()

    # -- cameras (routes) ------------------------------------------------------------------------

    async def sync(
        self,
        config: CameraConfig,
        *,
        enabled: bool,
        pin: int | None = None,
        resume: InterruptedSession | None = None,
    ) -> None:
        """Bring the camera in line with its configuration: start, update, move or stop it."""
        self._cameras[config.id] = _Camera(config, enabled, pin)
        self._names[config.id] = config.name
        if resume is not None:
            self._resume[config.id] = resume
        await self._reconcile({config.id})

    async def remove(self, camera_id: int) -> None:
        self._cameras.pop(camera_id, None)
        await self._reconcile({camera_id})
        self._names.pop(camera_id, None)

    async def rebalance(self) -> None:
        """Spread the automatically placed cameras evenly over the nodes there are now."""
        await self._reconcile(set(self._cameras), rebalance=True)

    async def refresh_placements(self) -> None:
        """Re-place cameras after this server's processing was turned on or off."""
        await self._reconcile(set(self._cameras) | set(self._placements))

    def push_settings(self) -> None:
        """Send the detection and performance settings to every worker (after a change)."""
        message = SettingsUpdate(settings=self._node_settings())
        for node in self._nodes.values():
            node.send(message)

    # -- queries ---------------------------------------------------------------------------------

    @property
    def local_enabled(self) -> bool:
        return self._local_available and self._settings.cluster().local_processing

    def placement(self, camera_id: int) -> Placement | None:
        return self._placements.get(camera_id)

    def worker_name(self, worker_id: int) -> str | None:
        return self._worker_names.get(worker_id)

    def node(self, worker_id: int) -> RemoteNode | None:
        return self._nodes.get(worker_id)

    def cameras_on(self, worker_id: int | None) -> list[int]:
        return sorted(
            camera_id
            for camera_id, placement in self._placements.items()
            if placement.worker_id == worker_id
        )

    def unplaced(self) -> list[int]:
        return sorted(
            camera_id
            for camera_id, camera in self._cameras.items()
            if camera.enabled and camera_id not in self._placements
        )

    def runtime(self, camera_id: int) -> RuntimeState:
        camera = self._cameras.get(camera_id)
        if camera is None or not camera.enabled:
            return _DISABLED
        placement = self._placements.get(camera_id)
        if placement is None:
            file = camera.config.source_type is SourceType.FILE
            return _state(CameraStatus.OFFLINE, FILE_NEEDS_SERVER if file else NO_NODE)
        if placement.worker_id is None:
            return self.local.runtime(camera_id)
        node = self._nodes.get(placement.worker_id)
        if node is None:
            return _state(CameraStatus.OFFLINE, WORKER_OFFLINE)
        return node.states.get(camera_id, _CONNECTING)

    def live(self, camera_id: int) -> LiveCamera | None:
        placement = self._placements.get(camera_id)
        if placement is None:
            return None
        if placement.worker_id is None:
            worker = self.local.worker(camera_id)
            return _LocalLive(worker) if worker is not None else None
        node = self._nodes.get(placement.worker_id)
        if node is None:
            return None
        return _RemoteLive(node, camera_id, partial(self.runtime, camera_id))

    def running(self) -> Sequence[RunningCamera]:
        result = [
            RunningCamera(config=worker.config, state=worker.state(), worker_id=LOCAL)
            for worker in self.local.workers()
        ]
        for camera_id, placement in self._placements.items():
            if placement.worker_id is None:
                continue
            node = self._nodes.get(placement.worker_id)
            camera = self._cameras.get(camera_id)
            state = node.states.get(camera_id) if node is not None else None
            if camera is not None and state is not None:
                result.append(RunningCamera(camera.config, state, placement.worker_id))
        return result

    def online_count(self) -> int:
        return sum(
            1
            for camera_id, camera in self._cameras.items()
            if camera.enabled and self.runtime(camera_id).status is CameraStatus.ONLINE
        )

    def models_in_use(self) -> set[str]:
        """Models that cameras on this server name as their own."""
        return self.local.models_in_use()

    # -- workers ---------------------------------------------------------------------------------

    async def attach(self, node: RemoteNode) -> None:
        """A worker connected: greet it and give it its cameras, and any waiting for a node."""
        previous = self._nodes.get(node.worker_id)
        if previous is not None:
            previous.close(CLOSE_REPLACED, "Replaced by a newer connection")
        self._nodes[node.worker_id] = node
        self._offline_since.pop(node.worker_id, None)
        self._worker_names[node.worker_id] = node.name
        node.send(Welcome(worker_id=node.worker_id, name=node.name, settings=self._node_settings()))
        logger.info(
            "Worker connected",
            extra={"worker_id": node.worker_id, "worker": node.name, "address": node.address},
        )
        await self._reconcile(set(self._cameras), notify={node.worker_id})

    async def detach(self, node: RemoteNode) -> None:
        """A worker's connection ended. Its cameras wait for it until the lease runs out."""
        if self._nodes.get(node.worker_id) is not node:
            return  # replaced by a newer connection, or removed
        del self._nodes[node.worker_id]
        self._mark_offline(node.worker_id, time.monotonic())
        logger.warning("Worker disconnected", extra={"worker_id": node.worker_id})
        for camera_id in self.cameras_on(node.worker_id):
            self._publish_status(camera_id)
        try:
            async with self._session_factory() as session:
                await session.execute(
                    update(Worker)
                    .where(Worker.id == node.worker_id)
                    .values(last_seen_at=datetime.now(UTC))
                )
                await session.commit()
        except SQLAlchemyError, OSError:
            logger.warning("Could not record when the worker left", exc_info=True)

    async def forget_worker(self, worker_id: int) -> None:
        """The worker was removed: disconnect it and move its cameras."""
        node = self._nodes.pop(worker_id, None)
        if node is not None:
            node.close(CLOSE_UNAUTHORIZED, "Worker removed")
        self._offline_since.pop(worker_id, None)
        self._worker_names.pop(worker_id, None)
        affected = set(self.cameras_on(worker_id))
        for camera_id, camera in self._cameras.items():
            if camera.pin == worker_id:
                camera.pin = None
                affected.add(camera_id)
        await self._reconcile(affected)

    def rename_worker(self, worker_id: int, name: str) -> None:
        self._worker_names[worker_id] = name
        node = self._nodes.get(worker_id)
        if node is not None:
            node.name = name

    def _mark_offline(self, worker_id: int, since: float) -> None:
        self._offline_since[worker_id] = since
        task = asyncio.create_task(self._expire(worker_id, since), name=f"lease-{worker_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _expire(self, worker_id: int, since: float) -> None:
        await asyncio.sleep(self.lease_seconds)
        if self._offline_since.get(worker_id) != since:
            return  # it came back (or was removed) in the meantime
        del self._offline_since[worker_id]
        moving = self.cameras_on(worker_id)
        if moving:
            logger.warning(
                "Worker away too long; moving its cameras",
                extra={"worker_id": worker_id, "cameras": moving},
            )
        await self._reconcile(set(moving))

    def _node_settings(self) -> NodeSettings:
        return NodeSettings(
            detection=self._settings.detection(), performance=self._settings.performance()
        )

    # -- placement -------------------------------------------------------------------------------

    def _next_epoch(self) -> int:
        # Microseconds since the epoch, so they keep growing across restarts too.
        self._epoch = max(self._epoch + 1, time.time_ns() // 1000)
        return self._epoch

    def _usable(self, worker_id: int | None) -> bool:
        if worker_id is None:
            return self.local_enabled
        if worker_id in self._nodes:
            return True
        since = self._offline_since.get(worker_id)
        return since is not None and time.monotonic() - since < self.lease_seconds

    def _capacity(self, worker_id: int | None) -> float:
        if worker_id is None:
            return self._local_capacity()
        info = self._nodes[worker_id].info
        cores = info.cpu_limit or info.physical_cores
        return max(0.5, cores) * (4.0 if info.device.startswith("cuda") else 1.0)

    def _least_loaded(self, counts: Counter[int | None], current: Placement | None) -> Target:
        candidates: list[int | None] = [LOCAL] if self.local_enabled else []
        candidates += sorted(self._nodes)
        if not candidates:
            return NOWHERE
        here = current.worker_id if current is not None else NOWHERE

        def score(node: int | None) -> tuple[float, bool, bool, int]:
            load = counts[node] - (1 if node == here else 0)
            # Ties: stay where it is, then prefer workers (this server also runs the rest).
            return ((load + 1) / self._capacity(node), node != here, node is None, node or 0)

        return min(candidates, key=score)

    def _target(
        self,
        camera: _Camera | None,
        current: Placement | None,
        counts: Counter[int | None],
        *,
        rebalance: bool,
    ) -> Target:
        if camera is None or not camera.enabled:
            return NOWHERE
        if camera.config.source_type is SourceType.FILE:
            return LOCAL if self.local_enabled else NOWHERE
        if camera.pin is not None:
            return camera.pin
        if current is not None and not rebalance and self._usable(current.worker_id):
            return current.worker_id
        return self._least_loaded(counts, current)

    async def _reconcile(
        self, camera_ids: set[int], *, rebalance: bool = False, notify: Iterable[int] = ()
    ) -> None:
        """Place ``camera_ids`` where they belong now and apply what changed. Workers in
        ``notify`` get their camera list even when nothing of theirs changed."""
        async with self._lock:
            counts = Counter(placement.worker_id for placement in self._placements.values())
            moves: list[_Move] = []
            stay: list[int] = []
            for camera_id in sorted(camera_ids):
                camera = self._cameras.get(camera_id)
                current = self._placements.get(camera_id)
                target = self._target(camera, current, counts, rebalance=rebalance)
                if target is NOWHERE:
                    if current is not None:
                        moves.append(_Move(camera_id, current, None))
                        counts[current.worker_id] -= 1
                    continue
                if current is not None and current.worker_id == target:
                    stay.append(camera_id)
                    continue
                new = Placement(target, self._next_epoch())
                moves.append(_Move(camera_id, current, new))
                counts[target] += 1
                if current is not None:
                    counts[current.worker_id] -= 1

            await self._save(moves)
            touched: set[int] = set(notify)
            for camera_id in stay:
                self._update(camera_id, touched)
            for move in moves:
                await self._apply(move, touched)
            for worker_id in sorted(touched):
                await self._send_assign(worker_id)
            for move in moves:
                if move.new is None or move.new.worker_id is not None:
                    self._publish_status(move.camera_id)

    def _update(self, camera_id: int, touched: set[int]) -> None:
        """The camera stays on its node: pass its configuration on if it changed."""
        camera = self._cameras[camera_id]
        worker_id = self._placements[camera_id].worker_id
        if worker_id is None:
            worker = self.local.worker(camera_id)
            if worker is None or worker.config != camera.config:
                resume = self._resume.pop(camera_id, None)
                self.local.sync(camera.config, enabled=True, resume=resume)
            return
        node = self._nodes.get(worker_id)
        if node is not None and node.configs.get(camera_id) != camera.config:
            touched.add(worker_id)

    async def _apply(self, move: _Move, touched: set[int]) -> None:
        camera = self._cameras.get(move.camera_id)
        final = camera is None or not camera.enabled
        old, new = move.old, move.new
        if old is not None:
            if old.worker_id is None:
                if camera is None:
                    self.local.remove(move.camera_id)
                elif final:
                    self.local.sync(camera.config, enabled=False)
                else:
                    handoff = self.local.release(move.camera_id)
                    if handoff is not None:
                        self._resume[move.camera_id] = handoff
            else:
                node = self._nodes.get(old.worker_id)
                if node is not None:
                    node.states.pop(move.camera_id, None)
                    if final:
                        node.stop_for_good.add(move.camera_id)
                        self._retired[move.camera_id] = old
                    touched.add(old.worker_id)
        if new is None:
            self._placements.pop(move.camera_id, None)
            return
        self._placements[move.camera_id] = new
        self._retired.pop(move.camera_id, None)
        assert camera is not None
        logger.info(
            "Camera placed",
            extra={
                "camera_id": move.camera_id,
                "worker_id": new.worker_id,
                "from_worker_id": old.worker_id if old else None,
            },
        )
        if new.worker_id is None:
            resume = await self._take_resume(move.camera_id)
            self.local.sync(camera.config, enabled=True, resume=resume)
        elif new.worker_id in self._nodes:
            touched.add(new.worker_id)

    async def _take_resume(self, camera_id: int) -> InterruptedSession | None:
        """A session to carry on: one handed over or found at startup, else the camera's open
        session in the database (it ran on a worker)."""
        resume = self._resume.pop(camera_id, None)
        if resume is not None:
            return resume
        try:
            async with self._session_factory() as session:
                return await resumable_session(session, camera_id)
        except SQLAlchemyError, OSError:
            logger.warning("Could not look up a session to resume", exc_info=True)
            return None

    async def _send_assign(self, worker_id: int) -> None:
        node = self._nodes.get(worker_id)
        if node is None:
            return
        assignments: list[Assignment] = []
        for camera_id in self.cameras_on(worker_id):
            camera = self._cameras.get(camera_id)
            if camera is None:
                continue
            assignment = Assignment(config=camera.config, epoch=self._placements[camera_id].epoch)
            if camera_id not in node.running:
                resume = await self._take_resume(camera_id)
                if resume is not None:
                    # In the worker's clock, which it sends back corrected.
                    assignment.resume = shift_session(resume.state, -node.correction)
                    assignment.resume_seen = resume.last_seen - node.correction
            assignments.append(assignment)
        node.send(Assign(cameras=assignments, stop=sorted(node.stop_for_good)))
        node.stop_for_good.clear()
        node.running = {item.config.id: item.epoch for item in assignments}
        node.configs = {item.config.id: item.config for item in assignments}

    async def _save(self, moves: list[_Move]) -> None:
        if not moves:
            return
        try:
            async with self._session_factory() as session:
                for move in moves:
                    await session.execute(
                        delete(CameraAssignment).where(CameraAssignment.camera_id == move.camera_id)
                    )
                    if move.new is not None and move.camera_id in self._cameras:
                        session.add(
                            CameraAssignment(
                                camera_id=move.camera_id,
                                worker_id=move.new.worker_id,
                                epoch=move.new.epoch,
                            )
                        )
                await session.commit()
        except SQLAlchemyError, OSError:
            # Placements still apply; only a restart would place these cameras afresh.
            logger.warning("Could not save camera placements", exc_info=True)

    def _publish_status(self, camera_id: int) -> None:
        state = self.runtime(camera_id)
        self._recorder.publish_status(camera_id, state.status, state.last_error)

    # -- worker reports --------------------------------------------------------------------------

    def _owns(self, node: RemoteNode, camera_id: int, epoch: int) -> bool:
        placement = self._placements.get(camera_id)
        return (
            placement is not None
            and placement.worker_id == node.worker_id
            and placement.epoch == epoch
        )

    async def handle(self, node: RemoteNode, message: WorkerMessage, files: list[bytes]) -> None:
        """One message from a worker."""
        match message:
            case States(cameras=reports):
                node.running = {report.camera_id: report.epoch for report in reports}
                for report in reports:
                    if self._owns(node, report.camera_id, report.epoch):
                        node.states[report.camera_id] = _shift_state(report.state, node.correction)
            case Status(camera_id=camera_id, epoch=epoch):
                if self._owns(node, camera_id, epoch):
                    previous = node.states.get(camera_id, _CONNECTING)
                    node.states[camera_id] = replace(
                        previous, status=message.status, last_error=message.last_error
                    )
                    self._recorder.publish_status(camera_id, message.status, message.last_error)
            case Resources(load=load):
                node.load = load
            case EventReport():
                await self._event(node, message, files)
            case SessionReport():
                await self._session(node, message, files)
            case LiveFrame(camera_id=camera_id, seq=seq):
                placement = self._placements.get(camera_id)
                if files and placement is not None and placement.worker_id == node.worker_id:
                    node.put_frame(camera_id, seq, files[0])
            case RawFrame(request_id=request_id):
                node.put_raw(request_id, files[0] if files else None)
            case Pong():
                node.pong(message)
            case Hello():
                pass  # only valid as the first message, handled on connecting

    async def _event(self, node: RemoteNode, message: EventReport, files: list[bytes]) -> None:
        event = message.event
        if event.camera_id not in self._cameras:
            node.ack(message.seq)  # the camera was deleted meanwhile
            return
        # Events are kept even from a worker that has lost the camera since: they happened.
        stored: str | None = None
        path = event.snapshot_path
        if path is not None and files:
            match = _EVENT_IMAGE.fullmatch(path)
            if match is not None and int(match["camera"]) == event.camera_id:
                stored = path if await self._write_photo(path, files[0]) else None
            else:
                logger.warning(
                    "Refused a photo from a worker",
                    extra={"worker_id": node.worker_id, "path": path[:200]},
                )
        event = replace(
            event, occurred_at=event.occurred_at + node.correction, snapshot_path=stored
        )
        self._recorder.submit(event, on_saved=partial(node.ack, message.seq))

    async def _session(self, node: RemoteNode, message: SessionReport, files: list[bytes]) -> None:
        state = message.session
        retired = self._retired.get(state.camera_id)
        closing = retired == Placement(node.worker_id, message.epoch)
        if not (self._owns(node, state.camera_id, message.epoch) or closing) or not (
            _SESSION_ID.fullmatch(state.id)
        ):
            # From before the camera moved: the node that has it now carries the session on.
            node.ack(message.seq)
            return
        claimed = {state.vehicle_snapshot_path, state.plate_snapshot_path} - {None}
        for path, data in zip(message.images, files, strict=False):
            match = _SESSION_IMAGE.fullmatch(path)
            if path in claimed and match is not None and match["session"] == state.id:
                await self._write_photo(path, data)
        # A photo the server does not have is left out; the saved row keeps the one it has.
        vehicle, plate = await asyncio.gather(
            self._existing(state.vehicle_snapshot_path), self._existing(state.plate_snapshot_path)
        )
        state = shift_session(
            replace(state, vehicle_snapshot_path=vehicle, plate_snapshot_path=plate),
            node.correction,
        )
        self._recorder.submit_session(state, on_saved=partial(node.ack, message.seq))

    async def _existing(self, path: str | None) -> str | None:
        if path is None or _SESSION_IMAGE.fullmatch(path) is None:
            return None
        exists = await asyncio.to_thread((self._snapshots_dir / path).is_file)
        return path if exists else None

    async def _write_photo(self, path: str, data: bytes) -> bool:
        """Write a photo from a worker under the snapshots directory (the path was checked)."""
        too_big = len(data) > MAX_FILE_BYTES
        if too_big or not data.startswith(b"\xff\xd8") or not self._save_images():
            return False
        root = self._snapshots_dir

        def write() -> None:
            target = root / path
            if not target.resolve().is_relative_to(root.resolve()):
                raise OSError("Path escapes the snapshots directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            partial_file = target.with_name(f"{target.name}.part")
            partial_file.write_bytes(data)
            partial_file.replace(target)

        try:
            await asyncio.to_thread(write)
        except OSError:
            logger.warning("Could not save a photo from a worker", exc_info=True)
            return False
        return True
