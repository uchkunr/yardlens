"use client";

import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";

import type { Locale } from "@/i18n/config";
import { useI18n } from "@/i18n/provider";
import {
  api,
  unwrap,
  type AppSettings,
  type Camera,
  type CameraCreate,
  type CameraUpdate,
  type EventPage,
  type EventType,
  type LoadingFilters,
  type SettingsUpdate,
  type SourceType,
  type YardEvent,
} from "@/lib/api/client";

export type EventFilters = {
  cameraId?: number;
  types?: EventType[];
  className?: string;
  since?: string;
  until?: string;
  page: number;
  pageSize: number;
};

export const queryKeys = {
  health: ["health"] as const,
  cameras: ["cameras"] as const,
  camera: (id: number) => ["cameras", id] as const,
  events: (filters: EventFilters) => ["events", "list", filters] as const,
  recentEvents: (cameraId?: number) => ["events", "recent", cameraId ?? "all"] as const,
  stats: ["stats"] as const,
  settings: ["settings"] as const,
  resources: ["system", "resources"] as const,
  loading: ["loading"] as const,
  loadingSummary: (days: number) => ["loading", "summary", days] as const,
  loadingSessions: (filters: LoadingSessionFilters) => ["loading", "sessions", filters] as const,
};

export type LoadingSessionFilters = LoadingFilters & { page: number; pageSize: number };

/** The browser's IANA time zone, which defines "today" for the statistics. */
export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

export const RECENT_EVENTS_LIMIT = 20;

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: () => unwrap(api.GET("/api/health")),
    refetchInterval: 10_000,
    retry: false,
  });
}

export function useCameras() {
  return useQuery({
    queryKey: queryKeys.cameras,
    queryFn: () => unwrap(api.GET("/api/cameras")),
    refetchInterval: 5_000,
  });
}

export function useCamera(id: number) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: queryKeys.camera(id),
    queryFn: () => unwrap(api.GET("/api/cameras/{camera_id}", { params: { path: { camera_id: id } } })),
    initialData: () => queryClient.getQueryData<Camera[]>(queryKeys.cameras)?.find((c) => c.id === id),
    refetchInterval: 3_000,
  });
}

export function useEvents(filters: EventFilters) {
  return useQuery({
    queryKey: queryKeys.events(filters),
    queryFn: () =>
      unwrap(
        api.GET("/api/events", {
          params: {
            query: {
              camera_id: filters.cameraId,
              type: filters.types?.length ? filters.types : undefined,
              class_name: filters.className,
              since: filters.since,
              until: filters.until,
              page: filters.page,
              page_size: filters.pageSize,
            },
          },
          querySerializer: { array: { style: "form", explode: true } },
        }),
      ),
    placeholderData: keepPreviousData,
  });
}

export function useRecentEvents(cameraId?: number) {
  return useQuery({
    queryKey: queryKeys.recentEvents(cameraId),
    queryFn: () =>
      unwrap(
        api.GET("/api/events", {
          params: { query: { camera_id: cameraId, page: 1, page_size: RECENT_EVENTS_LIMIT } },
        }),
      ),
    staleTime: Infinity,
  });
}

export function useStats() {
  return useQuery({
    queryKey: queryKeys.stats,
    queryFn: () => unwrap(api.GET("/api/stats/overview", { params: { query: { tz: browserTimeZone() } } })),
    refetchInterval: 30_000,
  });
}

export function useLoadingSummary(days = 14) {
  return useQuery({
    queryKey: queryKeys.loadingSummary(days),
    queryFn: () =>
      unwrap(api.GET("/api/loading/summary", { params: { query: { tz: browserTimeZone(), days } } })),
    refetchInterval: 30_000,
  });
}

export function useLoadingSessions(filters: LoadingSessionFilters) {
  return useQuery({
    queryKey: queryKeys.loadingSessions(filters),
    queryFn: () =>
      unwrap(
        api.GET("/api/loading/sessions", {
          params: {
            query: {
              camera_id: filters.cameraId,
              plate: filters.plate || undefined,
              since: filters.since,
              until: filters.until,
              page: filters.page,
              page_size: filters.pageSize,
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  });
}

export function useUpdateLoadingPlate() {
  const queryClient = useQueryClient();
  const { t, f } = useI18n();
  return useMutation({
    mutationFn: ({ id, plate }: { id: string; plate: string | null }) =>
      unwrap(
        api.PATCH("/api/loading/sessions/{session_id}", { params: { path: { session_id: id } }, body: { plate } }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.loading });
      toast.success(t.toasts.plateSaved);
    },
    onError: (error) => toast.error(t.toasts.plateFailed, { description: f.error(error) }),
  });
}

export function useSettings() {
  return useQuery({
    queryKey: queryKeys.settings,
    queryFn: () => unwrap(api.GET("/api/settings")),
  });
}

function upsertCamera(queryClient: QueryClient, camera: Camera) {
  queryClient.setQueryData<Camera[]>(queryKeys.cameras, (current) => {
    if (!current) return [camera];
    const exists = current.some((item) => item.id === camera.id);
    return exists
      ? current.map((item) => (item.id === camera.id ? camera : item))
      : [...current, camera];
  });
  queryClient.setQueryData(queryKeys.camera(camera.id), camera);
}

export function useCreateCamera() {
  const queryClient = useQueryClient();
  const { t, f } = useI18n();
  return useMutation({
    mutationFn: (body: CameraCreate) => unwrap(api.POST("/api/cameras", { body })),
    onSuccess: (camera) => {
      upsertCamera(queryClient, camera);
      void queryClient.invalidateQueries({ queryKey: queryKeys.stats });
      toast.success(t.toasts.cameraAdded);
    },
    onError: (error) => toast.error(t.toasts.addFailed, { description: f.error(error) }),
  });
}

export function useUpdateCamera() {
  const queryClient = useQueryClient();
  const { t, f } = useI18n();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: CameraUpdate }) =>
      unwrap(api.PATCH("/api/cameras/{camera_id}", { params: { path: { camera_id: id } }, body })),
    onSuccess: (camera) => {
      upsertCamera(queryClient, camera);
      toast.success(t.toasts.cameraSaved);
    },
    onError: (error) => toast.error(t.toasts.saveFailed, { description: f.error(error) }),
  });
}

