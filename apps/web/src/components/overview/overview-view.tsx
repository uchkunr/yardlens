"use client";

import { ArrowRight, Radio } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { CameraTile, CameraTileSkeleton } from "@/components/cameras/camera-tile";
import { EventList, EventListSkeleton } from "@/components/events/event-list";
import { EventSheet } from "@/components/events/event-sheet";
import { HourlyChart, HourlyChartSkeleton } from "@/components/overview/hourly-chart";
import { KpiCards, KpiCardsSkeleton } from "@/components/overview/kpi-cards";
import { useLiveEvents } from "@/components/shared/live-events-provider";
import { PageHeader } from "@/components/shared/page-header";
import { EmptyState, ErrorState, NoCamerasState } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { YardEvent } from "@/lib/api/client";
import { useCameras, useRecentEvents, useStats } from "@/lib/api/queries";

const OVERVIEW_TILES = 4;

export function OverviewView() {
  const stats = useStats();
  const cameras = useCameras();
  const recent = useRecentEvents();
  const { connected } = useLiveEvents();
  const [selected, setSelected] = useState<YardEvent | null>(null);

  const noCameras = cameras.isSuccess && cameras.data.length === 0;
  const tiles = (cameras.data ?? [])
    .slice()
    .sort((a, b) => Number(b.runtime.status === "online") - Number(a.runtime.status === "online"))
    .slice(0, OVERVIEW_TILES);

  return (
    <>
      <PageHeader
        title="Overview"
        description="Today's activity across all cameras, updated live."
        actions={
          <Badge variant="outline" className="gap-1.5 font-normal">
            <Radio className={connected ? "text-success" : "text-muted-foreground"} aria-hidden />
            {connected ? "Live" : "Reconnecting…"}
          </Badge>
        }
      />

      {noCameras ? <NoCamerasState className="min-h-80" /> : null}

      {stats.isPending ? (
        <KpiCardsSkeleton />
      ) : stats.isError ? (
        <ErrorState title="Couldn't load statistics" error={stats.error} onRetry={() => void stats.refetch()} />
      ) : noCameras ? null : (
        <KpiCards stats={stats.data} />
      )}

      {noCameras ? null : (
        <div className="grid gap-6 xl:grid-cols-3">
          <div className="space-y-6 xl:col-span-2">
            {stats.isPending ? <HourlyChartSkeleton /> : stats.isSuccess ? <HourlyChart stats={stats.data} /> : null}

            <section aria-labelledby="live-cameras" className="space-y-3">
              <div className="flex items-center justify-between">
                <h2 id="live-cameras" className="text-base font-semibold">
                  Live cameras
                </h2>
                <Button variant="ghost" size="sm" asChild>
                  <Link href="/cameras">
                    All cameras <ArrowRight aria-hidden />
                  </Link>
                </Button>
              </div>
              {cameras.isPending ? (
                <div className="grid gap-4 sm:grid-cols-2">
                  {Array.from({ length: 2 }, (_, index) => (
                    <CameraTileSkeleton key={index} />
                  ))}
                </div>
              ) : cameras.isError ? (
                <ErrorState title="Couldn't load cameras" error={cameras.error} onRetry={() => void cameras.refetch()} />
              ) : (
                <div className="grid gap-4 sm:grid-cols-2">
                  {tiles.map((camera) => (
                    <CameraTile key={camera.id} camera={camera} />
                  ))}
                </div>
              )}
            </section>
          </div>

          <Card className="h-fit xl:sticky xl:top-20">
            <CardHeader>
              <CardTitle>Recent events</CardTitle>
              <CardDescription>Newest first, streamed in real time</CardDescription>
              <CardAction>
                <Button variant="ghost" size="sm" asChild>
                  <Link href="/events">View all</Link>
                </Button>
              </CardAction>
            </CardHeader>
            <CardContent className="px-2">
              {recent.isPending ? (
                <EventListSkeleton />
              ) : recent.isError ? (
                <ErrorState title="Couldn't load events" error={recent.error} onRetry={() => void recent.refetch()} />
              ) : recent.data.items.length === 0 ? (
                <EmptyState
                  icon={<Radio aria-hidden />}
                  title="Waiting for events"
                  description="Detections, line crossings and camera status changes will show up here as they happen."
                  className="border-none"
                />
              ) : (
                <EventList events={recent.data.items} onSelect={setSelected} />
              )}
            </CardContent>
          </Card>
        </div>
      )}

      <EventSheet event={selected} onOpenChange={(open) => !open && setSelected(null)} />
    </>
  );
}
