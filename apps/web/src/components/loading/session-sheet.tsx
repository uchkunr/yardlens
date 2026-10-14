"use client";

import { ImageOff, PencilLine } from "lucide-react";
import { useState, type ReactNode } from "react";

import { InBayBadge } from "@/components/loading/session-table";
import { PlateBadge } from "@/components/loading/plate-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Spinner } from "@/components/ui/spinner";
import { useI18n } from "@/i18n/provider";
import type { LoadingSession } from "@/lib/api/client";
import { useUpdateLoadingPlate } from "@/lib/api/queries";
import { formatPlate } from "@/lib/format";

function Photo({ url, label }: { url: string | null; label: string }) {
  const { t } = useI18n();
  return (
    <figure className="space-y-1.5">
      <figcaption className="text-muted-foreground text-xs">{label}</figcaption>
      <div className="bg-muted relative flex aspect-video items-center justify-center overflow-hidden rounded-lg border">
        {url ? (
          <a href={url} target="_blank" rel="noreferrer" className="block size-full">
            <img src={url} alt={label} className="size-full object-contain" />
          </a>
        ) : (
          <span className="text-muted-foreground flex flex-col items-center gap-1 text-xs">
            <ImageOff className="size-4" aria-hidden />
            {t.sessionSheet.noPhoto}
          </span>
        )}
      </div>
    </figure>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right font-medium tabular-nums">{children}</dd>
    </div>
  );
}

function PlateEditor({
  session,
  onChange,
}: {
  session: LoadingSession;
  onChange?: (session: LoadingSession) => void;
}) {
  const { t, f } = useI18n();
  const m = t.sessionSheet;
  const update = useUpdateLoadingPlate();
  const [value, setValue] = useState(session.plate ? formatPlate(session.plate) : "");

  return (
    <form
      className="space-y-2"
      onSubmit={(event) => {
        event.preventDefault();
        update.mutate({ id: session.id, plate: value.trim() || null }, { onSuccess: (updated) => onChange?.(updated) });
      }}
    >
      <Field>
        <FieldLabel htmlFor="session-plate">{m.plate}</FieldLabel>
        <div className="flex gap-2">
          <Input
            id="session-plate"
            value={value}
            onChange={(event) => setValue(event.target.value.toUpperCase())}
            autoComplete="off"
            spellCheck={false}
            maxLength={16}
            placeholder="01 A 123 BC"
            className="font-mono tracking-wider"
          />
          <Button type="submit" variant="outline" disabled={update.isPending}>
            {update.isPending ? <Spinner /> : <PencilLine aria-hidden />}
            {m.savePlate}
          </Button>
        </div>
        <FieldDescription>
          {session.plate_corrected
            ? m.plateCorrected
            : session.plate_confidence !== null
              ? m.plateConfidence(f.percent(session.plate_confidence))
              : m.plateHint}
        </FieldDescription>
      </Field>
    </form>
  );
}

export function SessionSheet({
  session,
  onOpenChange,
  onChange,
}: {
  session: LoadingSession | null;
  onOpenChange: (open: boolean) => void;
  /** Called with the saved session after the plate is corrected. */
  onChange?: (session: LoadingSession) => void;
}) {
  const { t, f } = useI18n();
  const m = t.sessionSheet;

  return (
    <Sheet open={session !== null} onOpenChange={onOpenChange}>
      <SheetContent className="w-full gap-0 sm:max-w-xl">
        {session ? (
          <>
            <SheetHeader className="border-b">
              <SheetTitle className="flex flex-wrap items-center gap-2">
                {m.title} <PlateBadge plate={session.plate} />
                {session.status === "active" ? <InBayBadge /> : null}
              </SheetTitle>
              <SheetDescription>
                {session.camera_name} · {f.date(session.arrived_at)}
              </SheetDescription>
            </SheetHeader>
            <div className="flex-1 space-y-6 overflow-y-auto p-4">
              <div className="grid grid-cols-2 gap-3">
                <Photo url={session.vehicle_snapshot_url} label={m.vehiclePhoto} />
                <Photo url={session.plate_snapshot_url} label={m.platePhoto} />
              </div>

              <div className="bg-muted/50 flex items-baseline justify-between rounded-lg border p-4">
                <span className="text-muted-foreground text-sm">{m.loaded}</span>
                <span className="text-3xl font-semibold tabular-nums">{f.count(session.loaded)}</span>
              </div>

              <dl className="divide-y">
                <Row label={m.arrived}>{f.time(session.arrived_at)}</Row>
                <Row label={m.loadingStarted}>
                  {session.loading_started_at ? f.time(session.loading_started_at) : "-"}
                </Row>
                <Row label={m.loadingFinished}>
                  {session.loading_finished_at ? f.time(session.loading_finished_at) : "-"}
                </Row>
                <Row label={m.departed}>{session.departed_at ? f.time(session.departed_at) : m.stillHere}</Row>
                <Row label={m.duration}>{f.duration(session.duration_seconds)}</Row>
                {session.unloaded > 0 ? <Row label={m.unloaded}>{f.count(session.unloaded)}</Row> : null}
                <Row label={m.camera}>{session.camera_name}</Row>
                {session.plate_corrected ? (
                  <Row label={m.plate}>
                    <Badge variant="secondary" className="font-normal">
                      {m.plateCorrected}
                    </Badge>
                  </Row>
                ) : null}
              </dl>

              <PlateEditor key={`${session.id}-${session.plate ?? ""}`} session={session} onChange={onChange} />
            </div>
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
