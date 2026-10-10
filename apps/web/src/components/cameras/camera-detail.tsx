"use client";

import { ArrowDownRight, ArrowLeft, ArrowUpRight, Eye, Gauge, Pencil, Radio, Ruler } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";

import { EventList, EventListSkeleton } from "@/components/events/event-list";
import { EventSheet } from "@/components/events/event-sheet";
import { LiveFeed } from "@/components/shared/live-feed";
import { PageHeader } from "@/components/shared/page-header";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { StatusBadge } from "@/components/shared/status";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, type Camera, type YardEvent } from "@/lib/api/client";
import { useCamera, useRecentEvents } from "@/lib/api/queries";
import { classLabel, SOURCE_TYPE_LABELS, sortedCounts } from "@/lib/format";

function Stat({ label, value, icon }: { label: string; value: ReactNode; icon: ReactNode }) {
  return (
    <div className="bg-card flex items-center gap-3 rounded-xl border p-4">
      <div className="bg-muted text-muted-foreground flex size-9 items-center justify-center rounded-lg [&_svg]:size-4">
        {icon}
      </div>
      <div className="min-w-0">
        <div className="text-muted-foreground text-xs">{label}</div>
        <div className="truncate text-lg font-semibold tabular-nums">{value}</div>
      </div>
    </div>
  );
}

function CameraStats({ camera }: { camera: Camera }) {
  const { runtime } = camera;
  const visibleTotal = Object.values(runtime.visible).reduce((sum, count) => sum + count, 0);
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Stat label="In view now" value={visibleTotal} icon={<Eye aria-hidden />} />
      <Stat
        label="Crossed in"
        value={camera.line ? runtime.line_in : "—"}
        icon={<ArrowDownRight className="text-chart-2" aria-hidden />}
      />
      <Stat
        label="Crossed out"
        value={camera.line ? runtime.line_out : "—"}
        icon={<ArrowUpRight className="text-chart-3" aria-hidden />}
      />
      <Stat
        label="Inference rate"
        value={`${runtime.measured_fps.toFixed(1)} / ${camera.inference_fps} fps`}
        icon={<Gauge aria-hidden />}
      />
    </div>
  );
}

export function CameraDetail({ cameraId }: { cameraId: number }) {
  const camera = useCamera(cameraId);
  const recent = useRecentEvents(cameraId);
  const [selected, setSelected] = useState<YardEvent | null>(null);

  if (camera.isError && camera.error instanceof ApiError && camera.error.status === 404) {
    return (
      <EmptyState
        className="min-h-96"
        icon={<Radio aria-hidden />}
        title="Camera not found"
        description="It may have been deleted. Pick another camera from the list."
        action={
          <Button asChild variant="outline">
            <Link href="/cameras">
              <ArrowLeft aria-hidden /> Back to cameras
            </Link>
          </Button>
        }
      />
    );
  }
  if (camera.isError) {
    return <ErrorState title="Couldn't load camera" error={camera.error} onRetry={() => void camera.refetch()} />;
  }
  if (!camera.data) return <CameraDetailSkeleton />;

  const data = camera.data;
  const { runtime } = data;
  const counts = sortedCounts(runtime.visible);

  return (
    <>
      <div>
        <Button variant="ghost" size="sm" asChild className="-ml-2">
          <Link href="/cameras">
            <ArrowLeft aria-hidden /> Cameras
          </Link>
        </Button>
      </div>
      <PageHeader
        title={data.name}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <StatusBadge status={runtime.status} />
            <span>{SOURCE_TYPE_LABELS[data.source_type]}</span>
            {runtime.frame_width && runtime.frame_height ? (
              <span className="tabular-nums">
                · {runtime.frame_width}×{runtime.frame_height}
              </span>
            ) : null}
          </span>
        }
        actions={
          <Button variant="outline" asChild>
            <Link href={`/settings?tab=cameras&edit=${data.id}`}>
              <Pencil aria-hidden /> Edit camera
            </Link>
          </Button>
        }
      />

      {runtime.last_error && runtime.status !== "online" ? (
        <Alert variant="destructive">
          <AlertTitle>Stream problem</AlertTitle>
          <AlertDescription>{runtime.last_error}</AlertDescription>
        </Alert>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <LiveFeed cameraId={data.id} name={data.name} status={runtime.status} priority className="rounded-xl border" />
          <CameraStats camera={data} />
          <Card>
            <CardHeader>
              <CardTitle>In view now</CardTitle>
              <CardDescription>Objects currently tracked in the frame, by class</CardDescription>
            </CardHeader>
            <CardContent>
              {counts.length === 0 ? (
                <p className="text-muted-foreground text-sm">Nothing detected right now.</p>
              ) : (
                <ul className="flex flex-wrap gap-2">
                  {counts.map(([name, count]) => (
                    <li key={name} className="bg-muted rounded-md px-2.5 py-1 text-sm">
                      {classLabel(name)} <span className="font-semibold tabular-nums">{count}</span>
                    </li>
                  ))}
                </ul>
              )}
              {!data.line ? (
                <p className="text-muted-foreground mt-4 flex items-center gap-2 text-xs">
                  <Ruler className="size-3.5" aria-hidden />
                  No counting line yet.
                  <Link href={`/settings?tab=cameras&line=${data.id}`} className="text-foreground underline underline-offset-4">
                    Draw one
                  </Link>
                  to count in/out crossings.
                </p>
              ) : null}
            </CardContent>
          </Card>
        </div>
        <Card className="h-fit">
          <CardHeader>
            <CardTitle>Recent events</CardTitle>
            <CardDescription>This camera, newest first</CardDescription>
          </CardHeader>
          <CardContent className="px-2">
            {recent.isPending ? (
              <EventListSkeleton />
            ) : recent.isError ? (
              <ErrorState title="Couldn't load events" error={recent.error} onRetry={() => void recent.refetch()} />
            ) : recent.data.items.length === 0 ? (
              <EmptyState
                className="border-none"
                icon={<Radio aria-hidden />}
                title="No events yet"
                description="Events for this camera will appear here in real time."
              />
            ) : (
              <EventList events={recent.data.items} onSelect={setSelected} showCamera={false} />
            )}
          </CardContent>
        </Card>
      </div>
      <EventSheet event={selected} onOpenChange={(open) => !open && setSelected(null)} />
    </>
  );
}

export function CameraDetailSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-8 w-64" />
      <div className="grid gap-6 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <Skeleton className="aspect-video w-full rounded-xl" />
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {Array.from({ length: 4 }, (_, index) => (
              <Skeleton key={index} className="h-[74px] rounded-xl" />
            ))}
          </div>
        </div>
        <Skeleton className="h-96 rounded-xl" />
      </div>
    </div>
  );
}
