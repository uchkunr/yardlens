import type { Metadata } from "next";
import { Suspense } from "react";

import { LoadingView } from "@/components/loading/loading-view";
import { PageSkeleton } from "@/components/shared/page-skeleton";
import { getMessages } from "@/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  return { title: (await getMessages()).nav.loading };
}

export default function LoadingPage() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <LoadingView />
    </Suspense>
  );
}
