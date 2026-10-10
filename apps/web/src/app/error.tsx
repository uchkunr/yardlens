"use client";

import { ErrorState } from "@/components/shared/states";

export default function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <ErrorState className="min-h-[60vh]" title="Something went wrong" error={error} onRetry={reset} />;
}
