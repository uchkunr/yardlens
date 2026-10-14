"use client";

import { ArrowDownRight, ArrowLeft, ArrowUpRight, Eye, Gauge, Pencil, Radio, Ruler } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";

import { EventList, EventListSkeleton } from "@/components/events/event-list";
import { EventSheet } from "@/components/events/event-sheet";
import { PlateBadge } from "@/components/loading/plate-badge";
import { LiveFeed } from "@/components/shared/live-feed";
import { PageHeader } from "@/components/shared/page-header";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { StatusBadge } from "@/components/shared/status";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useI18n } from "@/i18n/provider";
import { ApiError, type Camera, type YardEvent } from "@/lib/api/client";
import { useCamera, useRecentEvents } from "@/lib/api/queries";
import { sortedCounts } from "@/lib/format";

function Stat({
  label,
  value,
  icon,
  hint,
}: {
  label: string;
  value: ReactNode;
  icon: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <div className="bg-card flex items-center gap-3 rounded-xl border p-4">
      <div className="bg-muted text-muted-foreground flex size-9 shrink-0 items-center justify-center rounded-lg [&_svg]:size-4">
        {icon}
      </div>
      <div className="min-w-0">
        <div className="text-muted-foreground truncate text-xs">{label}</div>
        <div className="truncate text-lg font-semibold tabular-nums">{value}</div>
        {hint ? (
          <div className="text-muted-foreground truncate text-xs" title={typeof hint === "string" ? hint : undefined}>
            {hint}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function LoadingNow({ camera }: { camera: Camera }) {
  const { t, f } = useI18n();
  const session = camera.runtime.loading;
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t.camera.loadingNow}</CardTitle>
      </CardHeader>
      <CardContent>
        {session ? (
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div className="space-y-2">
              <PlateBadge plate={session.plate} className="text-lg" />
              <p className="text-muted-foreground text-sm">
                {t.sessionSheet.arrived} {f.time(session.arrived_at)} · {f.duration(session.duration_seconds)}
              </p>
            </div>
            <div className="text-right">
              <div className="text-muted-foreground text-xs">{t.camera.loadedLabel}</div>
              <div className="text-4xl font-semibold tabular-nums">{f.count(session.loaded)}</div>
            </div>
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">{t.camera.loadingIdle}</p>
        )}
      </CardContent>
    </Card>
  );
}

function CameraStats({ camera }: { camera: Camera }) {
  const { runtime } = camera;
  const { t, f } = useI18n();
  const visibleTotal = Object.values(runtime.visible).reduce((sum, count) => sum + count, 0);
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Stat label={t.camera.inViewNow} value={f.count(visibleTotal)} icon={<Eye aria-hidden />} />
      <Stat
        label={t.camera.crossedIn}
        value={camera.line ? f.count(runtime.line_in) : "—"}
        icon={<ArrowDownRight className="text-chart-2" aria-hidden />}
      />
      <Stat
        label={t.camera.crossedOut}
        value={camera.line ? f.count(runtime.line_out) : "—"}
        icon={<ArrowUpRight className="text-chart-3" aria-hidden />}
      />
      <Stat
        label={t.camera.inferenceRate}
        value={t.camera.fps(f.decimal(runtime.measured_fps, 1))}
        icon={<Gauge aria-hidden />}
        hint={runtime.status === "online" ? t.camera.modes[runtime.activity] : null}
      />
    </div>
  );
}

export function CameraDetail({ cameraId }: { cameraId: number }) {
  const camera = useCamera(cameraId);
  const recent = useRecentEvents(cameraId);
  const [selected, setSelected] = useState<YardEvent | null>(null);
  const { t, f } = useI18n();

  if (camera.isError && camera.error instanceof ApiError && camera.error.status === 404) {
    return (
      <EmptyState
        className="min-h-96"
        icon={<Radio aria-hidden />}
        title={t.camera.notFoundTitle}
        description={t.camera.notFoundDescription}
        action={
          <Button asChild variant="outline">
            <Link href="/cameras">
              <ArrowLeft aria-hidden /> {t.camera.backToCameras}
            </Link>
          </Button>
        }
      />
    );
  }
  if (camera.isError) {
    return <ErrorState title={t.errors.loadCamera} error={camera.error} onRetry={() => void camera.refetch()} />;
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
            <ArrowLeft aria-hidden /> {t.camera.back}
          </Link>
        </Button>
      </div>
      <PageHeader
        title={data.name}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <StatusBadge status={runtime.status} />
            <span>{t.sourceTypes[data.source_type]}</span>
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
              <Pencil aria-hidden /> {t.camera.edit}
            </Link>
          </Button>
        }
      />

      {runtime.last_error && runtime.status !== "online" ? (
        <Alert variant="destructive">
          <AlertTitle>{t.camera.streamProblem}</AlertTitle>
          <AlertDescription>{f.server(runtime.last_error)}</AlertDescription>
        </Alert>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <LiveFeed cameraId={data.id} name={data.name} status={runtime.status} priority className="rounded-xl border">
            {runtime.loading ? (
              <div className="absolute top-3 left-3 rounded-lg bg-black/70 px-3 py-1.5 text-white backdrop-blur-sm">
                <div className="text-[11px] opacity-80">{t.camera.loadedLabel}</div>
                <div className="text-2xl leading-none font-semibold tabular-nums">{f.count(runtime.loading.loaded)}</div>
              </div>
            ) : null}
          </LiveFeed>
          {data.zone ? <LoadingNow camera={data} /> : null}
          <CameraStats camera={data} />
          <Card>
            <CardHeader>
              <CardTitle>{t.camera.inViewNow}</CardTitle>
              <CardDescription>{t.camera.inViewDescription}</CardDescription>
            </CardHeader>
            <CardContent>
              {counts.length === 0 ? (
                <p className="text-muted-foreground text-sm">{t.camera.nothingDetected}</p>
              ) : (
                <ul className="flex flex-wrap gap-2">
                  {counts.map(([name, count]) => (
                    <li key={name} className="bg-muted rounded-md px-2.5 py-1 text-sm">
                      {f.className(name)} <span className="font-semibold tabular-nums">{count}</span>
                    </li>
                  ))}
                </ul>
              )}
              {!data.line ? (
                <p className="text-muted-foreground mt-4 flex items-center gap-2 text-xs">
                  <Ruler className="size-3.5 shrink-0" aria-hidden />
                  <span>
                    {t.camera.noLine}{" "}
                    <Link href={`/settings?tab=cameras&line=${data.id}`} className="text-foreground underline underline-offset-4">
                      {t.camera.drawLine}
                    </Link>
                    {t.camera.noLineEnd}
                  </span>
                </p>
              ) : null}
            </CardContent>
          </Card>
        </div>
        <Card className="h-fit">
          <CardHeader>
            <CardTitle>{t.overview.recentEvents}</CardTitle>
            <CardDescription>{t.camera.recentDescription}</CardDescription>
          </CardHeader>
          <CardContent className="px-2">
            {recent.isPending ? (
              <EventListSkeleton />
            ) : recent.isError ? (
              <ErrorState title={t.errors.loadEvents} error={recent.error} onRetry={() => void recent.refetch()} />
            ) : recent.data.items.length === 0 ? (
              <EmptyState
                className="border-none"
                icon={<Radio aria-hidden />}
                title={t.camera.noEventsTitle}
                description={t.camera.noEventsDescription}
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
