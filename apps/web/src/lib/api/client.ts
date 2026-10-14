import createClient from "openapi-fetch";

import type { components, paths } from "@/lib/api/schema";

export type Schemas = components["schemas"];
export type Camera = Schemas["CameraRead"];
export type CameraCreate = Schemas["CameraCreate"];
export type CameraUpdate = Schemas["CameraUpdate"];
export type CameraStatus = Schemas["CameraStatus"];
export type CameraTestResult = Schemas["CameraTestResult"];
export type CountingLine = Schemas["CountingLine"];
export type SourceType = Schemas["SourceType"];
export type YardEvent = Schemas["EventRead"];
export type EventPage = Schemas["EventPage"];
export type EventType = Schemas["EventType"];
export type StatsOverview = Schemas["StatsOverview"];
export type AppSettings = Schemas["SettingsRead"];
export type SettingsUpdate = Schemas["SettingsUpdate"];
export type DetectionSettings = Schemas["DetectionSettings"];
export type Health = Schemas["HealthRead"];
export type LoadingZone = Schemas["LoadingZone"];
export type LoadingSession = Schemas["LoadingSessionRead"];
export type LoadingSessionPage = Schemas["LoadingSessionPage"];
export type LoadingSummary = Schemas["LoadingSummary"];
export type LoadingTotals = Schemas["LoadingTotals"];
export type SystemResources = Schemas["SystemResources"];
export type PerformanceSettings = Schemas["PerformanceSettings"];
export type Activity = Schemas["Activity"];
export type LoadLevel = Schemas["LoadLevelName"];

/** Direct API origin, used for long-lived connections (MJPEG and SSE). */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8100").replace(
  /\/$/,
  "",
);

/** Regular requests go same-origin through the Next.js rewrite to the API. */
export const api = createClient<paths>({ baseUrl: "" });

/**
 * `server` errors carry the API's own message; the others are produced here, so the UI can
 * show them in its language without parsing the text.
 */
export type ApiErrorKind = "unreachable" | "unavailable" | "failed" | "server";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly kind: ApiErrorKind = "server",
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type ValidationIssue = { loc?: (string | number)[]; msg?: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/** Turn a FastAPI error body (`{detail: string}` or a validation list) into a readable message. */
export function describeError(body: unknown, fallback: string): string {
  if (!isRecord(body)) return fallback;
  const { detail } = body;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first: unknown = detail[0];
    if (isRecord(first)) {
      const issue = first as ValidationIssue;
      const field = issue.loc?.filter((part) => part !== "body").join(".");
      if (issue.msg) return field ? `${field}: ${issue.msg}` : issue.msg;
    }
  }
  return fallback;
}

type FetchResult<T> = { data?: T; error?: unknown; response: Response };

/** Resolve an openapi-fetch call to its data or throw an ApiError with the server's message. */
export async function unwrap<T>(request: Promise<FetchResult<T>>): Promise<T> {
  let result: FetchResult<T>;
  try {
    result = await request;
  } catch {
    throw new ApiError("Cannot reach the YardLens API", 0, "unreachable");
  }
  const { data, error, response } = result;
  if (!response.ok) {
    const detail = describeError(error, "");
    if (detail) throw new ApiError(detail, response.status);
    if (response.status >= 500 && response.status !== 503) {
      throw new ApiError("The API is unavailable", response.status, "unavailable");
    }
    throw new ApiError(`Request failed (${response.status})`, response.status, "failed");
  }
  return data as T;
}

export function streamUrl(cameraId: number): string {
  return `${API_URL}/api/cameras/${cameraId}/stream.mjpeg`;
}

export function snapshotUrl(cameraId: number, options: { raw?: boolean; nonce?: number } = {}) {
  const params = new URLSearchParams();
  if (options.raw) params.set("raw", "true");
  if (options.nonce !== undefined) params.set("t", String(options.nonce));
  const query = params.toString();
  return `/api/cameras/${cameraId}/snapshot.jpg${query ? `?${query}` : ""}`;
}

export const EVENTS_STREAM_URL = `${API_URL}/api/events/stream`;

export type LoadingFilters = { cameraId?: number; plate?: string; since?: string; until?: string };

/** Same-origin download of the loading history as an Excel file. */
export function loadingExportUrl(filters: LoadingFilters, options: { tz: string; lang: string }) {
  const params = new URLSearchParams({ tz: options.tz, lang: options.lang });
  if (filters.cameraId) params.set("camera_id", String(filters.cameraId));
  if (filters.plate) params.set("plate", filters.plate);
  if (filters.since) params.set("since", filters.since);
  if (filters.until) params.set("until", filters.until);
  return `/api/loading/sessions/export?${params.toString()}`;
}
