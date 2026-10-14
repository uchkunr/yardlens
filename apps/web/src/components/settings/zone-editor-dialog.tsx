"use client";

import { MousePointerClick, RefreshCw, Trash2, Undo2, X } from "lucide-react";
import { useRef, useState, type MouseEvent } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Spinner } from "@/components/ui/spinner";
import { useI18n } from "@/i18n/provider";
import { snapshotUrl, type Camera } from "@/lib/api/client";
import { useUpdateCamera } from "@/lib/api/queries";
import { normalizePoint, type Point } from "@/lib/line";

const MIN_POINTS = 3;
const MAX_POINTS = 24;

function ZoneOverlay({ points }: { points: Point[] }) {
  return (
    <svg className="pointer-events-none absolute inset-0 size-full" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden>
      {points.length >= MIN_POINTS ? (
        <polygon
          points={points.map((point) => `${point.x},${point.y}`).join(" ")}
          fill="#38bdf8"
          fillOpacity={0.25}
          stroke="#38bdf8"
          strokeWidth={3}
          vectorEffect="non-scaling-stroke"
        />
      ) : points.length === 2 ? (
        <line
          x1={points[0]?.x}
          y1={points[0]?.y}
          x2={points[1]?.x}
          y2={points[1]?.y}
          stroke="#38bdf8"
          strokeWidth={3}
          vectorEffect="non-scaling-stroke"
        />
      ) : null}
      {points.map((point, index) => (
        <circle
          key={index}
          cx={point.x}
          cy={point.y}
          r={0.009}
          fill="#ffffff"
          stroke="#0369a1"
          strokeWidth={2}
          vectorEffect="non-scaling-stroke"
        />
      ))}
    </svg>
  );
}

/**
 * Click the corners of the spot where vehicles park to be loaded.
 * Render with `key={camera.id}` so the editor state starts fresh for each camera.
 */
export function ZoneEditorDialog({ camera, onOpenChange }: { camera: Camera; onOpenChange: (open: boolean) => void }) {
  const { t } = useI18n();
  const m = t.zoneEditor;
  const [points, setPoints] = useState<Point[]>(() => camera.zone?.points ?? []);
  const [nonce, setNonce] = useState(0);
  const [imageState, setImageState] = useState<"loading" | "ready" | "error">("loading");
  const frame = useRef<HTMLDivElement>(null);
  const updateCamera = useUpdateCamera();
  const { frame_width: width, frame_height: height } = camera.runtime;
  const online = camera.runtime.status === "online";

  const addPoint = (event: MouseEvent<HTMLDivElement>) => {
    const rect = frame.current?.getBoundingClientRect();
    if (!rect) return;
    const point = normalizePoint(event.clientX, event.clientY, rect);
    setPoints((current) => (current.length >= MAX_POINTS ? current : [...current, point]));
  };

  const save = (zone: { points: Point[] } | null) =>
    updateCamera.mutate({ id: camera.id, body: { zone } }, { onSuccess: () => onOpenChange(false) });

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{m.title(camera.name)}</DialogTitle>
          <DialogDescription>{m.description}</DialogDescription>
        </DialogHeader>

        {online ? (
          <div
            ref={frame}
            className="bg-muted relative aspect-video w-full cursor-crosshair overflow-hidden rounded-lg border select-none"
            style={width && height ? { aspectRatio: `${width} / ${height}` } : undefined}
            onClick={addPoint}
            role="application"
            aria-label={m.frameLabel}
          >
            <img
              src={snapshotUrl(camera.id, { raw: true, nonce })}
              alt={t.lineEditor.frameAlt(camera.name)}
              className="absolute inset-0 size-full object-fill"
              onLoad={() => setImageState("ready")}
              onError={() => setImageState("error")}
              draggable={false}
            />
            {imageState === "loading" ? (
              <div className="absolute inset-0 flex items-center justify-center">
                <Spinner className="size-6" />
              </div>
            ) : null}
            <ZoneOverlay points={points} />
            <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center">
              <span className="flex items-center gap-1.5 rounded-md bg-black/70 px-2.5 py-1 text-xs text-white">
                <MousePointerClick className="size-3.5" aria-hidden />
                {points.length < MIN_POINTS ? m.needMore : m.canSave}
              </span>
            </div>
          </div>
        ) : (
          <Alert>
            <AlertTitle>{t.lineEditor.notStreamingTitle}</AlertTitle>
            <AlertDescription>{t.lineEditor.notStreamingDescription}</AlertDescription>
          </Alert>
        )}

        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setImageState("loading");
              setNonce((value) => value + 1);
            }}
            disabled={!online}
          >
            <RefreshCw aria-hidden /> {t.lineEditor.newFrame}
          </Button>
          <Button variant="outline" size="sm" onClick={() => setPoints((current) => current.slice(0, -1))} disabled={points.length === 0}>
            <Undo2 aria-hidden /> {m.undo}
          </Button>
          <Button variant="outline" size="sm" onClick={() => setPoints([])} disabled={points.length === 0}>
            <X aria-hidden /> {m.clear}
          </Button>
        </div>

        <DialogFooter className="gap-2 sm:justify-between">
          {camera.zone ? (
            <Button variant="ghost" className="text-destructive" onClick={() => save(null)} disabled={updateCamera.isPending}>
              <Trash2 aria-hidden /> {m.remove}
            </Button>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {t.common.cancel}
            </Button>
            <Button onClick={() => save({ points })} disabled={points.length < MIN_POINTS || updateCamera.isPending}>
              {updateCamera.isPending ? <Spinner /> : null}
              {m.save}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
