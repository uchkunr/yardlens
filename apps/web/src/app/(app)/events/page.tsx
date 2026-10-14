import type { Metadata } from "next";
import { Suspense } from "react";

import { EventsView } from "@/components/events/events-view";
import { PageSkeleton } from "@/components/shared/page-skeleton";
import { getMessages } from "@/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  return { title: (await getMessages()).nav.events };
}

export default function EventsPage() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <EventsView />
    </Suspense>
  );
}
