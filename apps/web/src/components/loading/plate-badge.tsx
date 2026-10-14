"use client";

import { useI18n } from "@/i18n/provider";
import { formatPlate } from "@/lib/format";
import { cn } from "@/lib/utils";

/** A number plate drawn like one: black on white with a frame, in either theme. */
export function PlateBadge({ plate, className }: { plate: string | null | undefined; className?: string }) {
  const { t } = useI18n();
  if (!plate) {
    return <span className={cn("text-muted-foreground text-sm", className)}>{t.loading.plateUnknown}</span>;
  }
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md border-2 border-neutral-800 bg-white px-2 py-0.5 font-mono text-sm font-bold tracking-wider whitespace-nowrap text-neutral-950",
        className,
      )}
    >
      {formatPlate(plate)}
    </span>
  );
}
