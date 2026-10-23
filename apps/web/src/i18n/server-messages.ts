import type { Locale } from "@/i18n/config";

type Rule = [pattern: RegExp, render: (...groups: string[]) => string];

/**
 * The API reports errors, camera problems and test results in English. These are the same
 * messages in Russian; keep them in step with the strings in `apps/api`. Anything not listed
 * (exception text from a library, say) is shown as the API sent it.
 */
const RU_RULES: Rule[] = [
  [/^Camera (\d+) not found$/, (id) => `Камера ${id} не найдена`],
  [/^Event (\d+) not found$/, (id) => `Событие ${id} не найдено`],
  [/^A camera with this name already exists$/, () => "Камера с таким названием уже есть"],
  [/^Camera is disabled$/, () => "Камера отключена"],
  [/^No frame received yet$/, () => "Кадров пока нет"],
  [/^Connection successful$/, () => "Подключение установлено"],
  [/^Connected, but no frame was received$/, () => "Подключились, но кадр не пришёл"],
  [/^Timed out waiting for a frame$/, () => "Кадр от источника так и не пришёл"],
  [/^Could not read a frame: ([\s\S]+)$/, (reason) => `Не удалось прочитать кадр: ${reason}`],
  [/^Could not open the video stream$/, () => "Не удалось открыть видеопоток"],
  [/^Stream ended or stalled; reconnecting$/, () => "Поток прервался или завис, переподключаемся"],
  [/^Video file not found: ([\s\S]+)$/, (path) => `Видеофайл не найден: ${path}`],
  [
    /^(\S+) URL must start with (.+)$/,
    (label, schemes) => `Адрес ${label} должен начинаться с ${schemes.replaceAll(" or ", " или ")}`,
  ],
  [/^HTTP request failed: ([\s\S]+)$/, (reason) => `HTTP-запрос не удался: ${reason}`],
  [/^MJPEG request failed: ([\s\S]+)$/, (reason) => `Запрос MJPEG не удался: ${reason}`],
  [/^Unsupported HTTP content type '(.+)'$/, (type) => `Неподдерживаемый тип содержимого: «${type}»`],
  [/^Counting line points must be distinct$/, () => "Точки линии подсчёта должны различаться"],
  [/^'since' must be earlier than 'until'$/, () => "Начало периода должно быть раньше его конца"],
  [/^Unknown timezone '(.+)'$/, (zone) => `Неизвестный часовой пояс «${zone}»`],
  [/^Unknown classes: (.+)$/, (names) => `Неизвестные классы: ${names}`],
  [/^Unsupported model '(.+)'$/, (model) => `Модель «${model}» не поддерживается`],
  [/^Could not load model: ([\s\S]+)$/, (reason) => `Не удалось загрузить модель: ${reason}`],
  [
    /^Bot token and chat ID are required to enable Telegram$/,
    () => "Чтобы включить уведомления, укажите токен бота и ID чата",
  ],
  [/^Bot token and chat ID are required$/, () => "Укажите токен бота и ID чата"],
  [/^Test message sent$/, () => "Тестовое сообщение отправлено"],
  [/^Could not reach Telegram: (.+)$/, (reason) => `Не удалось связаться с Telegram: ${reason}`],
  [/^Telegram API error: ([\s\S]+)$/, (reason) => `Ошибка Telegram API: ${reason}`],
  [/^Unexpected error: ([\s\S]+)$/, (reason) => `Непредвиденная ошибка: ${reason}`],
  [/^Internal server error$/, () => "Внутренняя ошибка сервера"],
  [/^Loading session \w+ not found$/, () => "Погрузка не найдена"],
  [
    /^Cargo classes must be among the detected classes$/,
    () => "Груз нужно выбрать среди распознаваемых классов",
  ],
  [/^Main stream: ([\s\S]+)$/, (reason) => `Основной поток: ${translateServerMessage(reason, "ru")}`],
  [/^Wrong username or password$/, () => "Неверное имя пользователя или пароль"],
  [/^Too many attempts; try again in (\d+) s$/, (seconds) => `Слишком много попыток. Попробуйте через ${seconds} с`],
  [/^Not signed in$/, () => "Вы не вошли в систему"],
  [/^Session expired$/, () => "Сеанс истёк, войдите снова"],
  [/^Current password is wrong$/, () => "Текущий пароль указан неверно"],
  [/^Password must be at least (\d+) characters$/, (count) => `Пароль должен быть не короче ${count} символов`],
  [/^Password must be at most (\d+) characters$/, (count) => `Пароль должен быть не длиннее ${count} символов`],
  [/^Password must differ from the username$/, () => "Пароль не должен совпадать с именем пользователя"],
  [/^Cross-site request refused$/, () => "Запрос отклонён: он пришёл не со страницы YardLens"],
];

export function translateServerMessage(message: string, locale: Locale): string {
  if (locale === "en") return message;
  // Pydantic prefixes errors raised in validators.
  const text = message.replace(/^Value error, /, "");
  for (const [pattern, render] of RU_RULES) {
    const match = pattern.exec(text);
    if (match) return render(...match.slice(1));
  }
  return message;
}
