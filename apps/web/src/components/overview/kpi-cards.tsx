"use client";

import { Activity, ArrowDownRight, ArrowUpRight, Car, Cctv } from "lucide-react";
import type { ReactNode } from "react";

import { Card, CardAction, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { StatsOverview } from "@/lib/api/client";
import { formatCount } from "@/lib/format";

function Kpi({
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
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Kpi
        label="Vehicles today"
        value={formatCount(stats.vehicles_today)}
        icon={<Car aria-hidden />}
        footer={`${formatCount(stats.objects_today)} objects detected in total`}
      />
      <Kpi
        label="Line crossings today"
        value={
          <span className="flex items-baseline gap-3">
            <span className="flex items-baseline gap-1">
              <ArrowDownRight className="text-chart-2 size-5 self-center" aria-label="In" />
              {formatCount(stats.line_in_today)}
            </span>
            <span className="flex items-baseline gap-1">
              <ArrowUpRight className="text-chart-3 size-5 self-center" aria-label="Out" />
              {formatCount(stats.line_out_today)}
            </span>
          </span>
        }
        icon={<Activity aria-hidden />}
        footer="In / out across all counting lines"
      />
      <Kpi
        label="Cameras online"
        value={
          <>
            {stats.cameras_online}
            <span className="text-muted-foreground text-xl font-normal"> / {stats.cameras_total}</span>
          </>
        }
        icon={<Cctv aria-hidden />}
        footer={
          stats.cameras_total === 0
            ? "No cameras configured"
            : stats.cameras_online === stats.cameras_total
              ? "All cameras streaming"
              : `${stats.cameras_total - stats.cameras_online} not streaming`
        }
      />
      <Kpi
        label="Events last hour"
        value={formatCount(stats.events_last_hour)}
        icon={<Activity aria-hidden />}
        footer="Detections, crossings and status changes"
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
