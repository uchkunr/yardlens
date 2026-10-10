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

/** Direct API origin, used for long-lived connections (MJPEG and SSE). */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);

/** Regular requests go same-origin through the Next.js rewrite to the API. */
export const api = createClient<paths>({ baseUrl: "" });

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
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
    throw new ApiError("Cannot reach the YardLens API", 0);
  }
  const { data, error, response } = result;
  if (!response.ok) {
    const fallback =
      response.status >= 500 && response.status !== 503
        ? "The API is unavailable"
        : `Request failed (${response.status})`;
    throw new ApiError(describeError(error, fallback), response.status);
  }
  return data as T;
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "Something went wrong";
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
