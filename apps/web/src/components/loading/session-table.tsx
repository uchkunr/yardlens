"use client";

import { PlateBadge } from "@/components/loading/plate-badge";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n/provider";
import type { LoadingSession } from "@/lib/api/client";
import { formatPlate } from "@/lib/format";

export function InBayBadge() {
  const { t } = useI18n();
  return (
    <Badge variant="outline" className="border-success/30 bg-success/10 text-success gap-1.5 font-normal">
      <span className="bg-success size-1.5 animate-pulse rounded-full" aria-hidden />
      {t.loadingPage.inBay}
    </Badge>
  );
}

/** One row per vehicle; the same table on the overview and on the history page. */
export function SessionTable({
  sessions,
  onSelect,
  showDate = false,
  showCamera = false,
  compact = false,
}: {
  sessions: LoadingSession[];
  onSelect: (session: LoadingSession) => void;
  showDate?: boolean;
  showCamera?: boolean;
  /** Plate, arrival and count only, for narrow cards; vehicles still in the bay are marked. */
  compact?: boolean;
}) {
  const { t, f } = useI18n();
  const m = t.loadingPage;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="pl-4">{m.plate}</TableHead>
          {showDate ? <TableHead>{m.date}</TableHead> : null}
          <TableHead>{m.arrived}</TableHead>
          {compact ? null : <TableHead>{m.departed}</TableHead>}
          {compact ? null : <TableHead className="hidden sm:table-cell">{m.duration}</TableHead>}
          {showCamera ? <TableHead className="hidden lg:table-cell">{m.camera}</TableHead> : null}
          <TableHead className="pr-4 text-right">{m.loaded}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {sessions.map((session) => (
          <TableRow
            key={session.id}
            tabIndex={0}
            className="focus-visible:bg-muted cursor-pointer outline-none"
            onClick={() => onSelect(session)}
            onKeyDown={(keyboard) => {
              if (keyboard.key === "Enter" || keyboard.key === " ") {
                keyboard.preventDefault();
                onSelect(session);
              }
            }}
            aria-label={m.open(session.plate ? formatPlate(session.plate) : t.loading.plateUnknown)}
          >
            <TableCell className="pl-4">
              <span className="flex flex-wrap items-center gap-2">
                <PlateBadge plate={session.plate} />
                {compact && !session.departed_at ? <InBayBadge /> : null}
              </span>
            </TableCell>
            {showDate ? <TableCell className="tabular-nums">{f.date(session.arrived_at)}</TableCell> : null}
            <TableCell className="tabular-nums">{f.time(session.arrived_at)}</TableCell>
            {compact ? null : (
              <TableCell className="tabular-nums">
                {session.departed_at ? f.time(session.departed_at) : <InBayBadge />}
              </TableCell>
            )}
            {compact ? null : (
              <TableCell className="text-muted-foreground hidden tabular-nums sm:table-cell">
                {f.duration(session.duration_seconds)}
              </TableCell>
            )}
            {showCamera ? (
              <TableCell className="text-muted-foreground hidden max-w-40 truncate lg:table-cell">
                {session.camera_name}
              </TableCell>
            ) : null}
            <TableCell className="pr-4 text-right text-base font-semibold tabular-nums">
              {f.count(session.loaded)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function SessionTableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-2 p-4">
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} className="h-9 w-full" />
      ))}
    </div>
  );
}
