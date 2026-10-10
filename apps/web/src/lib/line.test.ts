import { describe, expect, test } from "bun:test";

import { inDirection, lineToPoints, normalizePoint, pointsToLine } from "@/lib/line";

describe("normalizePoint", () => {
  const rect = { left: 100, top: 50, width: 400, height: 200 };

  test("maps clicks to 0–1 coordinates", () => {
    expect(normalizePoint(300, 150, rect)).toEqual({ x: 0.5, y: 0.5 });
    expect(normalizePoint(100, 50, rect)).toEqual({ x: 0, y: 0 });
  });

  test("clamps clicks outside the element", () => {
    expect(normalizePoint(0, 1000, rect)).toEqual({ x: 0, y: 1 });
  });

  test("handles an empty rect", () => {
    expect(normalizePoint(10, 10, { left: 0, top: 0, width: 0, height: 0 })).toEqual({ x: 0, y: 0 });
  });
});

describe("pointsToLine", () => {
  test("builds a line from two distinct points", () => {
    expect(pointsToLine({ x: 0, y: 0.5 }, { x: 1, y: 0.5 })).toEqual({ x1: 0, y1: 0.5, x2: 1, y2: 0.5 });
  });

  test("rejects identical points", () => {
    expect(pointsToLine({ x: 0.3, y: 0.3 }, { x: 0.3, y: 0.3 })).toBeNull();
  });

  test("round-trips through lineToPoints", () => {
    const line = { x1: 0.1, y1: 0.2, x2: 0.8, y2: 0.9 };
    const [start, end] = lineToPoints(line);
    expect(pointsToLine(start, end)).toEqual(line);
  });
});

describe("inDirection", () => {
  test("points to the left of a left-to-right line (towards the top of the frame)", () => {
    const normal = inDirection({ x1: 0, y1: 0.5, x2: 1, y2: 0.5 });
    expect(normal.x).toBeCloseTo(0);
    expect(normal.y).toBeCloseTo(-1);
  });

  test("is a unit vector", () => {
    const normal = inDirection({ x1: 0.2, y1: 0.1, x2: 0.7, y2: 0.9 });
    expect(Math.hypot(normal.x, normal.y)).toBeCloseTo(1);
  });
});
