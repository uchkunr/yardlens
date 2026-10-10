"use client";

import { useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Unplug } from "lucide-react";

import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useHealth } from "@/lib/api/queries";

/** Shown when the API health check fails; retry refetches every active query. */
export function BackendBanner() {
  const health = useHealth();
  const queryClient = useQueryClient();

  if (!health.isError) return null;

  const retry = () => {
    void queryClient.refetchQueries({ type: "active" });
  };

  return (
    <div className="px-4 pt-4 lg:px-6">
      <Alert variant="destructive" role="alert">
        <Unplug aria-hidden />
        <AlertTitle>Backend unavailable</AlertTitle>
        <AlertDescription>
          Can&apos;t reach the YardLens API. Live feeds and data will resume automatically once it is back.
        </AlertDescription>
        <AlertAction>
          <Button size="sm" variant="outline" onClick={retry} disabled={health.isFetching}>
            <RefreshCw className={health.isFetching ? "animate-spin" : undefined} aria-hidden />
            Retry
          </Button>
        </AlertAction>
      </Alert>
    </div>
  );
}
