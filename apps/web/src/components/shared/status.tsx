import { Badge } from "@/components/ui/badge";
import type { CameraStatus, EventType } from "@/lib/api/client";
import { EVENT_TYPE_LABELS, STATUS_LABELS } from "@/lib/format";
import { cn } from "@/lib/utils";

const DOT_COLORS: Record<CameraStatus, string> = {
  online: "bg-success",
  connecting: "bg-warning",
  offline: "bg-muted-foreground/60",
  error: "bg-destructive",
  disabled: "bg-muted-foreground/30",
};

export function StatusDot({ status, className }: { status: CameraStatus; className?: string }) {
  const pulsing = status === "online" || status === "connecting";
  return (
    <span className={cn("relative inline-flex size-2 shrink-0", className)} aria-hidden>
      {pulsing ? (
        <span className={cn("absolute inline-flex size-full animate-ping rounded-full opacity-60", DOT_COLORS[status])} />
      ) : null}
      <span className={cn("relative inline-flex size-2 rounded-full", DOT_COLORS[status])} />
    </span>
  );
}

export function StatusBadge({ status, className }: { status: CameraStatus; className?: string }) {
  return (
    <Badge variant="outline" className={cn("gap-1.5 font-normal", className)}>
      <StatusDot status={status} />
      {STATUS_LABELS[status]}
    </Badge>
  );
}

const EVENT_TONES: Record<EventType, string> = {
  object_entered: "bg-chart-1/12 text-chart-1 border-chart-1/25",
  line_crossed_in: "bg-chart-2/12 text-chart-2 border-chart-2/25",
  line_crossed_out: "bg-chart-3/15 text-chart-3 border-chart-3/30",
  camera_online: "bg-success/12 text-success border-success/25",
  camera_offline: "bg-destructive/10 text-destructive border-destructive/25",
};

export function EventTypeBadge({ type, className }: { type: EventType; className?: string }) {
  return (
    <Badge variant="outline" className={cn("font-normal whitespace-nowrap", EVENT_TONES[type], className)}>
      {EVENT_TYPE_LABELS[type]}
    </Badge>
  );
}
