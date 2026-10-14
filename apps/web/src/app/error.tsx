"use client";

import { ErrorState } from "@/components/shared/states";
import { useI18n } from "@/i18n/provider";

export default function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const { t } = useI18n();
  return <ErrorState className="min-h-[60vh]" title={t.errors.generic} error={error} onRetry={reset} />;
}
