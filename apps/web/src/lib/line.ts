import type { CountingLine } from "@/lib/api/client";

export type Point = { x: number; y: number };

const clamp01 = (value: number) => Math.min(1, Math.max(0, value));
const round = (value: number) => Math.round(value * 10_000) / 10_000;

/** Convert a click inside an element's bounding box to normalized (0–1) coordinates. */
export function normalizePoint(
  clientX: number,
  clientY: number,
  rect: Pick<DOMRect, "left" | "top" | "width" | "height">,
): Point {
  if (rect.width <= 0 || rect.height <= 0) return { x: 0, y: 0 };
  return {
    x: round(clamp01((clientX - rect.left) / rect.width)),
    y: round(clamp01((clientY - rect.top) / rect.height)),
  };
}

export function pointsToLine(start: Point, end: Point): CountingLine | null {
  if (Math.abs(start.x - end.x) < 0.001 && Math.abs(start.y - end.y) < 0.001) return null;
  return { x1: start.x, y1: start.y, x2: end.x, y2: end.y };
}

export function lineToPoints(line: CountingLine): [Point, Point] {
  return [
    { x: line.x1, y: line.y1 },
    { x: line.x2, y: line.y2 },
  ];
}

/**
 * Unit normal pointing to the "in" side. Counting follows supervision's convention: an object
 * is counted "in" when it crosses from the right-hand side of start→end to its left-hand side.
 */
export function inDirection(line: CountingLine): Point {
  const dx = line.x2 - line.x1;
  const dy = line.y2 - line.y1;
  const length = Math.hypot(dx, dy) || 1;
  return { x: dy / length, y: -dx / length };
}
