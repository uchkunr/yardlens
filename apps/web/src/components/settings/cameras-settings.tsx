"use client";

import { Cctv, MoreHorizontal, Pencil, Plus, Ruler, Trash2 } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { CameraFormDialog } from "@/components/settings/camera-form-dialog";
import { LineEditorDialog } from "@/components/settings/line-editor-dialog";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { StatusBadge } from "@/components/shared/status";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { Camera } from "@/lib/api/client";
import { useCameras, useDeleteCamera, useToggleCamera } from "@/lib/api/queries";
import { SOURCE_TYPE_LABELS } from "@/lib/format";

type DialogState = { kind: "form"; camera?: Camera } | { kind: "line"; camera: Camera } | { kind: "delete"; camera: Camera };

/** Deep links from other pages: `?new=1`, `?edit=<id>`, `?line=<id>`. */
function dialogFromParams(params: URLSearchParams, cameras: Camera[] | undefined): DialogState | null {
  if (params.get("new") === "1") return { kind: "form" };
  const find = (key: string) => cameras?.find((camera) => String(camera.id) === params.get(key));
  const editing = find("edit");
  if (editing) return { kind: "form", camera: editing };
  const lining = find("line");
  if (lining) return { kind: "line", camera: lining };
  return null;
}

export function CamerasSettings() {
  const cameras = useCameras();
  const toggleCamera = useToggleCamera();
  const deleteCamera = useDeleteCamera();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [localDialog, setLocalDialog] = useState<DialogState | null>(null);

  const linkedDialog = dialogFromParams(searchParams, cameras.data);
  const dialog = localDialog ?? linkedDialog;
  // Always render dialogs against fresh camera data (status/line change while open).
  const current = dialog && "camera" in dialog && dialog.camera
    ? (cameras.data?.find((camera) => camera.id === dialog.camera?.id) ?? dialog.camera)
    : undefined;

  const close = () => {
    setLocalDialog(null);
    if (linkedDialog) router.replace(`${pathname}?tab=cameras`, { scroll: false });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Cameras</CardTitle>
        <CardDescription>Video sources, inference rate and counting lines.</CardDescription>
        <CardAction>
          <Button onClick={() => setLocalDialog({ kind: "form" })}>
            <Plus aria-hidden /> Add camera
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        {cameras.isPending ? (
          <div className="space-y-3">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-12 w-full" />
            ))}
          </div>
        ) : cameras.isError ? (
          <ErrorState title="Couldn't load cameras" error={cameras.error} onRetry={() => void cameras.refetch()} />
        ) : cameras.data.length === 0 ? (
          <EmptyState
            icon={<Cctv aria-hidden />}
            title="No cameras yet"
            description="Add an RTSP, HLS, MJPEG/JPEG, YouTube live or local video source to start detecting."
            action={
              <Button onClick={() => setLocalDialog({ kind: "form" })}>
                <Plus aria-hidden /> Add camera
              </Button>
            }
          />
        ) : (
          <div className="-mx-6 overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-6">Camera</TableHead>
                  <TableHead className="hidden md:table-cell">Source</TableHead>
                  <TableHead className="hidden sm:table-cell">Status</TableHead>
                  <TableHead className="hidden text-right lg:table-cell">FPS</TableHead>
                  <TableHead className="hidden lg:table-cell">Line</TableHead>
                  <TableHead className="text-center">Enabled</TableHead>
                  <TableHead className="w-12 pr-6">
                    <span className="sr-only">Actions</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {cameras.data.map((camera) => (
                  <TableRow key={camera.id}>
                    <TableCell className="max-w-64 pl-6">
                      <div className="truncate font-medium">{camera.name}</div>
                      <div className="text-muted-foreground truncate font-mono text-xs" title={camera.url}>
                        {camera.url}
                      </div>
                    </TableCell>
                    <TableCell className="text-muted-foreground hidden md:table-cell">
                      {SOURCE_TYPE_LABELS[camera.source_type]}
                    </TableCell>
                    <TableCell className="hidden sm:table-cell">
                      <StatusBadge status={camera.runtime.status} />
                    </TableCell>
                    <TableCell className="hidden text-right tabular-nums lg:table-cell">{camera.inference_fps}</TableCell>
                    <TableCell className="text-muted-foreground hidden lg:table-cell">
                      {camera.line ? "Configured" : "—"}
                    </TableCell>
                    <TableCell className="text-center">
                      <Switch
                        checked={camera.enabled}
                        onCheckedChange={(enabled) => toggleCamera.mutate({ id: camera.id, enabled })}
                        aria-label={`${camera.enabled ? "Disable" : "Enable"} ${camera.name}`}
                      />
                    </TableCell>
                    <TableCell className="pr-6">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="icon" aria-label={`Actions for ${camera.name}`}>
                            <MoreHorizontal aria-hidden />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onSelect={() => setLocalDialog({ kind: "form", camera })}>
                            <Pencil aria-hidden /> Edit
                          </DropdownMenuItem>
                          <DropdownMenuItem onSelect={() => setLocalDialog({ kind: "line", camera })}>
                            <Ruler aria-hidden /> Counting line
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem variant="destructive" onSelect={() => setLocalDialog({ kind: "delete", camera })}>
                            <Trash2 aria-hidden /> Delete
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>

      <CameraFormDialog
        open={dialog?.kind === "form"}
        onOpenChange={(open) => !open && close()}
        camera={dialog?.kind === "form" ? current : undefined}
      />
      {dialog?.kind === "line" && current ? (
        <LineEditorDialog key={current.id} camera={current} onOpenChange={(open) => !open && close()} />
      ) : null}
      <AlertDialog open={dialog?.kind === "delete"} onOpenChange={(open) => !open && close()}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {current?.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              The camera stops immediately and all of its events and snapshots are permanently removed.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                if (current) deleteCamera.mutate(current);
                close();
              }}
            >
              Delete camera
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}
