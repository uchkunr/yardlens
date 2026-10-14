"use client";

import { X } from "lucide-react";

import { DateRangePicker } from "@/components/shared/date-range-picker";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useI18n } from "@/i18n/provider";
import type { Camera, EventType } from "@/lib/api/client";
import { EVENT_TYPES } from "@/lib/format";

export { dateRangeToQuery } from "@/components/shared/date-range-picker";

export type EventsFilterState = {
  cameraId?: number;
  type?: EventType;
  className?: string;
  /** Inclusive local dates, `yyyy-MM-dd`. */
  from?: string;
  to?: string;
};

const ALL = "all";

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
  const active = Boolean(value.cameraId ?? value.type ?? value.className ?? value.from);
  const { t, f } = useI18n();

  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label={t.filters.group}>
      <Select
        value={value.cameraId ? String(value.cameraId) : ALL}
        onValueChange={(next) => onChange({ ...value, cameraId: next === ALL ? undefined : Number(next) })}
      >
        <SelectTrigger className="w-44" aria-label={t.filters.camera}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>{t.filters.allCameras}</SelectItem>
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
        <SelectTrigger className="w-40" aria-label={t.filters.type}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>{t.filters.allTypes}</SelectItem>
          {EVENT_TYPES.map((type) => (
            <SelectItem key={type} value={type}>
              {t.eventTypes[type]}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={value.className ?? ALL}
        onValueChange={(next) => onChange({ ...value, className: next === ALL ? undefined : next })}
      >
        <SelectTrigger className="w-36" aria-label={t.filters.class}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>{t.filters.allClasses}</SelectItem>
          {classes.map((name) => (
            <SelectItem key={name} value={name}>
              {f.className(name)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <DateRangePicker from={value.from} to={value.to} onChange={(range) => onChange({ ...value, ...range })} />

      {active ? (
        <Button variant="ghost" onClick={() => onChange({})}>
          <X aria-hidden /> {t.filters.reset}
        </Button>
      ) : null}
    </div>
  );
}
