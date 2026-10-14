import type { Metadata } from "next";
import { Suspense } from "react";

import { PageSkeleton } from "@/components/shared/page-skeleton";
import { SettingsView } from "@/components/settings/settings-view";
import { getMessages } from "@/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  return { title: (await getMessages()).nav.settings };
}

export default function SettingsPage() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <SettingsView />
    </Suspense>
  );
}
