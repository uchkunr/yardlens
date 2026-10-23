"""Text YardLens sends outside the dashboard (Telegram), in the language picked in Settings.

The dashboard translates itself; the API's own responses stay in English.
"""

from typing import Literal

from yardlens_api.core.enums import EventType

Language = Literal["ru", "en"]
DEFAULT_LANGUAGE: Language = "en"

EVENT_TITLES: dict[Language, dict[EventType, str]] = {
    "ru": {
        EventType.LINE_CROSSED_IN: "Пересечение линии: вход",
        EventType.LINE_CROSSED_OUT: "Пересечение линии: выход",
        EventType.CAMERA_ONLINE: "Камера в сети",
        EventType.CAMERA_OFFLINE: "Камера не в сети",
        EventType.VEHICLE_ARRIVED: "Машина приехала",
        EventType.LOADING_COMPLETED: "Погрузка завершена",
    },
    "en": {
        EventType.LINE_CROSSED_IN: "Line crossed (in)",
        EventType.LINE_CROSSED_OUT: "Line crossed (out)",
        EventType.CAMERA_ONLINE: "Camera online",
        EventType.CAMERA_OFFLINE: "Camera offline",
        EventType.VEHICLE_ARRIVED: "Vehicle arrived",
        EventType.LOADING_COMPLETED: "Loading completed",
    },
}

CAMERA_LABEL: dict[Language, str] = {"ru": "Камера", "en": "Camera"}

# Extra caption lines for loading events.
LOADING_LABELS: dict[Language, dict[str, str]] = {
    "ru": {
        "plate": "Номер",
        "loaded": "Погружено",
        "unloaded": "Выгружено",
        "duration": "Время на площадке",
        "minutes": "мин",
    },
    "en": {
        "plate": "Plate",
        "loaded": "Loaded",
        "unloaded": "Unloaded",
        "duration": "In the bay",
        "minutes": "min",
    },
}

EXPORT_LABELS: dict[Language, dict[str, str]] = {
    "ru": {
        "sheet": "Погрузки",
        "number": "№",
        "date": "Дата",
        "plate": "Номер машины",
        "camera": "Камера",
        "arrived": "Приехала",
        "loading_started": "Начало погрузки",
        "loading_finished": "Конец погрузки",
        "departed": "Уехала",
        "minutes": "Время на площадке, мин",
        "loaded": "Погружено",
        "unloaded": "Выгружено",
        "total": "Итого",
        "vehicles": "машин",
        "in_progress": "на площадке",
    },
    "en": {
        "sheet": "Loadings",
        "number": "#",
        "date": "Date",
        "plate": "Plate",
        "camera": "Camera",
        "arrived": "Arrived",
        "loading_started": "Loading started",
        "loading_finished": "Loading finished",
        "departed": "Left",
        "minutes": "Minutes in the bay",
        "loaded": "Loaded",
        "unloaded": "Unloaded",
        "total": "Total",
        "vehicles": "vehicles",
        "in_progress": "in the bay",
    },
}

LOGIN_ALERT: dict[Language, str] = {
    "ru": "⚠️ YardLens: много неудачных попыток входа. Адрес: {address}.",
    "en": "⚠️ YardLens: many failed sign-in attempts. Address: {address}.",
}

TEST_MESSAGE: dict[Language, str] = {
    "ru": "✅ Тестовое сообщение YardLens: уведомления работают.",
    "en": "✅ YardLens test message: notifications are working.",
}

# The 80 COCO classes, plus common custom cargo names; the dashboard keeps the same list.
CLASS_NAMES_RU: dict[str, str] = {
    "person": "человек",
    "bicycle": "велосипед",
    "car": "автомобиль",
    "motorcycle": "мотоцикл",
    "airplane": "самолёт",
    "bus": "автобус",
    "train": "поезд",
    "truck": "грузовик",
    "boat": "лодка",
    "traffic light": "светофор",
    "fire hydrant": "пожарный гидрант",
    "stop sign": "знак «Стоп»",
    "parking meter": "паркомат",
    "bench": "скамейка",
    "bird": "птица",
    "cat": "кошка",
    "dog": "собака",
    "horse": "лошадь",
    "sheep": "овца",
    "cow": "корова",
    "elephant": "слон",
    "bear": "медведь",
    "zebra": "зебра",
    "giraffe": "жираф",
    "backpack": "рюкзак",
    "umbrella": "зонт",
    "handbag": "сумка",
    "tie": "галстук",
    "suitcase": "чемодан",
    "frisbee": "фрисби",
    "skis": "лыжи",
    "snowboard": "сноуборд",
    "sports ball": "мяч",
    "kite": "воздушный змей",
    "baseball bat": "бейсбольная бита",
    "baseball glove": "бейсбольная перчатка",
    "skateboard": "скейтборд",
    "surfboard": "доска для сёрфинга",
    "tennis racket": "теннисная ракетка",
    "bottle": "бутылка",
    "wine glass": "бокал",
    "cup": "чашка",
    "fork": "вилка",
    "knife": "нож",
    "spoon": "ложка",
    "bowl": "миска",
    "banana": "банан",
    "apple": "яблоко",
    "sandwich": "сэндвич",
    "orange": "апельсин",
    "broccoli": "брокколи",
    "carrot": "морковь",
    "hot dog": "хот-дог",
    "pizza": "пицца",
    "donut": "пончик",
    "cake": "торт",
    "chair": "стул",
    "couch": "диван",
    "potted plant": "растение в горшке",
    "bed": "кровать",
    "dining table": "обеденный стол",
    "toilet": "унитаз",
    "tv": "телевизор",
    "laptop": "ноутбук",
    "mouse": "компьютерная мышь",
    "remote": "пульт",
    "keyboard": "клавиатура",
    "cell phone": "мобильный телефон",
    "microwave": "микроволновка",
    "oven": "духовка",
    "toaster": "тостер",
    "sink": "раковина",
    "refrigerator": "холодильник",
    "book": "книга",
    "clock": "часы",
    "vase": "ваза",
    "scissors": "ножницы",
    "teddy bear": "плюшевый мишка",
    "hair drier": "фен",
    "toothbrush": "зубная щётка",
    # Usual names in custom models trained for loading bays.
    "sack": "мешок",
    "bag": "мешок",
}


def class_label(name: str, language: Language) -> str:
    return CLASS_NAMES_RU.get(name, name) if language == "ru" else name
