"use client";

import { Cctv, MoreHorizontal, Pencil, Plus, Ruler, ScanSearch, SquareDashed, Trash2 } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { CameraDetectionDialog } from "@/components/settings/camera-detection-dialog";
import { CameraFormDialog } from "@/components/settings/camera-form-dialog";
import { LineEditorDialog } from "@/components/settings/line-editor-dialog";
import { ZoneEditorDialog } from "@/components/settings/zone-editor-dialog";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { StatusBadge } from "@/components/shared/status";
import { Badge } from "@/components/ui/badge";
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
import { useI18n } from "@/i18n/provider";
import type { Camera } from "@/lib/api/client";
import { useCameras, useDeleteCamera, useSettings, useToggleCamera } from "@/lib/api/queries";

type DialogState =
  | { kind: "form"; camera?: Camera }
  | { kind: "line"; camera: Camera }
  | { kind: "zone"; camera: Camera }
  | { kind: "detection"; camera: Camera }
  | { kind: "delete"; camera: Camera };

/** Deep links from other pages: `?new=1`, `?edit=<id>`, `?line=<id>`, `?zone=<id>`. */
function dialogFromParams(params: URLSearchParams, cameras: Camera[] | undefined): DialogState | null {
  if (params.get("new") === "1") return { kind: "form" };
  const find = (key: string) => cameras?.find((camera) => String(camera.id) === params.get(key));
  const editing = find("edit");
  if (editing) return { kind: "form", camera: editing };
  const lining = find("line");
  if (lining) return { kind: "line", camera: lining };
  const zoning = find("zone");
  if (zoning) return { kind: "zone", camera: zoning };
  return null;
}

export function CamerasSettings() {
  const cameras = useCameras();
  const settings = useSettings();
  const toggleCamera = useToggleCamera();
  const deleteCamera = useDeleteCamera();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [localDialog, setLocalDialog] = useState<DialogState | null>(null);
  const { t } = useI18n();
  const s = t.cameraList;

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
        <CardTitle>{s.title}</CardTitle>
        <CardDescription>{s.description}</CardDescription>
        <CardAction>
          <Button onClick={() => setLocalDialog({ kind: "form" })}>
            <Plus aria-hidden /> {s.add}
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
          <ErrorState title={t.errors.loadCameras} error={cameras.error} onRetry={() => void cameras.refetch()} />
        ) : cameras.data.length === 0 ? (
          <EmptyState
            icon={<Cctv aria-hidden />}
            title={s.emptyTitle}
            description={s.emptyDescription}
            action={
              <Button onClick={() => setLocalDialog({ kind: "form" })}>
                <Plus aria-hidden /> {s.add}
              </Button>
            }
          />
        ) : (
          <div className="-mx-6 overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-6">{s.camera}</TableHead>
                  <TableHead className="hidden md:table-cell">{s.source}</TableHead>
                  <TableHead className="hidden sm:table-cell">{s.status}</TableHead>
                  <TableHead className="hidden text-right lg:table-cell">{s.fps}</TableHead>
                  <TableHead className="hidden lg:table-cell">{s.line}</TableHead>
                  <TableHead className="text-center">{s.enabled}</TableHead>
                  <TableHead className="w-12 pr-6">
                    <span className="sr-only">{s.actions}</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {cameras.data.map((camera) => (
                  <TableRow key={camera.id}>
                    <TableCell className="max-w-64 pl-6">
                      <div className="flex items-center gap-2">
                        <span className="truncate font-medium">{camera.name}</span>
                        {camera.zone ? (
                          <Badge variant="secondary" className="shrink-0 font-normal">
                            {s.loadingBadge}
                          </Badge>
                        ) : null}
                        {camera.detection ? (
                          <Badge variant="outline" className="shrink-0 font-normal">
                            {s.ownDetectionBadge}
                          </Badge>
                        ) : null}
                      </div>
                      <div className="text-muted-foreground truncate font-mono text-xs" title={camera.url}>
                        {camera.url}
                      </div>
                    </TableCell>
                    <TableCell className="text-muted-foreground hidden md:table-cell">
                      {t.sourceTypes[camera.source_type]}
                    </TableCell>
                    <TableCell className="hidden sm:table-cell">
                      <StatusBadge status={camera.runtime.status} />
                    </TableCell>
                    <TableCell className="hidden text-right tabular-nums lg:table-cell">{camera.inference_fps}</TableCell>
                    <TableCell className="text-muted-foreground hidden lg:table-cell">
                      {camera.line ? s.lineSet : "-"}
                    </TableCell>
                    <TableCell className="text-center">
                      <Switch
                        checked={camera.enabled}
                        onCheckedChange={(enabled) => toggleCamera.mutate({ id: camera.id, enabled })}
                        aria-label={(camera.enabled ? s.disable : s.enable)(camera.name)}
                      />
                    </TableCell>
                    <TableCell className="pr-6">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="icon" aria-label={s.actionsFor(camera.name)}>
                            <MoreHorizontal aria-hidden />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onSelect={() => setLocalDialog({ kind: "form", camera })}>
                            <Pencil aria-hidden /> {s.edit}
                          </DropdownMenuItem>
                          <DropdownMenuItem onSelect={() => setLocalDialog({ kind: "line", camera })}>
                            <Ruler aria-hidden /> {s.countingLine}
                          </DropdownMenuItem>
                          <DropdownMenuItem onSelect={() => setLocalDialog({ kind: "zone", camera })}>
                            <SquareDashed aria-hidden /> {s.loadingZone}
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            disabled={!settings.data}
                            onSelect={() => setLocalDialog({ kind: "detection", camera })}
                          >
                            <ScanSearch aria-hidden /> {s.detection}
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem variant="destructive" onSelect={() => setLocalDialog({ kind: "delete", camera })}>
                            <Trash2 aria-hidden /> {s.delete}
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
      {dialog?.kind === "detection" && current && settings.data ? (
        <CameraDetectionDialog
          key={current.id}
          camera={current}
          settings={settings.data}
          onOpenChange={(open) => !open && close()}
        />
      ) : null}
      {dialog?.kind === "zone" && current ? (
        <ZoneEditorDialog key={current.id} camera={current} onOpenChange={(open) => !open && close()} />
      ) : null}
      <AlertDialog open={dialog?.kind === "delete"} onOpenChange={(open) => !open && close()}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{s.deleteTitle}</AlertDialogTitle>
            <AlertDialogDescription>
              {s.deleteDescription}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                if (current) deleteCamera.mutate(current);
                close();
              }}
            >
              {s.deleteConfirm}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}
