"use client";

import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { CSRF_HEADERS, goToLogin, refreshSession, type AuthState } from "@/lib/auth";

type User = AuthState["user"];

type Session = {
  user: User | null;
  signOut: () => Promise<void>;
  signOutEverywhere: () => Promise<void>;
};

const SessionContext = createContext<Session>({
  user: null,
  signOut: async () => undefined,
  signOutEverywhere: async () => undefined,
});

// Renew this long before the access token expires, and never more often than this.
const RENEW_EARLY_MS = 60_000;
const MIN_RENEW_DELAY_MS = 30_000;
// A tab coming back after this long renews at once instead of waiting for its timer.
const STALE_AFTER_MS = 5 * 60_000;

async function post(path: string): Promise<void> {
  try {
    await fetch(path, { method: "POST", headers: CSRF_HEADERS, credentials: "same-origin" });
  } finally {
    // A full load on purpose: nothing cached from the session may survive signing out.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign("/login");
  }
}

/**
 * Keeps the user signed in for good: the session is renewed on every visit, shortly before the
 * access token runs out, and when a sleeping tab wakes up. Each renewal also pushes the refresh
 * token's 400-day expiry forward, so only a year without a single visit signs the user out.
 */
export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let lastRenewal = 0;

    const renew = async () => {
      clearTimeout(timer);
      lastRenewal = Date.now();
      const state = await refreshSession();
      if (!active) return;
      if (!state) {
        goToLogin();
        return;
      }
      setUser(state.user);
      const due = Date.parse(state.access_expires_at) - Date.now() - RENEW_EARLY_MS;
      timer = setTimeout(() => void renew(), Math.max(MIN_RENEW_DELAY_MS, due));
    };

    const onVisible = () => {
      if (document.visibilityState === "visible" && Date.now() - lastRenewal > STALE_AFTER_MS) {
        void renew();
      }
    };

    void renew();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      active = false;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  const value = useMemo<Session>(
    () => ({
      user,
      signOut: () => post("/api/auth/logout"),
      signOutEverywhere: () => post("/api/auth/logout-all"),
    }),
    [user],
  );
  return <SessionContext value={value}>{children}</SessionContext>;
}

export function useSession() {
  return useContext(SessionContext);
}
