"use client";

import { addDays, format, parseISO, startOfDay } from "date-fns";
import { CalendarDays, X } from "lucide-react";
import type { DateRange } from "react-day-picker";

import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { Camera, EventType } from "@/lib/api/client";
import { classLabel, EVENT_TYPE_LABELS, EVENT_TYPES } from "@/lib/format";

export type EventsFilterState = {
  cameraId?: number;
  type?: EventType;
  className?: string;
  /** Inclusive local dates, `yyyy-MM-dd`. */
  from?: string;
  to?: string;
};

const ALL = "all";

/** Convert inclusive local calendar dates to the API's `[since, until)` ISO range. */
export function dateRangeToQuery(from?: string, to?: string): { since?: string; until?: string } {
  return {
    since: from ? startOfDay(parseISO(from)).toISOString() : undefined,
    until: to ? addDays(startOfDay(parseISO(to)), 1).toISOString() : undefined,
  };
}

function rangeLabel(from?: string, to?: string) {
  if (!from) return "Any date";
  const start = format(parseISO(from), "MMM d");
  if (!to || to === from) return start;
  return `${start} – ${format(parseISO(to), "MMM d")}`;
}

export function EventsFilters({
  value,
  onChange,
  cameras,
  classes,
}: {
  value: EventsFilterState;
  onChange: (next: EventsFilterState) => void;
  cameras: Camera[];
  classes: string[];
}) {
  const selectedRange: DateRange | undefined = value.from
    ? { from: parseISO(value.from), to: value.to ? parseISO(value.to) : undefined }
    : undefined;
  const active = Boolean(value.cameraId ?? value.type ?? value.className ?? value.from);

  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Event filters">
      <Select
        value={value.cameraId ? String(value.cameraId) : ALL}
        onValueChange={(next) => onChange({ ...value, cameraId: next === ALL ? undefined : Number(next) })}
      >
        <SelectTrigger className="w-44" aria-label="Camera">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>All cameras</SelectItem>
          {cameras.map((camera) => (
            <SelectItem key={camera.id} value={String(camera.id)}>
              {camera.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={value.type ?? ALL}
        onValueChange={(next) => onChange({ ...value, type: next === ALL ? undefined : (next as EventType) })}
      >
        <SelectTrigger className="w-40" aria-label="Event type">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>All types</SelectItem>
          {EVENT_TYPES.map((type) => (
            <SelectItem key={type} value={type}>
              {EVENT_TYPE_LABELS[type]}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={value.className ?? ALL}
        onValueChange={(next) => onChange({ ...value, className: next === ALL ? undefined : next })}
      >
        <SelectTrigger className="w-36" aria-label="Object class">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>All classes</SelectItem>
          {classes.map((name) => (
            <SelectItem key={name} value={name}>
              {classLabel(name)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Popover>
        <PopoverTrigger asChild>
          <Button variant="outline" className="min-w-40 justify-start font-normal" aria-label="Date range">
            <CalendarDays aria-hidden />
            {rangeLabel(value.from, value.to)}
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-auto p-0" align="start">
          <Calendar
            mode="range"
            numberOfMonths={1}
            selected={selectedRange}
            disabled={{ after: new Date() }}
            onSelect={(range) =>
              onChange({
                ...value,
                from: range?.from ? format(range.from, "yyyy-MM-dd") : undefined,
                to: range?.to ? format(range.to, "yyyy-MM-dd") : undefined,
              })
            }
          />
        </PopoverContent>
      </Popover>

      {active ? (
        <Button variant="ghost" onClick={() => onChange({})}>
          <X aria-hidden /> Reset
        </Button>
      ) : null}
    </div>
  );
}
