import type { ReactNode } from "react";

import { SessionProvider } from "@/components/auth/session-provider";
import { AppHeader } from "@/components/layout/app-header";
import { AppSidebar } from "@/components/layout/app-sidebar";
import { BackendBanner } from "@/components/layout/backend-banner";
import { LiveEventsProvider } from "@/components/shared/live-events-provider";
import { StreamSlotsProvider } from "@/components/shared/live-feed";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";

/** The signed-in app: keeps the session fresh, holds the live connection and the shell. */
export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <SessionProvider>
      <LiveEventsProvider>
        <StreamSlotsProvider>
          <SidebarProvider>
            <AppSidebar />
            <SidebarInset className="min-w-0">
              <AppHeader />
              <BackendBanner />
              <main className="flex flex-1 flex-col gap-6 p-4 lg:p-6">{children}</main>
            </SidebarInset>
          </SidebarProvider>
        </StreamSlotsProvider>
      </LiveEventsProvider>
    </SessionProvider>
  );
}
