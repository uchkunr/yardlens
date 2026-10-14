import type { Metadata } from "next";

import { OverviewView } from "@/components/overview/overview-view";
import { getMessages } from "@/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  return { title: (await getMessages()).nav.overview };
}

export default function OverviewPage() {
  return <OverviewView />;
}
