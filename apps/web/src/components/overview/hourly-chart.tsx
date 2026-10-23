"use client";

import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { Skeleton } from "@/components/ui/skeleton";
import { useI18n } from "@/i18n/provider";
import type { StatsOverview } from "@/lib/api/client";

export function HourlyChart({ stats }: { stats: StatsOverview }) {
  const { t, f } = useI18n();
  const chartConfig = {
    line_in: { label: t.chart.in, color: "var(--chart-2)" },
    line_out: { label: t.chart.out, color: "var(--chart-3)" },
  } satisfies ChartConfig;
  const data = stats.hourly.map((bucket) => ({ ...bucket, label: f.hour(bucket.hour) }));
  const total = stats.hourly.reduce((sum, bucket) => sum + bucket.line_in + bucket.line_out, 0);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t.chart.title}</CardTitle>
        <CardDescription>
          {total > 0 ? t.chart.description(f.count(total)) : t.chart.empty}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ChartContainer config={chartConfig} className="aspect-auto h-64 w-full">
          <BarChart data={data} margin={{ left: -16, right: 8, top: 8 }}>
            <CartesianGrid vertical={false} />
            <XAxis dataKey="label" tickLine={false} axisLine={false} tickMargin={8} minTickGap={24} />
            <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={48} />
            <ChartTooltip cursor={{ fillOpacity: 0.4 }} content={<ChartTooltipContent />} />
            <ChartLegend content={<ChartLegendContent />} />
            <Bar dataKey="line_in" fill="var(--color-line_in)" radius={[4, 4, 0, 0]} maxBarSize={20} />
            <Bar dataKey="line_out" fill="var(--color-line_out)" radius={[4, 4, 0, 0]} maxBarSize={20} />
          </BarChart>
        </ChartContainer>
      </CardContent>
    </Card>
  );
}

export function HourlyChartSkeleton() {
  return (
    <Card>
      <CardHeader className="gap-2">
        <Skeleton className="h-5 w-48" />
        <Skeleton className="h-4 w-72" />
      </CardHeader>
      <CardContent>
        <Skeleton className="h-64 w-full" />
      </CardContent>
    </Card>
  );
}
