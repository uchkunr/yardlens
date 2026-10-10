"use client";

import { Bar, CartesianGrid, ComposedChart, Line, XAxis, YAxis } from "recharts";

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
import type { StatsOverview } from "@/lib/api/client";
import { formatHour } from "@/lib/format";

const chartConfig = {
  entered: { label: "Objects", color: "var(--chart-1)" },
  line_in: { label: "In", color: "var(--chart-2)" },
  line_out: { label: "Out", color: "var(--chart-3)" },
} satisfies ChartConfig;

export function HourlyChart({ stats }: { stats: StatsOverview }) {
  const data = stats.hourly.map((bucket) => ({ ...bucket, label: formatHour(bucket.hour) }));
  const total = stats.hourly.reduce((sum, bucket) => sum + bucket.entered, 0);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Activity, last 24 hours</CardTitle>
        <CardDescription>
          {total > 0 ? `${total} objects detected · bars per hour, lines show crossings` : "No detections yet in the last 24 hours"}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ChartContainer config={chartConfig} className="aspect-auto h-64 w-full">
          <ComposedChart data={data} margin={{ left: -16, right: 8, top: 8 }}>
            <CartesianGrid vertical={false} />
            <XAxis dataKey="label" tickLine={false} axisLine={false} tickMargin={8} minTickGap={24} />
            <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={48} />
            <ChartTooltip cursor={{ fillOpacity: 0.4 }} content={<ChartTooltipContent />} />
            <ChartLegend content={<ChartLegendContent />} />
            <Bar dataKey="entered" fill="var(--color-entered)" radius={[4, 4, 0, 0]} maxBarSize={28} />
            <Line dataKey="line_in" type="monotone" stroke="var(--color-line_in)" strokeWidth={2} dot={false} />
            <Line dataKey="line_out" type="monotone" stroke="var(--color-line_out)" strokeWidth={2} dot={false} />
          </ComposedChart>
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
