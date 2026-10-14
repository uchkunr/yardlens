"""Events and loading sessions the server has not acknowledged yet.

Everything the cameras report goes in here, numbered, and stays until the server says it is
saved; after a dropped connection all of it is sent again (the server saves each event once). For
a session only its latest state is kept. Photos are read from disk when a report is sent, and
deleted once the server has them: the worker keeps no archive of its own.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from yardlens_api.core.enums import SessionStatus
from yardlens_api.schemas.worker_protocol import EventReport, SessionReport, pack
from yardlens_api.services.camera_worker import WorkerEvent
from yardlens_api.services.loading import SessionState

logger = logging.getLogger(__name__)

type Report = EventReport | SessionReport

# Beyond this many unacknowledged reports (a long outage) the oldest events give way.
DEFAULT_LIMIT = 20_000


@dataclass(frozen=True, slots=True)
class _Photo:
    path: str
    mtime_ns: int


class Outbox:
    def __init__(self, snapshots_dir: Path, *, limit: int = DEFAULT_LIMIT) -> None:
        self._dir = snapshots_dir
        self._limit = limit
        self._reports: dict[int, Report] = {}
        self._session_seq: dict[str, int] = {}
        # Session photos the server has, by the version it has; and what each send attached.
        self._delivered: dict[str, int] = {}
        self._attached: dict[int, list[_Photo]] = {}
        self._seq = 0
        self.dropped = 0

    def __len__(self) -> int:
        return len(self._reports)

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    def add_event(self, event: WorkerEvent, epoch: int) -> EventReport:
        report = EventReport(seq=self._next(), epoch=epoch, event=event)
        self._reports[report.seq] = report
        self._trim()
        return report

    def add_session(self, state: SessionState, epoch: int) -> SessionReport:
        """Queue the session's latest state, replacing an older one still waiting."""
        previous = self._session_seq.pop(state.id, None)
        if previous is not None:
            self._reports.pop(previous, None)
        report = SessionReport(seq=self._next(), epoch=epoch, session=state)
        self._reports[report.seq] = report
        self._session_seq[state.id] = report.seq
        return report

    def pending(self) -> list[Report]:
        """Everything not acknowledged, oldest first."""
        return list(self._reports.values())

    def is_pending(self, seq: int) -> bool:
        return seq in self._reports

    def encode(self, report: Report) -> str | bytes:
        """The frame for a report with its photos read from disk now (blocking: run it in a
        thread). Photos that are gone are left out."""
        if isinstance(report, EventReport):
            path = report.event.snapshot_path
            data = self._read(path) if path else None
            return pack(report, [data] if data is not None else [])
        photos: list[_Photo] = []
        files: list[bytes] = []
        for path in (report.session.vehicle_snapshot_path, report.session.plate_snapshot_path):
            if path is None:
                continue
            try:
                mtime = (self._dir / path).stat().st_mtime_ns
            except OSError:
                continue
            if self._delivered.get(path) == mtime:
                continue  # the server has this version
            data = self._read(path)
            if data is not None:
                photos.append(_Photo(path, mtime))
                files.append(data)
        self._attached[report.seq] = photos
        return pack(report.model_copy(update={"images": [p.path for p in photos]}), files)

    def ack(self, seqs: list[int]) -> None:
        for seq in seqs:
            report = self._reports.pop(seq, None)
            attached = self._attached.pop(seq, [])
            if isinstance(report, EventReport):
                self._remove(report.event.snapshot_path)
            elif isinstance(report, SessionReport):
                self._session_acked(report, attached)

    def _session_acked(self, report: SessionReport, attached: list[_Photo]) -> None:
        state = report.session
        if self._session_seq.get(state.id) == report.seq:
            del self._session_seq[state.id]
        for photo in attached:
            self._delivered[photo.path] = photo.mtime_ns
        if state.status is SessionStatus.COMPLETED and state.id not in self._session_seq:
            for path in (state.vehicle_snapshot_path, state.plate_snapshot_path):
                if path is not None:
                    self._delivered.pop(path, None)
                    self._remove(path)

    def _trim(self) -> None:
        while len(self._reports) > self._limit:
            oldest = next(
                (seq for seq, report in self._reports.items() if isinstance(report, EventReport)),
                None,
            )
            if oldest is None:
                return
            report = self._reports.pop(oldest)
            self._attached.pop(oldest, None)
            assert isinstance(report, EventReport)
            self._remove(report.event.snapshot_path)
            self.dropped += 1
            if self.dropped == 1 or self.dropped % 1000 == 0:
                logger.error(
                    "Server unreachable for long; dropping the oldest events",
                    extra={"dropped": self.dropped},
                )

    def _read(self, path: str) -> bytes | None:
        try:
            return (self._dir / path).read_bytes()
        except OSError:
            return None

    def _remove(self, path: str | None) -> None:
        if path is None:
            return
        try:
            (self._dir / path).unlink(missing_ok=True)
        except OSError:
            logger.debug("Could not remove a delivered photo", extra={"path": path})
