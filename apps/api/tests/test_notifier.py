from yardlens_api.core.enums import EventType
from yardlens_api.services.notifier import event_caption


def test_captions_follow_the_notification_language() -> None:
    russian = event_caption("ru", EventType.LINE_CROSSED_IN, "car", "Ворота")
    assert russian.splitlines() == ["Пересечение линии: вход · автомобиль", "Камера: Ворота"]
    english = event_caption("en", EventType.CAMERA_OFFLINE, None, "Gate")
    assert english.splitlines() == ["Camera offline", "Camera: Gate"]
