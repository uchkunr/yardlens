import type { Metadata } from "next";
import { Suspense } from "react";

import { EventsView } from "@/components/events/events-view";
import { PageSkeleton } from "@/components/shared/page-skeleton";

export const metadata: Metadata = { title: "Events" };

export default function EventsPage() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <EventsView />
    </Suspense>
  );
}
