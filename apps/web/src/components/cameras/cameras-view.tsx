"use client";

import { Settings2 } from "lucide-react";
import Link from "next/link";

import { CameraTile, CameraTileSkeleton } from "@/components/cameras/camera-tile";
import { PageHeader } from "@/components/shared/page-header";
import { ErrorState, NoCamerasState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { useCameras } from "@/lib/api/queries";

export function CamerasView() {
  const cameras = useCameras();
  const online = cameras.data?.filter((camera) => camera.runtime.status === "online").length ?? 0;

  return (
    <>
      <PageHeader
        title="Cameras"
        description={
          cameras.isSuccess && cameras.data.length > 0
            ? `${online} of ${cameras.data.length} cameras streaming`
            : "Live annotated feeds from every connected camera."
        }
        actions={
          <Button variant="outline" asChild>
            <Link href="/settings?tab=cameras">
              <Settings2 aria-hidden /> Manage cameras
            </Link>
          </Button>
        }
      />
      {cameras.isPending ? (
        <div className="grid gap-4 sm:grid-cols-2 2xl:grid-cols-3">
          {Array.from({ length: 6 }, (_, index) => (
            <CameraTileSkeleton key={index} />
          ))}
        </div>
      ) : cameras.isError ? (
        <ErrorState title="Couldn't load cameras" error={cameras.error} onRetry={() => void cameras.refetch()} />
      ) : cameras.data.length === 0 ? (
        <NoCamerasState className="min-h-96" />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 2xl:grid-cols-3">
          {cameras.data.map((camera) => (
            <CameraTile key={camera.id} camera={camera} />
          ))}
        </div>
      )}
    </>
  );
}
