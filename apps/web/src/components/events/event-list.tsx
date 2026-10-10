"use client";

import { ImageOff } from "lucide-react";

import { EventTypeBadge } from "@/components/shared/status";
import { Skeleton } from "@/components/ui/skeleton";
import { useNow } from "@/hooks/use-now";
import type { YardEvent } from "@/lib/api/client";
import { classLabel, formatDateTime, formatRelative } from "@/lib/format";

export function EventThumbnail({ event, className }: { event: YardEvent; className?: string }) {
  return (
    <div className={`bg-muted relative aspect-video shrink-0 overflow-hidden rounded-md ${className ?? "w-20"}`}>
      {event.snapshot_url ? (
        <img src={event.snapshot_url} alt="" loading="lazy" className="size-full object-cover" />
      ) : (
        <ImageOff className="text-muted-foreground absolute inset-0 m-auto size-4" aria-hidden />
      )}
    </div>
  );
}

/** Compact live list used on the overview and camera detail pages. */
export function EventList({
  events,
  onSelect,
  showCamera = true,
}: {
  events: YardEvent[];
  onSelect: (event: YardEvent) => void;
  showCamera?: boolean;
}) {
  const now = useNow();
  return (
    <ul className="divide-y" aria-live="polite" aria-relevant="additions">
      {events.map((event) => (
        <li key={event.id}>
          <button
            type="button"
            onClick={() => onSelect(event)}
            className="hover:bg-muted/60 focus-visible:ring-ring/50 flex w-full items-center gap-3 rounded-md px-2 py-2.5 text-left transition-colors outline-none focus-visible:ring-[3px]"
          >
            <EventThumbnail event={event} />
            <div className="min-w-0 flex-1 space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <EventTypeBadge type={event.type} />
                {event.class_name ? <span className="text-sm font-medium">{classLabel(event.class_name)}</span> : null}
              </div>
              <p className="text-muted-foreground truncate text-xs">
                {showCamera ? `${event.camera_name} · ` : null}
                <time dateTime={event.created_at} title={formatDateTime(event.created_at)}>
                  {formatRelative(event.created_at, now)}
                </time>
              </p>
            </div>
          </button>
        </li>
      ))}
    </ul>
  );
}

export function EventListSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-1">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="flex items-center gap-3 px-2 py-2.5">
          <Skeleton className="aspect-video w-20 rounded-md" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-3 w-24" />
          </div>
        </div>
      ))}
    </div>
  );
}
