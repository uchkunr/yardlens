import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { CameraDetail, CameraDetailSkeleton } from "@/components/cameras/camera-detail";

export const metadata: Metadata = { title: "Camera" };

async function CameraDetailRoute({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const cameraId = Number(id);
  if (!Number.isInteger(cameraId) || cameraId <= 0) notFound();
  return <CameraDetail cameraId={cameraId} />;
}

export default function CameraPage({ params }: PageProps<"/cameras/[id]">) {
  return (
    <Suspense fallback={<CameraDetailSkeleton />}>
      <CameraDetailRoute params={params} />
    </Suspense>
  );
}
