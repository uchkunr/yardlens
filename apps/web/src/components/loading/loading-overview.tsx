"use client";

import { CalendarRange, Clock, Package, Truck } from "lucide-react";
import Link from "next/link";
import { Bar, CartesianGrid, ComposedChart, Line, XAxis, YAxis } from "recharts";

import { PlateBadge } from "@/components/loading/plate-badge";
import { SessionTable, SessionTableSkeleton } from "@/components/loading/session-table";
import { Kpi } from "@/components/overview/kpi-cards";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { useI18n } from "@/i18n/provider";
import type { LoadingSession, LoadingSummary } from "@/lib/api/client";
import { useLoadingSessions } from "@/lib/api/queries";

const RECENT_ROWS = 8;

function LoadingKpis({ summary }: { summary: LoadingSummary }) {
  const { t, f } = useI18n();
  const m = t.loading;
  const { today } = summary;
  const [current, ...others] = summary.active;
  const periodLoaded = summary.daily.reduce((sum, day) => sum + day.loaded, 0);
  const periodVehicles = summary.daily.reduce((sum, day) => sum + day.vehicles, 0);

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Kpi
        label={m.vehiclesToday}
        value={f.count(today.vehicles)}
        icon={<Truck aria-hidden />}
        footer={
          today.average_duration_seconds !== null
            ? m.average(f.duration(today.average_duration_seconds))
            : today.vehicles === 0
              ? m.noVehiclesYet
              : null
        }
      />
      <Kpi
        label={m.loadedToday}
        value={f.count(today.loaded)}
        icon={<Package aria-hidden />}
        footer={m.loadedFooter}
      />
      <Kpi
        label={m.now}
        value={
          current ? (
            <span className="flex items-center gap-2">
              <PlateBadge plate={current.plate} className="text-lg" />
              {others.length > 0 ? (
                <span className="text-muted-foreground text-base font-normal">+{others.length}</span>
              ) : null}
            </span>
          ) : (
            m.bayFree
          )
        }
        icon={<Clock aria-hidden />}
        footer={current ? m.inProgress(f.count(current.loaded), f.duration(current.duration_seconds)) : m.waiting}
      />
      <Kpi
        label={m.periodTitle}
        value={f.count(periodLoaded)}
        icon={<CalendarRange aria-hidden />}
        footer={m.periodFooter(f.count(periodVehicles))}
      />
    </div>
  );
}

function LoadingChart({ summary }: { summary: LoadingSummary }) {
  const { t, f } = useI18n();
  const m = t.loading;
  const config = {
    loaded: { label: m.sacks, color: "var(--chart-1)" },
    vehicles: { label: m.vehicles, color: "var(--chart-3)" },
  } satisfies ChartConfig;
  const data = summary.daily.map((day) => ({ ...day, label: f.shortDate(day.day) }));

  return (
    <Card>
      <CardHeader>
        <CardTitle>{m.chartTitle}</CardTitle>
        <CardDescription>{m.chartDescription}</CardDescription>
      </CardHeader>
      <CardContent>
        <ChartContainer config={config} className="aspect-auto h-64 w-full">
          <ComposedChart data={data} margin={{ left: -8, right: -8, top: 8 }}>
            <CartesianGrid vertical={false} />
            <XAxis dataKey="label" tickLine={false} axisLine={false} tickMargin={8} minTickGap={16} />
            <YAxis yAxisId="loaded" allowDecimals={false} tickLine={false} axisLine={false} width={48} />
            <YAxis
              yAxisId="vehicles"
              orientation="right"
              allowDecimals={false}
              tickLine={false}
              axisLine={false}
              width={32}
            />
            <ChartTooltip cursor={{ fillOpacity: 0.4 }} content={<ChartTooltipContent />} />
            <ChartLegend content={<ChartLegendContent />} />
            <Bar yAxisId="loaded" dataKey="loaded" fill="var(--color-loaded)" radius={[4, 4, 0, 0]} maxBarSize={36} />
            <Line
              yAxisId="vehicles"
              dataKey="vehicles"
              type="monotone"
              stroke="var(--color-vehicles)"
              strokeWidth={2}
              dot={{ r: 3 }}
            />
          </ComposedChart>
        </ChartContainer>
      </CardContent>
    </Card>
  );
}

function RecentSessions({ onSelect }: { onSelect: (session: LoadingSession) => void }) {
  const { t } = useI18n();
  const m = t.loading;
  const sessions = useLoadingSessions({ page: 1, pageSize: RECENT_ROWS });

  return (
    <Card className="gap-0 overflow-hidden pb-0">
      <CardHeader className="pb-4">
        <CardTitle>{m.recentTitle}</CardTitle>
        <CardAction>
          <Button variant="ghost" size="sm" asChild>
            <Link href="/loading">{m.history}</Link>
          </Button>
        </CardAction>
      </CardHeader>
      {sessions.isPending ? (
        <SessionTableSkeleton rows={4} />
      ) : sessions.isError ? (
        <CardContent className="pb-6">
          <ErrorState title={t.loadingPage.loadError} error={sessions.error} onRetry={() => void sessions.refetch()} />
        </CardContent>
      ) : sessions.data.items.length === 0 ? (
        <CardContent className="pb-6">
          <EmptyState
            className="border-none"
            icon={<Truck aria-hidden />}
            title={m.noSessionsTitle}
            description={m.noSessionsDescription}
          />
        </CardContent>
      ) : (
        <SessionTable sessions={sessions.data.items} onSelect={onSelect} compact />
      )}
    </Card>
  );
}

/** The overview of a loading site: what a manager wants to see at a glance. */
export function LoadingOverview({
  summary,
  onSelect,
}: {
  summary: LoadingSummary;
  onSelect: (session: LoadingSession) => void;
}) {
  const { t, f } = useI18n();
  // "Today" as the API counted it, in the browser's time zone.
  const today = summary.daily.at(-1)?.day;
  return (
    <section aria-labelledby="loading-today" className="space-y-4">
      <h2 id="loading-today" className="text-base font-semibold">
        {t.loading.todayTitle}
        {today ? <span className="text-muted-foreground ml-2 font-normal">{f.day(today)}</span> : null}
      </h2>
      <LoadingKpis summary={summary} />
      <div className="grid gap-6 xl:grid-cols-5">
        <div className="xl:col-span-3">
          <LoadingChart summary={summary} />
        </div>
        <div className="xl:col-span-2">
          <RecentSessions onSelect={onSelect} />
        </div>
      </div>
    </section>
  );
}
