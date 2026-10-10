"use client";

import Link from "next/link";

import { LiveFeed } from "@/components/shared/live-feed";
import { StatusDot } from "@/components/shared/status";
import { Skeleton } from "@/components/ui/skeleton";
import type { Camera } from "@/lib/api/client";
import { classLabel, sortedCounts, STATUS_LABELS } from "@/lib/format";

/** Live tile with name, status and the classes currently in view overlaid on the feed. */
export function CameraTile({ camera }: { camera: Camera }) {
  const { runtime } = camera;
  const counts = sortedCounts(runtime.visible).slice(0, 4);

  return (
    <Link
      href={`/cameras/${camera.id}`}
      className="group bg-card focus-visible:ring-ring/50 block overflow-hidden rounded-xl border shadow-xs transition-shadow outline-none hover:shadow-md focus-visible:ring-[3px]"
      aria-label={`${camera.name}, ${STATUS_LABELS[runtime.status]}. Open camera`}
    >
      <LiveFeed cameraId={camera.id} name={camera.name} status={runtime.status}>
        {counts.length > 0 ? (
          <div className="absolute inset-x-2 bottom-2 flex flex-wrap gap-1.5">
            {counts.map(([name, count]) => (
              <span
                key={name}
                className="rounded-md bg-black/65 px-1.5 py-0.5 text-[11px] font-medium text-white tabular-nums backdrop-blur-sm"
              >
                {classLabel(name)} {count}
              </span>
            ))}
          </div>
        ) : null}
      </LiveFeed>
      <div className="flex items-center gap-2 px-3 py-2.5">
        <StatusDot status={runtime.status} />
        <span className="truncate text-sm font-medium">{camera.name}</span>
        <span className="text-muted-foreground ml-auto shrink-0 text-xs tabular-nums">
          {runtime.status === "online" && camera.line
            ? `In ${runtime.line_in} · Out ${runtime.line_out}`
            : STATUS_LABELS[runtime.status]}
        </span>
      </div>
    </Link>
  );
}

export function CameraTileSkeleton() {
  return (
    <div className="overflow-hidden rounded-xl border">
      <Skeleton className="aspect-video w-full rounded-none" />
      <div className="flex items-center gap-2 px-3 py-3">
        <Skeleton className="size-2 rounded-full" />
        <Skeleton className="h-4 w-32" />
      </div>
    </div>
  );
}
