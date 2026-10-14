"use client";

import { ArrowRight, Radio } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { CameraTile, CameraTileSkeleton } from "@/components/cameras/camera-tile";
import { EventList, EventListSkeleton } from "@/components/events/event-list";
import { EventSheet } from "@/components/events/event-sheet";
import { LoadingOverview } from "@/components/loading/loading-overview";
import { SessionSheet } from "@/components/loading/session-sheet";
import { HourlyChart, HourlyChartSkeleton } from "@/components/overview/hourly-chart";
import { KpiCards, KpiCardsSkeleton } from "@/components/overview/kpi-cards";
import { useLiveEvents } from "@/components/shared/live-events-provider";
import { PageHeader } from "@/components/shared/page-header";
import { EmptyState, ErrorState, NoCamerasState } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useI18n } from "@/i18n/provider";
import type { LoadingSession, YardEvent } from "@/lib/api/client";
import { useCameras, useLoadingSummary, useRecentEvents, useStats } from "@/lib/api/queries";

const OVERVIEW_TILES = 4;

export function OverviewView() {
  const stats = useStats();
  const cameras = useCameras();
  const recent = useRecentEvents();
  const { connected } = useLiveEvents();
  const [selected, setSelected] = useState<YardEvent | null>(null);
  const [session, setSession] = useState<LoadingSession | null>(null);
  const loading = useLoadingSummary();
  const { t } = useI18n();
  // With a loading bay set up, the page leads with what a manager cares about: vehicles and sacks.
  const loadingSite = (loading.data?.loading_cameras ?? 0) > 0;
  const liveSession = session ? (loading.data?.active.find((item) => item.id === session.id) ?? session) : null;

  const noCameras = cameras.isSuccess && cameras.data.length === 0;
  const tiles = (cameras.data ?? [])
    .slice()
    .sort((a, b) => Number(b.runtime.status === "online") - Number(a.runtime.status === "online"))
    .slice(0, OVERVIEW_TILES);

  return (
    <>
      <PageHeader
        title={t.nav.overview}
        description={t.overview.description}
        actions={
          <Badge variant="outline" className="gap-1.5 font-normal">
            <Radio className={connected ? "text-success" : "text-muted-foreground"} aria-hidden />
            {connected ? t.overview.live : t.overview.reconnecting}
          </Badge>
        }
      />

      {noCameras ? <NoCamerasState className="min-h-80" /> : null}

      {loadingSite && loading.data ? (
        <LoadingOverview summary={loading.data} onSelect={setSession} />
      ) : stats.isPending ? (
        <KpiCardsSkeleton />
      ) : stats.isError ? (
        <ErrorState title={t.errors.loadStats} error={stats.error} onRetry={() => void stats.refetch()} />
      ) : noCameras ? null : (
        <KpiCards stats={stats.data} />
      )}

      {noCameras ? null : (
        <div className="grid gap-6 xl:grid-cols-3">
          <div className="space-y-6 xl:col-span-2">
            {loadingSite ? null : stats.isPending ? (
              <HourlyChartSkeleton />
            ) : stats.isSuccess ? (
              <HourlyChart stats={stats.data} />
            ) : null}

            <section aria-labelledby="live-cameras" className="space-y-3">
              <div className="flex items-center justify-between">
                <h2 id="live-cameras" className="text-base font-semibold">
                  {t.overview.liveCameras}
                </h2>
                <Button variant="ghost" size="sm" asChild>
                  <Link href="/cameras">
                    {t.overview.allCameras} <ArrowRight aria-hidden />
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
                <ErrorState title={t.errors.loadCameras} error={cameras.error} onRetry={() => void cameras.refetch()} />
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
              <CardTitle>{t.overview.recentEvents}</CardTitle>
              <CardDescription>{t.overview.recentDescription}</CardDescription>
              <CardAction>
                <Button variant="ghost" size="sm" asChild>
                  <Link href="/events">{t.overview.viewAll}</Link>
                </Button>
              </CardAction>
            </CardHeader>
            <CardContent className="px-2">
              {recent.isPending ? (
                <EventListSkeleton />
              ) : recent.isError ? (
                <ErrorState title={t.errors.loadEvents} error={recent.error} onRetry={() => void recent.refetch()} />
              ) : recent.data.items.length === 0 ? (
                <EmptyState
                  icon={<Radio aria-hidden />}
                  title={t.overview.waitingTitle}
                  description={t.overview.waitingDescription}
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
      <SessionSheet session={liveSession} onOpenChange={(open) => !open && setSession(null)} onChange={setSession} />
    </>
  );
}
