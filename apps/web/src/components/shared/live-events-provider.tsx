"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { EVENTS_STREAM_URL, type Camera, type CameraStatus, type YardEvent } from "@/lib/api/client";
import { prependRecentEvent, queryKeys } from "@/lib/api/queries";

type CameraStatusMessage = { camera_id: number; status: CameraStatus; last_error: string | null };

type LiveEventsState = {
  /** True while the server-sent events connection is open. */
  connected: boolean;
  /** Events received since `markSeen` was last called (drives "new events" prompts). */
  unseenCount: number;
  markSeen: () => void;
};

const LiveEventsContext = createContext<LiveEventsState>({
  connected: false,
  unseenCount: 0,
  markSeen: () => undefined,
});

const STATS_REFRESH_DELAY_MS = 3_000;

/** One app-wide SSE connection that keeps cached queries fresh as events arrive. */
export function LiveEventsProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [connected, setConnected] = useState(false);
  const [unseenCount, setUnseenCount] = useState(0);

  useEffect(() => {
    const source = new EventSource(EVENTS_STREAM_URL);
    let statsTimer: ReturnType<typeof setTimeout> | undefined;

    const refreshStatsSoon = () => {
      statsTimer ??= setTimeout(() => {
        statsTimer = undefined;
        void queryClient.invalidateQueries({ queryKey: queryKeys.stats });
      }, STATS_REFRESH_DELAY_MS);
    };

    const onReady = () => setConnected(true);
    const onError = () => setConnected(false);

    const onEvent = (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as YardEvent;
      for (const key of [queryKeys.recentEvents(), queryKeys.recentEvents(event.camera_id)]) {
        queryClient.setQueryData(key, (page: Parameters<typeof prependRecentEvent>[0]) =>
          prependRecentEvent(page, event),
        );
      }
      setUnseenCount((count) => count + 1);
      refreshStatsSoon();
    };

    const onStatus = (message: MessageEvent<string>) => {
      const update = JSON.parse(message.data) as CameraStatusMessage;
      const apply = (camera: Camera): Camera =>
        camera.id === update.camera_id
          ? { ...camera, runtime: { ...camera.runtime, status: update.status, last_error: update.last_error } }
          : camera;
      queryClient.setQueryData<Camera[]>(queryKeys.cameras, (cameras) => cameras?.map(apply));
      queryClient.setQueryData<Camera>(queryKeys.camera(update.camera_id), (camera) =>
        camera ? apply(camera) : camera,
      );
    };

    source.addEventListener("ready", onReady);
    source.addEventListener("error", onError);
    source.addEventListener("event", onEvent);
    source.addEventListener("camera_status", onStatus);
    return () => {
      clearTimeout(statsTimer);
      source.close();
    };
  }, [queryClient]);

  const markSeen = useCallback(() => setUnseenCount(0), []);
  const value = useMemo(() => ({ connected, unseenCount, markSeen }), [connected, unseenCount, markSeen]);

  return <LiveEventsContext value={value}>{children}</LiveEventsContext>;
}

export function useLiveEvents() {
  return useContext(LiveEventsContext);
}
