import type { Metadata } from "next";

import { CamerasView } from "@/components/cameras/cameras-view";

export const metadata: Metadata = { title: "Cameras" };

export default function CamerasPage() {
  return <CamerasView />;
}
