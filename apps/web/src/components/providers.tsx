"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import type { ReactNode } from "react";

import { LiveEventsProvider } from "@/components/shared/live-events-provider";
import { StreamSlotsProvider } from "@/components/shared/live-feed";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ApiError } from "@/lib/api/client";

let browserQueryClient: QueryClient | undefined;

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5_000,
        refetchOnWindowFocus: true,
        // Don't hammer an API that is down or reject requests that can never succeed.
        retry: (failureCount, error) =>
          !(error instanceof ApiError && (error.status === 0 || error.status === 404 || error.status === 422)) &&
          failureCount < 2,
      },
    },
  });
}

function getQueryClient() {
  if (typeof window === "undefined") return makeQueryClient();
  browserQueryClient ??= makeQueryClient();
  return browserQueryClient;
}

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      <QueryClientProvider client={getQueryClient()}>
        <LiveEventsProvider>
          <StreamSlotsProvider>
            <TooltipProvider delayDuration={300}>{children}</TooltipProvider>
          </StreamSlotsProvider>
        </LiveEventsProvider>
        <Toaster position="bottom-right" richColors closeButton />
      </QueryClientProvider>
    </ThemeProvider>
  );
}
