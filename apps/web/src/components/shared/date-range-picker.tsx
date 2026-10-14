"use client";

import { addDays, format, parseISO, startOfDay } from "date-fns";
import { CalendarDays } from "lucide-react";
import type { DateRange } from "react-day-picker";
import { enUS as dayPickerEnglish, ru as dayPickerRussian } from "react-day-picker/locale";

import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useI18n } from "@/i18n/provider";

/** Convert inclusive local calendar dates (`yyyy-MM-dd`) to the API's `[since, until)` ISO range. */
export function dateRangeToQuery(from?: string, to?: string): { since?: string; until?: string } {
  return {
    since: from ? startOfDay(parseISO(from)).toISOString() : undefined,
    until: to ? addDays(startOfDay(parseISO(to)), 1).toISOString() : undefined,
  };
}

export function DateRangePicker({
  from,
  to,
  onChange,
}: {
  /** Inclusive local dates, `yyyy-MM-dd`. */
  from?: string;
  to?: string;
  onChange: (range: { from?: string; to?: string }) => void;
}) {
  const { locale, t, f } = useI18n();
  const selected: DateRange | undefined = from
    ? { from: parseISO(from), to: to ? parseISO(to) : undefined }
    : undefined;
  const label = !from
    ? t.filters.anyDate
    : !to || to === from
      ? f.shortDate(from)
      : `${f.shortDate(from)} – ${f.shortDate(to)}`;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" className="min-w-40 justify-start font-normal" aria-label={t.filters.dateRange}>
          <CalendarDays aria-hidden />
          {label}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="start">
        <Calendar
          mode="range"
          locale={locale === "ru" ? dayPickerRussian : dayPickerEnglish}
          numberOfMonths={1}
          selected={selected}
          disabled={{ after: new Date() }}
          onSelect={(range) =>
            onChange({
              from: range?.from ? format(range.from, "yyyy-MM-dd") : undefined,
              to: range?.to ? format(range.to, "yyyy-MM-dd") : undefined,
            })
          }
        />
      </PopoverContent>
    </Popover>
  );
}