/** Enable/disable with an optimistic status flip; rolled back if the request fails. */
export function useToggleCamera() {
  const queryClient = useQueryClient();
  const { t, f } = useI18n();
  return useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      unwrap(
        enabled
          ? api.POST("/api/cameras/{camera_id}/enable", { params: { path: { camera_id: id } } })
          : api.POST("/api/cameras/{camera_id}/disable", { params: { path: { camera_id: id } } }),
      ),
    onMutate: async ({ id, enabled }) => {
      await queryClient.cancelQueries({ queryKey: queryKeys.cameras });
      const previous = queryClient.getQueryData<Camera[]>(queryKeys.cameras);
      queryClient.setQueryData<Camera[]>(queryKeys.cameras, (current) =>
        current?.map((camera) =>
          camera.id === id
            ? {
                ...camera,
                enabled,
                runtime: { ...camera.runtime, status: enabled ? "connecting" : "disabled" },
              }
            : camera,
        ),
      );
      return { previous };
    },
    onError: (error, _variables, context) => {
      if (context?.previous) queryClient.setQueryData(queryKeys.cameras, context.previous);
      toast.error(t.toasts.toggleFailed, { description: f.error(error) });
    },
    onSuccess: (camera) => {
      upsertCamera(queryClient, camera);
      toast.success(camera.enabled ? t.toasts.cameraEnabled : t.toasts.cameraDisabled);
    },
  });
}

export function useDeleteCamera() {
  const queryClient = useQueryClient();
  const { t, f } = useI18n();
  return useMutation({
    mutationFn: (camera: Camera) =>
      unwrap(api.DELETE("/api/cameras/{camera_id}", { params: { path: { camera_id: camera.id } } })),
    onMutate: async (camera) => {
      await queryClient.cancelQueries({ queryKey: queryKeys.cameras });
      const previous = queryClient.getQueryData<Camera[]>(queryKeys.cameras);
      queryClient.setQueryData<Camera[]>(queryKeys.cameras, (current) =>
        current?.filter((item) => item.id !== camera.id),
      );
      return { previous };
    },
    onError: (error, _camera, context) => {
      if (context?.previous) queryClient.setQueryData(queryKeys.cameras, context.previous);
      toast.error(t.toasts.deleteFailed, { description: f.error(error) });
    },
    onSuccess: (_data, camera) => {
      queryClient.removeQueries({ queryKey: queryKeys.camera(camera.id) });
      void queryClient.invalidateQueries({ queryKey: ["events"] });
      void queryClient.invalidateQueries({ queryKey: queryKeys.stats });
      toast.success(t.toasts.cameraDeleted);
    },
  });
}

export function useTestCamera() {
  const { t, f } = useI18n();
  return useMutation({
    mutationFn: (body: { source_type: SourceType; url: string }) =>
      unwrap(api.POST("/api/cameras/test", { body: { ...body, timeout_seconds: 15 } })),
    onError: (error) => toast.error(t.toasts.testFailed, { description: f.error(error) }),
  });
}

export function useUpdateSettings(successMessage: string) {
  const queryClient = useQueryClient();
  const { t, f } = useI18n();
  return useMutation({
    mutationFn: (body: SettingsUpdate) => unwrap(api.PUT("/api/settings", { body })),
    onSuccess: (settings: AppSettings) => {
      queryClient.setQueryData(queryKeys.settings, settings);
      void queryClient.invalidateQueries({ queryKey: queryKeys.health });
      toast.success(successMessage);
    },
    onError: (error) => toast.error(t.toasts.settingsFailed, { description: f.error(error) }),
  });
}

export function useTestTelegram() {
  const { t, f } = useI18n();
  return useMutation({
    mutationFn: (body: { bot_token?: string; chat_id?: string; language?: Locale }) =>
      unwrap(api.POST("/api/settings/telegram/test", { body })),
    onSuccess: (result) => {
      if (result.success) toast.success(f.server(result.message));
      else toast.error(t.toasts.telegramTestFailed, { description: f.server(result.message) });
    },
    onError: (error) => toast.error(t.toasts.telegramTestFailed, { description: f.error(error) }),
  });
}

/** Insert a live event at the top of a cached "recent events" page (deduplicated, capped). */
export function prependRecentEvent(page: EventPage | undefined, event: YardEvent): EventPage | undefined {
  if (!page || page.items.some((item) => item.id === event.id)) return page;
  return {
    ...page,
    total: page.total + 1,
    items: [event, ...page.items].slice(0, RECENT_EVENTS_LIMIT),
  };
}

/** Machine load and what the governor allows; polled while the load panel is open. */
export function useSystemResources() {
  return useQuery({
    queryKey: queryKeys.resources,
    queryFn: () => unwrap(api.GET("/api/system/resources")),
    refetchInterval: 2_000,
  });
}
