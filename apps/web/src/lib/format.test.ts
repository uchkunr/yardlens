import { describe, expect, test } from "bun:test";

import { describeError } from "@/lib/api/client";
import { classLabel, formatCount, formatDuration, formatRelative, sortedCounts } from "@/lib/format";

describe("format helpers", () => {
  test("classLabel capitalizes and handles missing values", () => {
    expect(classLabel("truck")).toBe("Truck");
    expect(classLabel(null)).toBe("—");
  });

  test("formatCount groups thousands", () => {
    expect(formatCount(1234567)).toBe("1,234,567");
  });

  test("formatDuration picks the two most significant units", () => {
    expect(formatDuration(42)).toBe("42s");
    expect(formatDuration(125)).toBe("2m");
    expect(formatDuration(3 * 3600 + 120)).toBe("3h 2m");
    expect(formatDuration(2 * 86400 + 5 * 3600)).toBe("2d 5h");
  });

  test("formatRelative says 'just now' for very recent times", () => {
    const now = Date.parse("2026-01-01T12:00:00Z");
    expect(formatRelative("2026-01-01T11:59:55Z", now)).toBe("just now");
    expect(formatRelative("2026-01-01T11:58:00Z", now)).toMatch(/ago$/);
  });

  test("sortedCounts orders by count descending", () => {
    expect(sortedCounts({ car: 2, person: 5, bus: 1 })).toEqual([
      ["person", 5],
      ["car", 2],
      ["bus", 1],
    ]);
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
