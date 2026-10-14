import { describe, expect, test } from "bun:test";

import { createFormatters } from "@/i18n/format";
import { MESSAGES } from "@/i18n/messages";
import { translateServerMessage } from "@/i18n/server-messages";
import { ApiError, describeError } from "@/lib/api/client";
import { formatPlate, sortedCounts } from "@/lib/format";

const ru = createFormatters("ru", MESSAGES.ru);
const en = createFormatters("en", MESSAGES.en);

// Intl separates Russian thousands with a narrow no-break space.
const plain = (text: string) => text.replace(/[  ]/g, " ");

describe("format helpers", () => {
  test("className translates, capitalizes and handles missing values", () => {
    expect(ru.className("truck")).toBe("Грузовик");
    expect(ru.className("stop sign")).toBe("Знак «Стоп»");
    expect(en.className("truck")).toBe("Truck");
    expect(en.className(null)).toBe("—");
  });

  test("count groups thousands the local way", () => {
    expect(en.count(1234567)).toBe("1,234,567");
    expect(plain(ru.count(1234567))).toBe("1 234 567");
  });

  test("decimal uses the local separator", () => {
    expect(ru.decimal(0.35, 2)).toBe("0,35");
    expect(en.decimal(0.35, 2)).toBe("0.35");
  });

  test("duration picks the two most significant units", () => {
    expect(en.duration(42)).toBe("42s");
    expect(en.duration(125)).toBe("2m");
    expect(en.duration(3 * 3600 + 120)).toBe("3h 2m");
    expect(en.duration(2 * 86400 + 5 * 3600)).toBe("2d 5h");
    expect(ru.duration(3 * 3600 + 120)).toBe("3 ч 2 мин");
    expect(ru.duration(2 * 86400 + 5 * 3600)).toBe("2 д 5 ч");
  });

  test("relative says 'just now' for very recent times", () => {
    const now = Date.parse("2026-01-01T12:00:00Z");
    expect(en.relative("2026-01-01T11:59:55Z", now)).toBe("just now");
    expect(ru.relative("2026-01-01T11:59:55Z", now)).toBe("только что");
    expect(en.relative("2026-01-01T11:58:00Z", now)).toBe("2 minutes ago");
    expect(ru.relative("2026-01-01T11:58:00Z", now)).toBe("2 минуты назад");
    expect(ru.relative("2026-01-01T11:55:00Z", now)).toBe("5 минут назад");
  });

  test("shortDate follows the local word order", () => {
    expect(en.shortDate("2026-10-14")).toBe("Oct 14");
    expect(ru.shortDate("2026-10-14")).toBe("14 окт.");
  });

  test("formatPlate spaces Uzbek plates and leaves others alone", () => {
    expect(formatPlate("01A123BC")).toBe("01 A 123 BC");
    expect(formatPlate("01123ABC")).toBe("01 123 ABC");
    expect(formatPlate("KZ777ABC02")).toBe("KZ777ABC02");
  });

  test("sortedCounts orders by count descending", () => {
    expect(sortedCounts({ car: 2, person: 5, bus: 1 })).toEqual([
      ["person", 5],
      ["car", 2],
      ["bus", 1],
    ]);
  });
});

describe("error messages", () => {
  test("errors raised in the client are shown in the UI language", () => {
    expect(ru.error(new ApiError("Cannot reach the YardLens API", 0, "unreachable"))).toBe("Нет связи с API YardLens");
    expect(ru.error(new ApiError("Request failed (404)", 404, "failed"))).toBe("Запрос не выполнен (404)");
    expect(en.error(new ApiError("Request failed (404)", 404, "failed"))).toBe("Request failed (404)");
  });

  test("known API messages are translated, with their details kept", () => {
    expect(translateServerMessage("Camera 3 not found", "ru")).toBe("Камера 3 не найдена");
    expect(translateServerMessage("RTSP URL must start with rtsp:// or rtsps://", "ru")).toBe(
      "Адрес RTSP должен начинаться с rtsp:// или rtsps://",
    );
    expect(translateServerMessage("Video file not found: data/videos/a.mp4", "ru")).toBe(
      "Видеофайл не найден: data/videos/a.mp4",
    );
    expect(translateServerMessage("Value error, Counting line points must be distinct", "ru")).toBe(
      "Точки линии подсчёта должны различаться",
    );
  });

  test("unknown messages and English pass through unchanged", () => {
    expect(translateServerMessage("Something new from the API", "ru")).toBe("Something new from the API");
    expect(translateServerMessage("Camera 3 not found", "en")).toBe("Camera 3 not found");
  });
});

describe("describeError", () => {
  test("reads FastAPI string details", () => {
    expect(describeError({ detail: "Camera 3 not found" }, "fallback")).toBe("Camera 3 not found");
  });

  test("reads the first validation issue with its field", () => {
    const body = { detail: [{ loc: ["body", "inference_fps"], msg: "Input should be less than or equal to 15" }] };
    expect(describeError(body, "fallback")).toBe("inference_fps: Input should be less than or equal to 15");
  });

  test("falls back for unknown shapes", () => {
    expect(describeError("oops", "fallback")).toBe("fallback");
    expect(describeError({ detail: [] }, "fallback")).toBe("fallback");
  });
});
