import type { Metadata } from "next";

import { CamerasView } from "@/components/cameras/cameras-view";
import { getMessages } from "@/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  return { title: (await getMessages()).nav.cameras };
}

export default function CamerasPage() {
  return <CamerasView />;
}
