"use client";

import { RefreshCw } from "lucide-react";
import type { ReactNode } from "react";

import { useLiveEvents } from "@/components/shared/live-events-provider";
import { ErrorState } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useI18n } from "@/i18n/provider";
import { API_URL } from "@/lib/api/client";
import { useHealth } from "@/lib/api/queries";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-3 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right font-medium">{children}</dd>
    </div>
  );
}

function Indicator({ ok, label }: { ok: boolean; label: string }) {
  return (
    <Badge variant="outline" className="gap-1.5 font-normal">
      <span className={ok ? "bg-success size-2 rounded-full" : "bg-destructive size-2 rounded-full"} aria-hidden />
      {label}
    </Badge>
  );
}

export function SystemSettings() {
  const health = useHealth();
  const { connected } = useLiveEvents();
  const { t, f } = useI18n();
  const s = t.system;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{s.title}</CardTitle>
        <CardDescription>{s.description}</CardDescription>
        <CardAction>
          <Button variant="outline" size="sm" onClick={() => void health.refetch()} disabled={health.isFetching}>
            <RefreshCw className={health.isFetching ? "animate-spin" : undefined} aria-hidden /> {t.common.refresh}
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        {health.isPending ? (
          <div className="space-y-4 py-2">
            {Array.from({ length: 6 }, (_, index) => (
              <Skeleton key={index} className="h-5 w-full" />
            ))}
          </div>
        ) : health.isError ? (
          <ErrorState title={s.unreachable} error={health.error} onRetry={() => void health.refetch()} />
        ) : (
          <dl className="divide-y">
            <Row label={s.api}>
              <Indicator ok label={s.healthy} />
            </Row>
            <Row label={s.liveUpdates}>
              <Indicator ok={connected} label={connected ? s.connected : s.disconnected} />
            </Row>
            <Row label={s.version}>{health.data.version}</Row>
            <Row label={s.uptime}>{f.duration(health.data.uptime_seconds)}</Row>
            <Row label={s.model}>
              {health.data.detector.model}
              {!health.data.detector.loaded ? <span className="text-muted-foreground"> ({s.modelLoading})</span> : null}
            </Row>
            <Row label={s.device}>
              <span className="font-mono text-xs">{health.data.detector.device}</span>
            </Row>
            <Row label={s.camerasOnline}>
              {health.data.cameras_online} / {health.data.cameras_total}
            </Row>
            <Row label={s.python}>{health.data.python_version}</Row>
            <Row label={s.apiUrl}>
              <span className="font-mono text-xs">{API_URL}</span>
            </Row>
          </dl>
        )}
      </CardContent>
    </Card>
  );
}
