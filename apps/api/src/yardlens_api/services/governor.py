"""Load governor: keeps the machine below a CPU limit by trading frame rate for headroom.

Every couple of seconds the resource monitor feeds a ``ResourceSample`` to ``Governor``, which
turns it into a ``LoadLevel`` with hysteresis: it steps up one level after a few readings over the
limit and steps back down only after a longer calm spell, so it never flaps. ``limits_for`` maps
the level to the knobs the camera workers read on every tick. The live view gives way first;
counting keeps at least ``MIN_COUNTING_FPS`` so no crossing is missed.

Everything here is pure (no threads, clocks or I/O) and unit-tested with synthetic samples.
"""

from dataclasses import dataclass
from enum import IntEnum, StrEnum

# Below this an object can cross the counting line between two inference frames unnoticed.
MIN_COUNTING_FPS = 4.0
# A vehicle standing in a loading bay is checked this often while nothing else moves.
STANDBY_FPS = 3.0
# Load must fall this many points below the limit before the governor relaxes.
RELAX_MARGIN = 15.0
# Memory use (whole machine) at which the governor stops waiting for the CPU to complain.
MEMORY_PRESSURE_PERCENT = 92.0


class LoadLevel(IntEnum):
    NORMAL = 0
    BUSY = 1
    HIGH = 2
    OVERLOAD = 3


class LoadLevelName(StrEnum):
    NORMAL = "normal"
    BUSY = "busy"
    HIGH = "high"
    OVERLOAD = "overload"


LEVEL_NAMES: dict[LoadLevel, LoadLevelName] = {
    LoadLevel.NORMAL: LoadLevelName.NORMAL,
    LoadLevel.BUSY: LoadLevelName.BUSY,
    LoadLevel.HIGH: LoadLevelName.HIGH,
    LoadLevel.OVERLOAD: LoadLevelName.OVERLOAD,
}


@dataclass(frozen=True, slots=True)
class ResourceSample:
    cpu_percent: float
    """Whole machine, 0-100."""
    memory_percent: float
    """Whole machine, 0-100."""
    detector_busy: float = 0.0
    """Share of wall time spent in inference, 0-1. Near 1 the detector is the bottleneck."""
    gpu_percent: float | None = None
    """Only when inference runs on that GPU."""

    @property
    def pressure(self) -> float:
        """The scarcest resource, as a percentage."""
        return max(self.cpu_percent, self.detector_busy * 100.0, self.gpu_percent or 0.0)


@dataclass(frozen=True, slots=True)
class Limits:
    """What camera workers may spend right now. Immutable; replaced as a whole."""

    level: LoadLevel = LoadLevel.NORMAL
    adaptive: bool = False
    """Slow down cameras where nothing happens."""
    fps_scale: float = 1.0
    """Applied to each camera's configured (maximum) inference rate while it is active."""
    idle_fps: float = 1.0
    stream_fps: float = 15.0
    jpeg_quality: int = 80

    def active_fps(self, max_fps: float) -> float:
        return max(min(max_fps, MIN_COUNTING_FPS), max_fps * self.fps_scale)

    def standby_fps(self, max_fps: float) -> float:
        return min(self.active_fps(max_fps), max(self.idle_fps, STANDBY_FPS * self.fps_scale))

    def resting_fps(self, max_fps: float) -> float:
        return min(self.active_fps(max_fps), self.idle_fps)


@dataclass(frozen=True, slots=True)
class Budget:
    """The user's performance settings and the configured live-view quality."""

    cpu_limit_percent: float = 75.0
    adaptive: bool = True
    idle_fps: float = 1.0
    stream_fps: float = 15.0
    jpeg_quality: int = 80


def limits_for(level: LoadLevel, budget: Budget) -> Limits:
    """The live view gives way first; inference only slows from ``HIGH`` on."""
    match level:
        case LoadLevel.NORMAL:
            fps_scale, stream_fps, quality, idle_fps = (
                1.0,
                budget.stream_fps,
                budget.jpeg_quality,
                budget.idle_fps,
            )
        case LoadLevel.BUSY:
            fps_scale, stream_fps, quality, idle_fps = (
                1.0,
                min(budget.stream_fps, 8.0),
                min(budget.jpeg_quality, 70),
                budget.idle_fps,
            )
        case LoadLevel.HIGH:
            fps_scale, stream_fps, quality, idle_fps = (
                0.7,
                min(budget.stream_fps, 4.0),
                min(budget.jpeg_quality, 60),
                max(0.2, budget.idle_fps / 2),
            )
        case LoadLevel.OVERLOAD:
            fps_scale, stream_fps, quality, idle_fps = (
                0.5,
                min(budget.stream_fps, 2.0),
                min(budget.jpeg_quality, 50),
                0.2,
            )
    return Limits(
        level=level,
        adaptive=budget.adaptive,
        fps_scale=fps_scale,
        idle_fps=idle_fps,
        stream_fps=stream_fps,
        jpeg_quality=quality,
    )


class Governor:
    """Steps the load level up after ``raise_after`` readings over the limit and down after
    ``lower_after`` readings well under it."""

    def __init__(self, *, raise_after: int = 2, lower_after: int = 5) -> None:
        self._raise_after = raise_after
        self._lower_after = lower_after
        self._over = 0
        self._under = 0
        self.level = LoadLevel.NORMAL

    def update(self, sample: ResourceSample, limit_percent: float) -> LoadLevel:
        pressure = sample.pressure
        if pressure > limit_percent:
            self._over += 1
            self._under = 0
        elif pressure < limit_percent - RELAX_MARGIN:
            self._under += 1
            self._over = 0
        else:
            self._over = self._under = 0

        if self._over >= self._raise_after and self.level < LoadLevel.OVERLOAD:
            self.level = LoadLevel(self.level + 1)
            self._over = 0
        elif self._under >= self._lower_after and self.level > LoadLevel.NORMAL:
            self.level = LoadLevel(self.level - 1)
            self._under = 0

        if sample.memory_percent >= MEMORY_PRESSURE_PERCENT and self.level < LoadLevel.HIGH:
            self.level = LoadLevel.HIGH
        return self.level
