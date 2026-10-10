"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { CircleCheck, CircleAlert, PlugZap } from "lucide-react";
import { useEffect } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { z } from "zod";

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
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import type { Camera, SourceType } from "@/lib/api/client";
import { useCreateCamera, useTestCamera, useUpdateCamera } from "@/lib/api/queries";
import { SOURCE_TYPE_LABELS, SOURCE_TYPES, SOURCE_URL_PLACEHOLDERS } from "@/lib/format";

const SOURCE_HINTS: Record<SourceType, string> = {
  rtsp: "IP cameras and NVRs. Include credentials in the URL if required.",
  http: "An MJPEG stream (multipart) or a JPEG snapshot URL that is polled.",
  hls: "An HLS playlist, e.g. a city or DOT traffic camera.",
  youtube: "A YouTube live stream; resolved to HLS automatically via yt-dlp.",
  file: "Path to a video on the API server, relative to its data directory. Plays in a loop.",
};

const cameraSchema = z.object({
  name: z.string().trim().min(1, "Name is required").max(120, "Keep it under 120 characters"),
  source_type: z.enum(["rtsp", "http", "hls", "youtube", "file"]),
  url: z.string().trim().min(1, "Source URL is required").max(2048),
  inference_fps: z
    .number({ error: "Enter a number" })
    .min(0.2, "At least 0.2 fps")
    .max(15, "At most 15 fps"),
  enabled: z.boolean(),
});

type CameraFormValues = z.infer<typeof cameraSchema>;

const DEFAULTS: CameraFormValues = { name: "", source_type: "rtsp", url: "", inference_fps: 3, enabled: true };

export function CameraFormDialog({
  open,
  onOpenChange,
  camera,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Edit this camera; create a new one when omitted. */
  camera?: Camera;
}) {
  const createCamera = useCreateCamera();
  const updateCamera = useUpdateCamera();
  const testCamera = useTestCamera();

  const form = useForm<CameraFormValues>({ resolver: zodResolver(cameraSchema), defaultValues: DEFAULTS });
  const sourceType = useWatch({ control: form.control, name: "source_type" });
  const { reset: resetTest } = testCamera;

  useEffect(() => {
    if (!open) return;
    form.reset(
      camera
        ? {
            name: camera.name,
            source_type: camera.source_type,
            url: camera.url,
            inference_fps: camera.inference_fps,
            enabled: camera.enabled,
          }
        : DEFAULTS,
    );
    resetTest();
  }, [open, camera, form, resetTest]);

  const runTest = async () => {
    const valid = await form.trigger(["source_type", "url"]);
    if (!valid) return;
    const { source_type, url } = form.getValues();
    testCamera.mutate({ source_type, url });
  };

  const onSubmit = form.handleSubmit((values) => {
    const done = { onSuccess: () => onOpenChange(false) };
    if (camera) updateCamera.mutate({ id: camera.id, body: values }, done);
    else createCamera.mutate(values, done);
  });

  const saving = createCamera.isPending || updateCamera.isPending;
  const result = testCamera.data;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{camera ? "Edit camera" : "Add camera"}</DialogTitle>
          <DialogDescription>
            {camera ? "Changing the source reconnects the camera." : "Connect a stream; detection starts right away."}
          </DialogDescription>
        </DialogHeader>
        <form id="camera-form" onSubmit={onSubmit} noValidate>
          <FieldGroup>
            <Field data-invalid={Boolean(form.formState.errors.name)}>
              <FieldLabel htmlFor="camera-name">Name</FieldLabel>
              <Input
                id="camera-name"
                placeholder="North gate"
                autoComplete="off"
                aria-invalid={Boolean(form.formState.errors.name)}
                {...form.register("name")}
              />
              <FieldError errors={[form.formState.errors.name]} />
            </Field>

            <Field>
              <FieldLabel htmlFor="camera-source-type">Source type</FieldLabel>
              <Controller
                control={form.control}
                name="source_type"
                render={({ field }) => (
                  <Select
                    value={field.value}
                    onValueChange={(value) => {
                      field.onChange(value);
                      resetTest();
                    }}
                  >
                    <SelectTrigger id="camera-source-type" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {SOURCE_TYPES.map((type) => (
                        <SelectItem key={type} value={type}>
                          {SOURCE_TYPE_LABELS[type]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
              <FieldDescription>{SOURCE_HINTS[sourceType]}</FieldDescription>
            </Field>

            <Field data-invalid={Boolean(form.formState.errors.url)}>
              <FieldLabel htmlFor="camera-url">{sourceType === "file" ? "File path" : "URL"}</FieldLabel>
              <Input
                id="camera-url"
                placeholder={SOURCE_URL_PLACEHOLDERS[sourceType]}
                autoComplete="off"
                spellCheck={false}
                className="font-mono text-xs"
                aria-invalid={Boolean(form.formState.errors.url)}
                {...form.register("url", { onChange: () => resetTest() })}
              />
              <FieldError errors={[form.formState.errors.url]} />
            </Field>

            <div className="grid gap-5 sm:grid-cols-2">
              <Field data-invalid={Boolean(form.formState.errors.inference_fps)}>
                <FieldLabel htmlFor="camera-fps">Inference FPS</FieldLabel>
                <Input
                  id="camera-fps"
                  type="number"
                  inputMode="decimal"
                  step={0.5}
                  min={0.2}
                  max={15}
                  aria-invalid={Boolean(form.formState.errors.inference_fps)}
                  {...form.register("inference_fps", { valueAsNumber: true })}
                />
                <FieldError errors={[form.formState.errors.inference_fps]} />
              </Field>
              <Field orientation="horizontal" className="self-end pb-2">
                <Controller
                  control={form.control}
                  name="enabled"
                  render={({ field }) => (
                    <Switch id="camera-enabled" checked={field.value} onCheckedChange={field.onChange} />
                  )}
                />
                <FieldLabel htmlFor="camera-enabled">Enabled</FieldLabel>
              </Field>
            </div>

            <div className="space-y-3">
              <Button type="button" variant="outline" onClick={runTest} disabled={testCamera.isPending}>
                {testCamera.isPending ? <Spinner /> : <PlugZap aria-hidden />}
                {testCamera.isPending ? "Testing connection…" : "Test connection"}
              </Button>
              {result ? (
                result.success ? (
                  <div className="space-y-2">
                    <Alert>
                      <CircleCheck className="text-success" aria-hidden />
                      <AlertTitle>{result.message}</AlertTitle>
                      <AlertDescription>
                        {result.width}×{result.height} · first frame in {(result.elapsed_ms / 1000).toFixed(1)}s
                      </AlertDescription>
                    </Alert>
                    {result.preview ? (
                      <img
                        src={result.preview}
                        alt="Preview frame from the camera"
                        className="aspect-video w-full rounded-lg border object-contain"
                      />
                    ) : null}
                  </div>
                ) : (
                  <Alert variant="destructive">
                    <CircleAlert aria-hidden />
                    <AlertTitle>Connection failed</AlertTitle>
                    <AlertDescription>{result.message}</AlertDescription>
                  </Alert>
                )
              ) : null}
            </div>
          </FieldGroup>
        </form>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" form="camera-form" disabled={saving}>
            {saving ? <Spinner /> : null}
            {camera ? "Save changes" : "Add camera"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
