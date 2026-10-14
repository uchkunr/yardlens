"use client";

import { CircleAlert, Cctv, Compass, Plus, RefreshCw } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { useI18n } from "@/i18n/provider";
import { cn } from "@/lib/utils";

export function ErrorState({
  title,
  error,
  onRetry,
  className,
}: {
  title?: string;
  error: unknown;
  onRetry?: () => void;
  className?: string;
}) {
  const { t, f } = useI18n();
  return (
    <Empty className={cn("border", className)}>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <CircleAlert aria-hidden />
        </EmptyMedia>
        <EmptyTitle>{title ?? t.errors.loadData}</EmptyTitle>
        <EmptyDescription>{f.error(error)}</EmptyDescription>
      </EmptyHeader>
      {onRetry ? (
        <EmptyContent>
          <Button variant="outline" size="sm" onClick={onRetry}>
            <RefreshCw aria-hidden /> {t.common.tryAgain}
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
  const { t } = useI18n();
  return (
    <EmptyState
      className={className}
      icon={<Cctv aria-hidden />}
      title={t.noCameras.title}
      description={t.noCameras.description}
      action={
        <Button asChild>
          <Link href="/settings?tab=cameras&new=1">
            <Plus aria-hidden /> {t.noCameras.action}
          </Link>
        </Button>
      }
    />
  );
}

export function NotFoundState({ className }: { className?: string }) {
  const { t } = useI18n();
  return (
    <EmptyState
      className={className}
      icon={<Compass aria-hidden />}
      title={t.notFound.title}
      description={t.notFound.description}
      action={
        <Button asChild>
          <Link href="/">{t.notFound.back}</Link>
        </Button>
      }
    />
  );
}
