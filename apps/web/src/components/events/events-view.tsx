"use client";

import { ChevronLeft, ChevronRight, ListFilter, RefreshCw } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { EventThumbnail } from "@/components/events/event-list";
import { EventSheet } from "@/components/events/event-sheet";
import { dateRangeToQuery, EventsFilters, type EventsFilterState } from "@/components/events/events-filters";
import { useLiveEvents } from "@/components/shared/live-events-provider";
import { PageHeader } from "@/components/shared/page-header";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { EventTypeBadge } from "@/components/shared/status";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Pagination, PaginationContent, PaginationItem } from "@/components/ui/pagination";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { EventType, YardEvent } from "@/lib/api/client";
import { useCameras, useEvents, useSettings } from "@/lib/api/queries";
import { classLabel, EVENT_TYPES, formatCount, formatDateTime } from "@/lib/format";

const PAGE_SIZES = [25, 50, 100] as const;

function readState(params: URLSearchParams) {
  const camera = Number(params.get("camera"));
  const type = params.get("type");
  const page = Number(params.get("page"));
  const size = Number(params.get("size"));
  const filters: EventsFilterState = {
    cameraId: Number.isInteger(camera) && camera > 0 ? camera : undefined,
    type: type && (EVENT_TYPES as string[]).includes(type) ? (type as EventType) : undefined,
    className: params.get("class") ?? undefined,
    from: params.get("from") ?? undefined,
    to: params.get("to") ?? undefined,
  };
  return {
    filters,
    page: Number.isInteger(page) && page > 0 ? page : 1,
    pageSize: (PAGE_SIZES as readonly number[]).includes(size) ? size : PAGE_SIZES[0],
  };
}

