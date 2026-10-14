from enum import StrEnum


class SourceType(StrEnum):
    RTSP = "rtsp"
    HTTP = "http"
    HLS = "hls"
    YOUTUBE = "youtube"
    FILE = "file"


class CameraStatus(StrEnum):
    DISABLED = "disabled"
    CONNECTING = "connecting"
    ONLINE = "online"
    OFFLINE = "offline"
    ERROR = "error"


class EventType(StrEnum):
    OBJECT_ENTERED = "object_entered"
    LINE_CROSSED_IN = "line_crossed_in"
    LINE_CROSSED_OUT = "line_crossed_out"
    CAMERA_ONLINE = "camera_online"
    CAMERA_OFFLINE = "camera_offline"
    VEHICLE_ARRIVED = "vehicle_arrived"
    LOADING_COMPLETED = "loading_completed"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"


class Activity(StrEnum):
    """What a camera sees, which sets how often it runs inference (see ``services.pacing``)."""

    ACTIVE = "active"
    STANDBY = "standby"
    IDLE = "idle"


VEHICLE_CLASSES: frozenset[str] = frozenset({"car", "truck", "bus", "motorcycle", "bicycle"})
# Vehicles that can park in a loading bay; a custom model should name its truck class "truck".
LOADING_VEHICLE_CLASSES: frozenset[str] = frozenset({"car", "truck", "bus"})
DEFAULT_CLASSES: tuple[str, ...] = ("car", "truck", "bus", "motorcycle", "person")
