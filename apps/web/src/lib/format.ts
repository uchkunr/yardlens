import { formatDistanceToNowStrict } from "date-fns";

import type { CameraStatus, EventType, SourceType } from "@/lib/api/client";

export const EVENT_TYPE_LABELS: Record<EventType, string> = {
  object_entered: "Object entered",
  line_crossed_in: "Crossed in",
  line_crossed_out: "Crossed out",
  camera_online: "Camera online",
  camera_offline: "Camera offline",
};

export const EVENT_TYPES = Object.keys(EVENT_TYPE_LABELS) as EventType[];

export const SOURCE_TYPE_LABELS: Record<SourceType, string> = {
  rtsp: "RTSP",
  http: "HTTP (MJPEG / JPEG)",
  hls: "HLS (.m3u8)",
  youtube: "YouTube live",
  file: "Video file (looped)",
};

export const SOURCE_TYPES = Object.keys(SOURCE_TYPE_LABELS) as SourceType[];

export const SOURCE_URL_PLACEHOLDERS: Record<SourceType, string> = {
  rtsp: "rtsp://user:pass@192.168.1.20:554/stream1",
  http: "https://example.com/camera/snapshot.jpg",
  hls: "https://example.com/live/playlist.m3u8",
  youtube: "https://www.youtube.com/watch?v=…",
  file: "videos/vehicles.mp4",
};

export const STATUS_LABELS: Record<CameraStatus, string> = {
  online: "Online",
  connecting: "Connecting",
  offline: "Offline",
  error: "Error",
  disabled: "Disabled",
};

export function classLabel(name: string | null | undefined): string {
  if (!name) return "—";
  return name.charAt(0).toUpperCase() + name.slice(1);
}

export function formatCount(value: number): string {
  return new Intl.NumberFormat("en-US").format(value);
}

export function formatRelative(iso: string, now: number = Date.now()): string {
  const date = new Date(iso);
  if (Math.abs(now - date.getTime()) < 10_000) return "just now";
  return formatDistanceToNowStrict(date, { addSuffix: true });
}

export function formatDateTime(iso: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(iso));
}

export function formatHour(iso: string): string {
  return new Intl.DateTimeFormat("en-US", { hour: "numeric" }).format(new Date(iso));
}

export function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m`;
  return `${seconds}s`;
}

/** Sort `{ class: count }` maps by count, largest first. */
export function sortedCounts(counts: Record<string, number>): [string, number][] {
  return Object.entries(counts).sort(([, a], [, b]) => b - a);
}
