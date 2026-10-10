"use client";

import { ExternalLink, ImageOff } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { EventTypeBadge } from "@/components/shared/status";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import type { YardEvent } from "@/lib/api/client";
import { classLabel, EVENT_TYPE_LABELS, formatDateTime } from "@/lib/format";

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 py-2 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right font-medium">{children}</dd>
    </div>
  );
}

export function EventSheet({ event, onOpenChange }: { event: YardEvent | null; onOpenChange: (open: boolean) => void }) {
  const box = event?.details?.box;
  const reason = event?.details?.reason;

  return (
    <Sheet open={event !== null} onOpenChange={onOpenChange}>
      <SheetContent className="w-full gap-0 sm:max-w-xl">
        {event ? (
          <>
            <SheetHeader className="border-b">
              <SheetTitle className="flex items-center gap-2">
                {EVENT_TYPE_LABELS[event.type]}
                {event.class_name ? <span className="text-muted-foreground font-normal">· {classLabel(event.class_name)}</span> : null}
              </SheetTitle>
              <SheetDescription>
                {event.camera_name} · {formatDateTime(event.created_at)}
              </SheetDescription>
            </SheetHeader>
            <div className="flex-1 space-y-6 overflow-y-auto p-4">
              <div className="bg-muted relative aspect-video overflow-hidden rounded-lg border">
                {event.snapshot_url ? (
                  <a href={event.snapshot_url} target="_blank" rel="noreferrer" className="block size-full">
                    <img
                      src={event.snapshot_url}
                      alt={`Snapshot of ${event.class_name ?? "event"} on ${event.camera_name}`}
                      className="size-full object-contain"
                    />
                  </a>
                ) : (
                  <div className="text-muted-foreground absolute inset-0 flex flex-col items-center justify-center gap-2 text-sm">
                    <ImageOff className="size-5" aria-hidden />
                    No snapshot for this event
                  </div>
                )}
              </div>
              <dl className="divide-y">
                <Detail label="Type">
                  <EventTypeBadge type={event.type} />
                </Detail>
                <Detail label="Camera">{event.camera_name}</Detail>
                <Detail label="Time">{formatDateTime(event.created_at)}</Detail>
                {event.class_name ? <Detail label="Class">{classLabel(event.class_name)}</Detail> : null}
                {event.confidence !== null ? (
                  <Detail label="Confidence">{Math.round(event.confidence * 100)}%</Detail>
                ) : null}
                {event.tracker_id !== null ? <Detail label="Track ID">#{event.tracker_id}</Detail> : null}
                {Array.isArray(box) ? <Detail label="Bounding box">{box.join(", ")}</Detail> : null}
                {typeof reason === "string" ? <Detail label="Reason">{reason}</Detail> : null}
                <Detail label="Event ID">
                  <span className="font-mono text-xs">{event.id}</span>
                </Detail>
              </dl>
              <Separator />
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" size="sm" asChild>
                  <Link href={`/cameras/${event.camera_id}`}>View camera</Link>
                </Button>
                {event.snapshot_url ? (
                  <Button variant="ghost" size="sm" asChild>
                    <a href={event.snapshot_url} target="_blank" rel="noreferrer">
                      <ExternalLink aria-hidden /> Open snapshot
                    </a>
                  </Button>
                ) : null}
              </div>
            </div>
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
