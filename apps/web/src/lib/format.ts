import type { EventType, SourceType } from "@/lib/api/client";

// Language-dependent formatting lives in `@/i18n/format` (use `useI18n().f`).

export const EVENT_TYPES: EventType[] = [
  "line_crossed_in",
  "line_crossed_out",
  "camera_online",
  "camera_offline",
  "vehicle_arrived",
  "loading_completed",
];

export const SOURCE_TYPES: SourceType[] = ["rtsp", "http", "hls", "file"];

export const SOURCE_URL_PLACEHOLDERS: Record<SourceType, string> = {
  rtsp: "rtsp://user:pass@192.168.1.20:554/stream1",
  http: "https://example.com/camera/snapshot.jpg",
  hls: "https://example.com/live/playlist.m3u8",
  file: "videos/vehicles.mp4",
};

// Uzbek plates: "01 A 123 BC" (private) and "01 123 ABC" (organisations); the API stores them unspaced.
const UZBEK_PLATES = [/^(\d{2})([A-Z])(\d{3})([A-Z]{2})$/, /^(\d{2})(\d{3})([A-Z]{3})$/];

/** `01A123BC` → `01 A 123 BC`; other formats are shown as they are. */
export function formatPlate(plate: string): string {
  for (const pattern of UZBEK_PLATES) {
    const match = pattern.exec(plate);
    if (match) return match.slice(1).join(" ");
  }
  return plate;
}

/** Sort `{ class: count }` maps by count, largest first. */
export function sortedCounts(counts: Record<string, number>): [string, number][] {
  return Object.entries(counts).sort(([, a], [, b]) => b - a);
}
