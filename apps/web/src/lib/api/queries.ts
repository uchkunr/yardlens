"use client";

import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";

import {
  api,
  errorMessage,
  unwrap,
  type AppSettings,
  type Camera,
  type CameraCreate,
  type CameraUpdate,
  type EventPage,
  type EventType,
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
};

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
    queryFn: () => {
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      return unwrap(api.GET("/api/stats/overview", { params: { query: { tz } } }));
    },
    refetchInterval: 30_000,
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
  return useMutation({
    mutationFn: (body: CameraCreate) => unwrap(api.POST("/api/cameras", { body })),
    onSuccess: (camera) => {
      upsertCamera(queryClient, camera);
      void queryClient.invalidateQueries({ queryKey: queryKeys.stats });
      toast.success(`Camera "${camera.name}" added`);
    },
    onError: (error) => toast.error("Could not add camera", { description: errorMessage(error) }),
  });
}

export function useUpdateCamera() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: CameraUpdate }) =>
      unwrap(api.PATCH("/api/cameras/{camera_id}", { params: { path: { camera_id: id } }, body })),
    onSuccess: (camera) => {
      upsertCamera(queryClient, camera);
      toast.success(`Camera "${camera.name}" saved`);
    },
    onError: (error) => toast.error("Could not save camera", { description: errorMessage(error) }),
  });
}

/** Enable/disable with an optimistic status flip; rolled back if the request fails. */
export function useToggleCamera() {
  const queryClient = useQueryClient();
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
      toast.error("Could not change camera state", { description: errorMessage(error) });
    },
    onSuccess: (camera) => {
      upsertCamera(queryClient, camera);
      toast.success(`${camera.name} ${camera.enabled ? "enabled" : "disabled"}`);
    },
  });
}

export function useDeleteCamera() {
  const queryClient = useQueryClient();
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
      toast.error("Could not delete camera", { description: errorMessage(error) });
    },
    onSuccess: (_data, camera) => {
      queryClient.removeQueries({ queryKey: queryKeys.camera(camera.id) });
      void queryClient.invalidateQueries({ queryKey: ["events"] });
      void queryClient.invalidateQueries({ queryKey: queryKeys.stats });
      toast.success(`Camera "${camera.name}" deleted`);
    },
  });
}

export function useTestCamera() {
  return useMutation({
    mutationFn: (body: { source_type: SourceType; url: string }) =>
      unwrap(api.POST("/api/cameras/test", { body: { ...body, timeout_seconds: 15 } })),
    onError: (error) => toast.error("Connection test failed", { description: errorMessage(error) }),
  });
}

export function useUpdateSettings(successMessage: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SettingsUpdate) => unwrap(api.PUT("/api/settings", { body })),
    onSuccess: (settings: AppSettings) => {
      queryClient.setQueryData(queryKeys.settings, settings);
      void queryClient.invalidateQueries({ queryKey: queryKeys.health });
      toast.success(successMessage);
    },
    onError: (error) => toast.error("Could not save settings", { description: errorMessage(error) }),
  });
}

export function useTestTelegram() {
  return useMutation({
    mutationFn: (body: { bot_token?: string; chat_id?: string }) =>
      unwrap(api.POST("/api/settings/telegram/test", { body })),
    onSuccess: (result) => {
      if (result.success) toast.success(result.message);
      else toast.error("Telegram test failed", { description: result.message });
    },
    onError: (error) => toast.error("Telegram test failed", { description: errorMessage(error) }),
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
