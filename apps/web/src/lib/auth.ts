/**
 * The browser side of the session. Tokens are HttpOnly cookies the page never reads; this module
 * only asks the API to renew them, and sends the header every state-changing request needs.
 *
 * Renewing rotates the refresh token, so only one renewal may run at a time: within a tab a
 * shared promise, across tabs a Web Lock where the browser has them (HTTPS and localhost). The
 * API tolerates two tabs racing for a minute, which covers browsers without locks.
 */
import type { components } from "@/lib/api/schema";

export type AuthState = components["schemas"]["AuthState"];

/** A custom header a cross-site form cannot send; the API refuses unsafe requests without it. */
export const CSRF_HEADERS = { "X-YardLens-Request": "1" } as const;

const LOCK_NAME = "yardlens-session-refresh";

let pending: Promise<AuthState | null> | null = null;

async function requestRefresh(): Promise<AuthState | null> {
  try {
    const response = await fetch("/api/auth/refresh", {
      method: "POST",
      headers: CSRF_HEADERS,
      credentials: "same-origin",
    });
    return response.ok ? ((await response.json()) as AuthState) : null;
  } catch {
    return null;
  }
}

async function refreshExclusively(): Promise<AuthState | null> {
  const locks = typeof navigator === "undefined" ? undefined : navigator.locks;
  return locks ? await locks.request(LOCK_NAME, requestRefresh) : await requestRefresh();
}

/** Renew the session; resolves to the new state, or `null` when the user must sign in again. */
export function refreshSession(): Promise<AuthState | null> {
  if (pending) return pending;
  const task = refreshExclusively().finally(() => {
    pending = null;
  });
  pending = task;
  return task;
}

export function loginUrl(): string {
  const next = `${window.location.pathname}${window.location.search}`;
  return next === "/" ? "/login" : `/login?next=${encodeURIComponent(next)}`;
}

/** Leave for the sign-in page, remembering where the user was. */
export function goToLogin(): void {
  if (window.location.pathname !== "/login") window.location.assign(loginUrl());
}

/** `fetch` for the API client: on 401 renew the session once and retry, else go sign in. */
export async function authFetch(request: Request): Promise<Response> {
  const retry = request.clone();
  const response = await fetch(request);
  if (response.status !== 401 || new URL(request.url).pathname.startsWith("/api/auth/")) {
    return response;
  }
  if (await refreshSession()) return fetch(retry);
  goToLogin();
  return response;
}
