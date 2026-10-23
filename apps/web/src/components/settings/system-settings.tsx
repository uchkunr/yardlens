"use client";

import { useQuery } from "@tanstack/react-query";
import { Download, RefreshCw } from "lucide-react";
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

const CA_URL = "/api/workers/ca.pem";

/** The SHA-256 of the certificate in a PEM file, as browsers and operating systems show it. */
async function fingerprint(pem: string): Promise<string> {
  const body = pem.match(/-----BEGIN CERTIFICATE-----([\s\S]+?)-----END CERTIFICATE-----/)?.[1] ?? "";
  const der = Uint8Array.from(atob(body.replace(/\s+/g, "")), (char) => char.charCodeAt(0));
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", der));
  return Array.from(digest, (byte) => byte.toString(16).padStart(2, "0").toUpperCase()).join(":");
}

/** Offered only where the server has a certificate authority of its own (HTTPS through Caddy). */
function CertificateCard() {
  const { t } = useI18n();
  const s = t.system;
  const certificate = useQuery({
    queryKey: ["system", "ca"] as const,
    queryFn: async () => {
      const response = await fetch(CA_URL, { credentials: "same-origin" });
      if (!response.ok) return null;
      return fingerprint(await response.text());
    },
    staleTime: Infinity,
    retry: false,
  });
  if (!certificate.data) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>{s.certificateTitle}</CardTitle>
        <CardDescription>{s.certificateDescription}</CardDescription>
        <CardAction>
          <Button variant="outline" asChild>
            <a href={CA_URL} download="yardlens-ca.crt">
              <Download aria-hidden /> {s.downloadCertificate}
            </a>
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div className="space-y-1">
          <div className="text-muted-foreground text-xs">{s.fingerprint}</div>
          <div className="font-mono text-xs break-all">{certificate.data}</div>
        </div>
        <ul className="text-muted-foreground list-disc space-y-1.5 pl-5">
          <li>{s.install.windows}</li>
          <li>{s.install.android}</li>
          <li>{s.install.ios}</li>
          <li>{s.install.macos}</li>
        </ul>
      </CardContent>
    </Card>
  );
}

export function SystemSettings() {
  return (
    <div className="space-y-6">
      <StatusCard />
      <CertificateCard />
    </div>
  );
}

function StatusCard() {
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
              {/* Empty behind Caddy, where the API shares the dashboard's origin. */}
              <span className="font-mono text-xs">
                {API_URL || (typeof window === "undefined" ? "" : window.location.origin)}
              </span>
            </Row>
          </dl>
        )}
      </CardContent>
    </Card>
  );
}
