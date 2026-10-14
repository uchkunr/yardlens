"use client";

import { ChevronLeft, ChevronRight, Download, Search, Truck, X } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { SessionSheet } from "@/components/loading/session-sheet";
import { SessionTable, SessionTableSkeleton } from "@/components/loading/session-table";
import { PageHeader } from "@/components/shared/page-header";
import { DateRangePicker, dateRangeToQuery } from "@/components/shared/date-range-picker";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Pagination, PaginationContent, PaginationItem } from "@/components/ui/pagination";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useI18n } from "@/i18n/provider";
import { loadingExportUrl, type LoadingSession } from "@/lib/api/client";
import { browserTimeZone, useCameras, useLoadingSessions } from "@/lib/api/queries";

const PAGE_SIZE = 25;
const ALL = "all";
const SEARCH_DELAY_MS = 300;

type LoadingViewState = { cameraId?: number; plate?: string; from?: string; to?: string; page: number };

function readState(params: URLSearchParams): LoadingViewState {
  const camera = Number(params.get("camera"));
  const page = Number(params.get("page"));
  return {
    cameraId: Number.isInteger(camera) && camera > 0 ? camera : undefined,
    plate: params.get("plate") ?? undefined,
    from: params.get("from") ?? undefined,
    to: params.get("to") ?? undefined,
    page: Number.isInteger(page) && page > 0 ? page : 1,
  };
}

function Total({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="bg-card rounded-xl border p-4">
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
    </div>
  );
}

/** Loading history: one row per vehicle, filterable, with totals and an Excel export. */
export function LoadingView() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const state = readState(searchParams);
  const { t, f, locale } = useI18n();
  const m = t.loadingPage;

  const cameras = useCameras();
  const bays = cameras.data?.filter((camera) => camera.zone) ?? [];
  const filters = { cameraId: state.cameraId, plate: state.plate, ...dateRangeToQuery(state.from, state.to) };
  const sessions = useLoadingSessions({ ...filters, page: state.page, pageSize: PAGE_SIZE });

  const [selected, setSelected] = useState<LoadingSession | null>(null);
  // Show the latest version of the open session (live counter, corrected plate).
  const current = selected ? (sessions.data?.items.find((item) => item.id === selected.id) ?? selected) : null;

  const update = (next: Partial<LoadingViewState>) => {
    const merged = { ...state, page: 1, ...next };
    const params = new URLSearchParams();
    if (merged.cameraId) params.set("camera", String(merged.cameraId));
    if (merged.plate) params.set("plate", merged.plate);
    if (merged.from) params.set("from", merged.from);
    if (merged.to) params.set("to", merged.to);
    if (merged.page > 1) params.set("page", String(merged.page));
    const search = params.toString();
    router.replace(search ? `${pathname}?${search}` : pathname, { scroll: false });
  };

  const [plateInput, setPlateInput] = useState(state.plate ?? "");
  useEffect(() => {
    if (plateInput === (state.plate ?? "")) return;
    const timer = setTimeout(() => update({ plate: plateInput.trim() || undefined }), SEARCH_DELAY_MS);
    return () => clearTimeout(timer);
    // `update` is rebuilt every render; only the typed text should restart the timer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plateInput]);

  const filtered = Boolean(state.cameraId ?? state.plate ?? state.from);
  const total = sessions.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const totals = sessions.data?.totals;

  return (
    <>
      <PageHeader
        title={t.nav.loading}
        description={m.description}
        actions={
          <Button asChild variant="outline">
            <a href={loadingExportUrl(filters, { tz: browserTimeZone(), lang: locale })} download>
              <Download aria-hidden /> {m.export}
            </a>
          </Button>
        }
      />

      <div className="flex flex-wrap items-center gap-2" role="group" aria-label={t.filters.group}>
        <div className="relative w-full sm:w-56">
          <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" aria-hidden />
          <Input
            value={plateInput}
            onChange={(event) => setPlateInput(event.target.value.toUpperCase())}
            placeholder={m.search}
            aria-label={m.search}
            className="pl-8 font-mono tracking-wider placeholder:font-sans placeholder:tracking-normal"
          />
        </div>
        <DateRangePicker from={state.from} to={state.to} onChange={(range) => update(range)} />
        {bays.length > 1 ? (
          <Select
            value={state.cameraId ? String(state.cameraId) : ALL}
            onValueChange={(next) => update({ cameraId: next === ALL ? undefined : Number(next) })}
          >
            <SelectTrigger className="w-44" aria-label={t.filters.camera}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t.filters.allCameras}</SelectItem>
              {bays.map((camera) => (
                <SelectItem key={camera.id} value={String(camera.id)}>
                  {camera.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : null}
        {filtered ? (
          <Button
            variant="ghost"
            onClick={() => {
              setPlateInput("");
              update({ cameraId: undefined, plate: undefined, from: undefined, to: undefined });
            }}
          >
            <X aria-hidden /> {t.filters.reset}
          </Button>
        ) : null}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {totals ? (
          <>
            <Total label={m.vehicles} value={f.count(totals.vehicles)} />
            <Total label={m.loaded} value={f.count(totals.loaded)} />
            <Total
              label={m.average}
              value={totals.average_duration_seconds !== null ? f.duration(totals.average_duration_seconds) : "—"}
            />
          </>
        ) : (
          Array.from({ length: 3 }, (_, index) => <Skeleton key={index} className="h-[78px] rounded-xl" />)
        )}
      </div>

      {sessions.isError ? (
        <ErrorState title={m.loadError} error={sessions.error} onRetry={() => void sessions.refetch()} />
      ) : (
        <Card className="gap-0 overflow-hidden py-0">
          {sessions.isPending ? (
            <SessionTableSkeleton rows={8} />
          ) : sessions.data.items.length === 0 ? (
            <EmptyState
              className="m-6 border-none"
              icon={<Truck aria-hidden />}
              title={filtered ? t.events.emptyTitle : t.loading.noSessionsTitle}
              description={filtered ? m.emptyFiltered : t.loading.noSessionsDescription}
            />
          ) : (
            <SessionTable
              sessions={sessions.data.items}
              onSelect={setSelected}
              showDate
              showCamera={bays.length > 1}
            />
          )}
        </Card>
      )}

      {sessions.isSuccess && total > PAGE_SIZE ? (
        <Pagination className="justify-end">
          <PaginationContent>
            <PaginationItem>
              <Button
                variant="outline"
                size="sm"
                disabled={state.page <= 1}
                onClick={() => update({ page: state.page - 1 })}
                aria-label={t.events.previousPage}
              >
                <ChevronLeft aria-hidden /> {t.events.previous}
              </Button>
            </PaginationItem>
            <PaginationItem className="text-muted-foreground px-2 text-sm tabular-nums">
              {t.events.pageOf(f.count(state.page), f.count(pageCount))}
            </PaginationItem>
            <PaginationItem>
              <Button
                variant="outline"
                size="sm"
                disabled={state.page >= pageCount}
                onClick={() => update({ page: state.page + 1 })}
                aria-label={t.events.nextPage}
              >
                {t.events.next} <ChevronRight aria-hidden />
              </Button>
            </PaginationItem>
          </PaginationContent>
        </Pagination>
      ) : null}

      <SessionSheet session={current} onOpenChange={(open) => !open && setSelected(null)} onChange={setSelected} />
    </>
  );
}
