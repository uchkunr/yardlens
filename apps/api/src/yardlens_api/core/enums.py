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


VEHICLE_CLASSES: frozenset[str] = frozenset({"car", "truck", "bus", "motorcycle", "bicycle"})
DEFAULT_CLASSES: tuple[str, ...] = ("car", "truck", "bus", "motorcycle", "person")
