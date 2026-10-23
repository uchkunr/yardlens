"use client";

import { Activity, ArrowDownRight, ArrowUpRight, Cctv } from "lucide-react";
import type { ReactNode } from "react";

import { Card, CardAction, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useI18n } from "@/i18n/provider";
import type { StatsOverview } from "@/lib/api/client";

export function Kpi({
  label,
  value,
  icon,
  footer,
}: {
  label: string;
  value: ReactNode;
  icon: ReactNode;
  footer: ReactNode;
}) {
  return (
    <Card className="gap-3">
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardAction className="text-muted-foreground [&_svg]:size-4">{icon}</CardAction>
        <CardTitle className="text-3xl font-semibold tracking-tight tabular-nums">{value}</CardTitle>
        <div className="text-muted-foreground text-xs">{footer}</div>
      </CardHeader>
    </Card>
  );
}

export function KpiCards({ stats }: { stats: StatsOverview }) {
  const { t, f } = useI18n();
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Kpi
        label={t.kpi.crossedIn}
        value={f.count(stats.line_in_today)}
        icon={<ArrowDownRight className="text-chart-2" aria-hidden />}
        footer={t.kpi.crossingsFooter}
      />
      <Kpi
        label={t.kpi.crossedOut}
        value={f.count(stats.line_out_today)}
        icon={<ArrowUpRight className="text-chart-3" aria-hidden />}
        footer={t.kpi.crossingsFooter}
      />
      <Kpi
        label={t.kpi.camerasOnline}
        value={
          <>
            {stats.cameras_online}
            <span className="text-muted-foreground text-xl font-normal"> / {stats.cameras_total}</span>
          </>
        }
        icon={<Cctv aria-hidden />}
        footer={
          stats.cameras_total === 0
            ? t.kpi.noCameras
            : stats.cameras_online === stats.cameras_total
              ? t.kpi.allStreaming
              : t.kpi.notStreaming(f.count(stats.cameras_total - stats.cameras_online))
        }
      />
      <Kpi
        label={t.kpi.eventsLastHour}
        value={f.count(stats.events_last_hour)}
        icon={<Activity aria-hidden />}
        footer={t.kpi.eventsFooter}
      />
    </div>
  );
}

export function KpiCardsSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {Array.from({ length: 4 }, (_, index) => (
        <Card key={index} className="gap-3">
          <CardHeader className="gap-3">
            <Skeleton className="h-4 w-28" />
            <Skeleton className="h-8 w-20" />
            <Skeleton className="h-3 w-40" />
          </CardHeader>
        </Card>
      ))}
    </div>
  );
}
