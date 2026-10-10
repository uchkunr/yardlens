"use client";

import { ArrowLeftRight, MousePointerClick, RefreshCw, Trash2, Undo2 } from "lucide-react";
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
import { snapshotUrl, type Camera } from "@/lib/api/client";
import { useUpdateCamera } from "@/lib/api/queries";
import { inDirection, lineToPoints, normalizePoint, pointsToLine, type Point } from "@/lib/line";

function LineOverlay({ points }: { points: Point[] }) {
  const [start, end] = points;
  const line = start && end ? pointsToLine(start, end) : null;
  const normal = line ? inDirection(line) : null;
  const mid = line ? { x: (line.x1 + line.x2) / 2, y: (line.y1 + line.y2) / 2 } : null;
  const arrowLength = 0.08;

  return (
    <svg
      className="pointer-events-none absolute inset-0 size-full"
      viewBox="0 0 1 1"
      preserveAspectRatio="none"
      aria-hidden
    >
      <defs>
        <marker id="in-arrow" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="5" markerHeight="5" orient="auto">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#22c55e" />
        </marker>
      </defs>
      {start && end ? (
        <line
          x1={start.x}
          y1={start.y}
          x2={end.x}
          y2={end.y}
          stroke="#facc15"
          strokeWidth={3}
          vectorEffect="non-scaling-stroke"
        />
      ) : null}
      {mid && normal ? (
        <line
          x1={mid.x - normal.x * arrowLength}
          y1={mid.y - normal.y * arrowLength}
          x2={mid.x + normal.x * arrowLength}
          y2={mid.y + normal.y * arrowLength}
          stroke="#22c55e"
          strokeWidth={2.5}
          vectorEffect="non-scaling-stroke"
          markerEnd="url(#in-arrow)"
        />
      ) : null}
      {points.map((point, index) => (
        <circle
          key={index}
          cx={point.x}
          cy={point.y}
          r={0.009}
          fill={index === 0 ? "#facc15" : "#ffffff"}
          stroke="#000"
          strokeWidth={1.5}
          vectorEffect="non-scaling-stroke"
        />
      ))}
    </svg>
  );
}

/**
 * Click two points on a raw snapshot to define the counting line.
 * Render with `key={camera.id}` so the editor state starts fresh for each camera.
 */
export function LineEditorDialog({
  camera,
  onOpenChange,
}: {
  camera: Camera;
  onOpenChange: (open: boolean) => void;
}) {
  const [points, setPoints] = useState<Point[]>(() => (camera.line ? lineToPoints(camera.line) : []));
  const [nonce, setNonce] = useState(0);
  const [imageState, setImageState] = useState<"loading" | "ready" | "error">("loading");
  const frame = useRef<HTMLDivElement>(null);
  const updateCamera = useUpdateCamera();
  const { frame_width: width, frame_height: height } = camera.runtime;

  const online = camera.runtime.status === "online";
  const line = points.length === 2 && points[0] && points[1] ? pointsToLine(points[0], points[1]) : null;

  const addPoint = (event: MouseEvent<HTMLDivElement>) => {
    const rect = frame.current?.getBoundingClientRect();
    if (!rect) return;
    const point = normalizePoint(event.clientX, event.clientY, rect);
    setPoints((current) => (current.length >= 2 ? [point] : [...current, point]));
  };

  const save = (value: typeof line) =>
    updateCamera.mutate({ id: camera.id, body: { line: value } }, { onSuccess: () => onOpenChange(false) });

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Counting line · {camera.name}</DialogTitle>
          <DialogDescription>
            Click two points on the frame. Objects crossing in the direction of the green arrow count as
            &ldquo;in&rdquo;, the opposite direction as &ldquo;out&rdquo;.
          </DialogDescription>
        </DialogHeader>

        {online ? (
          <div
            ref={frame}
            className="bg-muted relative aspect-video w-full cursor-crosshair overflow-hidden rounded-lg border select-none"
            style={width && height ? { aspectRatio: `${width} / ${height}` } : undefined}
            onClick={addPoint}
            role="application"
            aria-label="Snapshot. Click to place counting line points."
          >
            <img
              src={snapshotUrl(camera.id, { raw: true, nonce })}
              alt={`Current frame from ${camera.name}`}
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
            <LineOverlay points={points} />
            {points.length < 2 ? (
              <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center">
                <span className="flex items-center gap-1.5 rounded-md bg-black/70 px-2.5 py-1 text-xs text-white">
                  <MousePointerClick className="size-3.5" aria-hidden />
                  {points.length === 0 ? "Click the start point" : "Click the end point"}
                </span>
              </div>
            ) : null}
          </div>
        ) : (
          <Alert>
            <AlertTitle>Camera is not streaming</AlertTitle>
            <AlertDescription>
              A live frame is needed to place the line. Enable the camera and wait until it is online.
            </AlertDescription>
          </Alert>
        )}

        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => {
              setImageState("loading");
              setNonce((value) => value + 1);
            }}
            disabled={!online}
          >
            <RefreshCw aria-hidden /> New frame
          </Button>
          <Button variant="outline" size="sm" onClick={() => setPoints([])} disabled={points.length === 0}>
            <Undo2 aria-hidden /> Clear points
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPoints((current) => [...current].reverse())}
            disabled={!line}
          >
            <ArrowLeftRight aria-hidden /> Flip direction
          </Button>
        </div>

        <DialogFooter className="gap-2 sm:justify-between">
          {camera.line ? (
            <Button variant="ghost" className="text-destructive" onClick={() => save(null)} disabled={updateCamera.isPending}>
              <Trash2 aria-hidden /> Remove line
            </Button>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button onClick={() => save(line)} disabled={!line || updateCamera.isPending}>
              {updateCamera.isPending ? <Spinner /> : null}
              Save line
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
