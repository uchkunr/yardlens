import { CircleAlert, Cctv, Plus, RefreshCw } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { errorMessage } from "@/lib/api/client";
import { cn } from "@/lib/utils";

export function ErrorState({
  title = "Couldn't load data",
  error,
  onRetry,
  className,
}: {
  title?: string;
  error: unknown;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <Empty className={cn("border", className)}>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <CircleAlert aria-hidden />
        </EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        <EmptyDescription>{errorMessage(error)}</EmptyDescription>
      </EmptyHeader>
      {onRetry ? (
        <EmptyContent>
          <Button variant="outline" size="sm" onClick={onRetry}>
            <RefreshCw aria-hidden /> Try again
          </Button>
        </EmptyContent>
      ) : null}
    </Empty>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon: ReactNode;
  title: string;
  description: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <Empty className={cn("border border-dashed", className)}>
      <EmptyHeader>
        <EmptyMedia variant="icon">{icon}</EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        <EmptyDescription>{description}</EmptyDescription>
      </EmptyHeader>
      {action ? <EmptyContent>{action}</EmptyContent> : null}
    </Empty>
  );
}

/** First-run state: no cameras configured yet. */
export function NoCamerasState({ className }: { className?: string }) {
  return (
    <EmptyState
      className={className}
      icon={<Cctv aria-hidden />}
      title="Add your first camera"
      description="Connect an RTSP, HLS, MJPEG/JPEG, YouTube live or local video source. Detections and events appear here within seconds."
      action={
        <Button asChild>
          <Link href="/settings?tab=cameras&new=1">
            <Plus aria-hidden /> Add camera
          </Link>
        </Button>
      }
    />
  );
}
