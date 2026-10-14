"use client";

import { useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Unplug } from "lucide-react";

import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/i18n/provider";
import { useHealth } from "@/lib/api/queries";

/** Shown when the API health check fails; retry refetches every active query. */
export function BackendBanner() {
  const health = useHealth();
  const queryClient = useQueryClient();
  const { t } = useI18n();

  if (!health.isError) return null;

  const retry = () => {
    void queryClient.refetchQueries({ type: "active" });
  };

  return (
    <div className="px-4 pt-4 lg:px-6">
      <Alert variant="destructive" role="alert">
        <Unplug aria-hidden />
        <AlertTitle>{t.backend.title}</AlertTitle>
        <AlertDescription>{t.backend.description}</AlertDescription>
        <AlertAction>
          <Button size="sm" variant="outline" onClick={retry} disabled={health.isFetching}>
            <RefreshCw className={health.isFetching ? "animate-spin" : undefined} aria-hidden />
            {t.common.retry}
          </Button>
        </AlertAction>
      </Alert>
    </div>
  );
}