export function EventsView() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { filters, page, pageSize } = readState(searchParams);
  const [selected, setSelected] = useState<YardEvent | null>(null);
  const { unseenCount, markSeen } = useLiveEvents();

  const cameras = useCameras();
  const settings = useSettings();
  const query = useMemo(
    () => ({
      cameraId: filters.cameraId,
      types: filters.type ? [filters.type] : undefined,
      className: filters.className,
      ...dateRangeToQuery(filters.from, filters.to),
      page,
      pageSize,
    }),
    [filters.cameraId, filters.type, filters.className, filters.from, filters.to, page, pageSize],
  );
  const events = useEvents(query);

  useEffect(() => markSeen(), [markSeen]);

  const update = (next: EventsFilterState, nextPage = 1, nextSize = pageSize) => {
    const params = new URLSearchParams();
    if (next.cameraId) params.set("camera", String(next.cameraId));
    if (next.type) params.set("type", next.type);
    if (next.className) params.set("class", next.className);
    if (next.from) params.set("from", next.from);
    if (next.to) params.set("to", next.to);
    if (nextPage > 1) params.set("page", String(nextPage));
    if (nextSize !== PAGE_SIZES[0]) params.set("size", String(nextSize));
    const search = params.toString();
    router.replace(search ? `${pathname}?${search}` : pathname, { scroll: false });
  };

  const refresh = () => {
    markSeen();
    void events.refetch();
  };

  const total = events.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const classes = settings.data?.detection.classes ?? [];

  return (
    <>
      <PageHeader
        title="Events"
        description="Every detection, line crossing and camera status change."
        actions={
          unseenCount > 0 ? (
            <Button variant="outline" onClick={refresh}>
              <RefreshCw aria-hidden /> {formatCount(unseenCount)} new
            </Button>
          ) : null
        }
      />

      <EventsFilters
        value={filters}
        onChange={(next) => update(next)}
        cameras={cameras.data ?? []}
        classes={classes}
      />

      {events.isError ? (
        <ErrorState title="Couldn't load events" error={events.error} onRetry={() => void events.refetch()} />
      ) : (
        <Card className="gap-0 overflow-hidden py-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-28 pl-4">Snapshot</TableHead>
                <TableHead>Event</TableHead>
                <TableHead>Class</TableHead>
                <TableHead className="hidden lg:table-cell">Camera</TableHead>
                <TableHead className="hidden text-right xl:table-cell">Confidence</TableHead>
                <TableHead className="pr-4 text-right">Time</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody aria-busy={events.isFetching}>
              {events.isPending ? (
                Array.from({ length: 8 }, (_, index) => (
                  <TableRow key={index}>
                    <TableCell className="pl-4">
                      <Skeleton className="aspect-video w-20 rounded-md" />
                    </TableCell>
                    {Array.from({ length: 5 }, (_, cell) => (
                      <TableCell key={cell} className={cell === 2 ? "hidden lg:table-cell" : cell === 3 ? "hidden xl:table-cell" : undefined}>
                        <Skeleton className="h-4 w-20" />
                      </TableCell>
                    ))}
                  </TableRow>
                ))
              ) : events.data.items.length === 0 ? (
                <TableRow className="hover:bg-transparent">
                  <TableCell colSpan={6} className="p-6">
                    <EmptyState
                      className="border-none"
                      icon={<ListFilter aria-hidden />}
                      title="No events found"
                      description={
                        Object.values(filters).some(Boolean)
                          ? "Try widening the filters or the date range."
                          : "Events appear here as soon as a camera detects something."
                      }
                    />
                  </TableCell>
                </TableRow>
              ) : (
                events.data.items.map((event) => (
                  <TableRow
                    key={event.id}
                    tabIndex={0}
                    className="focus-visible:bg-muted cursor-pointer outline-none"
                    onClick={() => setSelected(event)}
                    onKeyDown={(keyboard) => {
                      if (keyboard.key === "Enter" || keyboard.key === " ") {
                        keyboard.preventDefault();
                        setSelected(event);
                      }
                    }}
                    aria-label={`Open event ${event.id}`}
                  >
                    <TableCell className="pl-4">
                      <EventThumbnail event={event} />
                    </TableCell>
                    <TableCell>
                      <EventTypeBadge type={event.type} />
                    </TableCell>
                    <TableCell>{classLabel(event.class_name)}</TableCell>
                    <TableCell className="hidden max-w-48 truncate lg:table-cell">{event.camera_name}</TableCell>
                    <TableCell className="text-muted-foreground hidden text-right tabular-nums xl:table-cell">
                      {event.confidence !== null ? `${Math.round(event.confidence * 100)}%` : "—"}
                    </TableCell>
                    <TableCell className="text-muted-foreground pr-4 text-right whitespace-nowrap tabular-nums">
                      <time dateTime={event.created_at}>{formatDateTime(event.created_at)}</time>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </Card>
      )}

      {events.isSuccess && total > 0 ? (
        <div className="flex flex-col-reverse items-center justify-between gap-3 sm:flex-row">
          <div className="text-muted-foreground flex items-center gap-2 text-sm">
            <span className="tabular-nums">
              {formatCount((page - 1) * pageSize + 1)}–{formatCount(Math.min(page * pageSize, total))} of{" "}
              {formatCount(total)}
            </span>
            <Select value={String(pageSize)} onValueChange={(size) => update(filters, 1, Number(size))}>
              <SelectTrigger size="sm" className="w-24" aria-label="Rows per page">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PAGE_SIZES.map((size) => (
                  <SelectItem key={size} value={String(size)}>
                    {size} / page
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Pagination className="mx-0 w-auto">
            <PaginationContent>
              <PaginationItem>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page <= 1}
                  onClick={() => update(filters, page - 1)}
                  aria-label="Previous page"
                >
                  <ChevronLeft aria-hidden /> Previous
                </Button>
              </PaginationItem>
              <PaginationItem className="text-muted-foreground px-2 text-sm tabular-nums">
                Page {page} of {pageCount}
              </PaginationItem>
              <PaginationItem>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= pageCount}
                  onClick={() => update(filters, page + 1)}
                  aria-label="Next page"
                >
                  Next <ChevronRight aria-hidden />
                </Button>
              </PaginationItem>
            </PaginationContent>
          </Pagination>
        </div>
      ) : null}

      <EventSheet event={selected} onOpenChange={(open) => !open && setSelected(null)} />
    </>
  );
}
