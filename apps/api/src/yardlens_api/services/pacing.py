"""How often each camera runs inference, and on which part of the frame.

Most of the day nothing happens in front of a yard camera, yet a fixed rate spends the same CPU on
an empty bay as on a busy one. ``Pacer`` picks the rate from what the camera saw last:

* **active**: something moves, a new object appears or cargo is counted. Full rate, held for a
  few seconds after the last movement so a pause does not drop the rate mid-crossing.
* **standby**: a loading-bay camera with a vehicle standing in the zone and nothing moving.
* **idle**: nothing moves. A trickle of frames is enough to notice when that changes.

``analysis_region`` narrows inference on loading-bay cameras to the zone and the counting line:
fewer pixels to process, and the objects that matter fill more of the model's input.
"""

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from yardlens_api.core.enums import Activity
from yardlens_api.schemas.camera import CountingLine, LoadingZone
from yardlens_api.services.governor import Limits

type Frame = NDArray[np.uint8]

# Stay at full rate this long after the last sign of movement.
ACTIVE_HOLD_SECONDS = 8.0
# Margin around the zone and line, as a share of the frame, so objects are tracked before they
# reach the line and trucks are seen whole.
REGION_MARGIN = 0.2
# Cropping a region this large saves too little to be worth it.
MAX_REGION_SHARE = 0.6
MODEL_INPUT_SIZE = 640
MIN_INPUT_SIZE = 320


class Pacer:
    def __init__(self, max_fps: float, *, hold_seconds: float = ACTIVE_HOLD_SECONDS) -> None:
        self.max_fps = max_fps
        self._hold = hold_seconds
        self._last_motion: float | None = None
        self._standby = False

    def observe(self, t: float, *, moving: bool, standby: bool = False) -> None:
        """Record what the inference frame taken at monotonic time ``t`` showed."""
        if moving or self._last_motion is None:
            # The first frame counts as movement so tracking starts at full rate.
            self._last_motion = t
        self._standby = standby

    def activity(self, t: float) -> Activity:
        if self._last_motion is None or t - self._last_motion < self._hold:
            return Activity.ACTIVE
        return Activity.STANDBY if self._standby else Activity.IDLE

    def fps(self, t: float, limits: Limits) -> float:
        if not limits.adaptive:
            return limits.active_fps(self.max_fps)
        match self.activity(t):
            case Activity.ACTIVE:
                return limits.active_fps(self.max_fps)
            case Activity.STANDBY:
                return limits.standby_fps(self.max_fps)
            case Activity.IDLE:
                return limits.resting_fps(self.max_fps)


@dataclass(frozen=True, slots=True)
class Region:
    """A rectangle in normalized (0-1) frame coordinates."""

    left: float
    top: float
    right: float
    bottom: float

    @property
    def share(self) -> float:
        return (self.right - self.left) * (self.bottom - self.top)

    def pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        return (
            max(0, math.floor(self.left * width)),
            max(0, math.floor(self.top * height)),
            min(width, math.ceil(self.right * width)),
            min(height, math.ceil(self.bottom * height)),
        )


def analysis_region(
    line: CountingLine | None, zone: LoadingZone | None, *, margin: float = REGION_MARGIN
) -> Region | None:
    """The part of the frame a loading-bay camera needs, or ``None`` to analyse it all."""
    if zone is None:
        return None
    xs = [point.x for point in zone.points]
    ys = [point.y for point in zone.points]
    if line is not None:
        xs += [line.x1, line.x2]
        ys += [line.y1, line.y2]
    region = Region(
        left=max(0.0, min(xs) - margin),
        top=max(0.0, min(ys) - margin),
        right=min(1.0, max(xs) + margin),
        bottom=min(1.0, max(ys) + margin),
    )
    return region if region.share <= MAX_REGION_SHARE else None


def crop_region(frame: Frame, region: Region | None) -> tuple[Frame, tuple[int, int]]:
    """``frame`` cut to ``region`` and the crop's top-left corner in ``frame``."""
    if region is None:
        return frame, (0, 0)
    height, width = frame.shape[:2]
    left, top, right, bottom = region.pixels(width, height)
    return frame[top:bottom, left:right], (left, top)


def input_size(width: int, height: int) -> int:
    """Model input size for an image this big: no upscaling past it, a multiple of 32."""
    side = math.ceil(max(width, height) / 32) * 32
    return max(MIN_INPUT_SIZE, min(MODEL_INPUT_SIZE, side))
