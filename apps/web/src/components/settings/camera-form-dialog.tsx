"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { CircleCheck, CircleAlert, PlugZap } from "lucide-react";
import { useEffect, useMemo } from "react";
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
import type { Messages } from "@/i18n/messages";
import { useI18n } from "@/i18n/provider";
import type { Camera } from "@/lib/api/client";
import { useCreateCamera, useTestCamera, useUpdateCamera } from "@/lib/api/queries";
import { SOURCE_TYPES, SOURCE_URL_PLACEHOLDERS } from "@/lib/format";

function cameraSchema(t: Messages) {
  const m = t.cameraForm;
  return z.object({
    name: z.string().trim().min(1, m.nameRequired).max(120, m.nameTooLong),
    source_type: z.enum(["rtsp", "http", "hls", "youtube", "file"]),
    url: z.string().trim().min(1, m.urlRequired).max(2048),
    inference_fps: z.number({ error: t.common.enterNumber }).min(0.2, m.fpsMin).max(15, m.fpsMax),
    plate_url: z
      .string()
      .trim()
      .max(2048)
      .refine((value) => value === "" || /^rtsps?:\/\/./i.test(value), m.plateUrlInvalid),
    enabled: z.boolean(),
  });
}

type CameraFormValues = z.infer<ReturnType<typeof cameraSchema>>;

const DEFAULTS: CameraFormValues = {
  name: "",
  source_type: "rtsp",
  url: "",
  inference_fps: 3,
  plate_url: "",
  enabled: true,
};

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
  const { t, f } = useI18n();
  const m = t.cameraForm;
  const schema = useMemo(() => cameraSchema(t), [t]);

  const form = useForm<CameraFormValues>({ resolver: zodResolver(schema), defaultValues: DEFAULTS });
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
            plate_url: camera.plate_url ?? "",
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
    // Only RTSP cameras have a separate main stream.
    const body = { ...values, plate_url: values.source_type === "rtsp" && values.plate_url ? values.plate_url : null };
    if (camera) updateCamera.mutate({ id: camera.id, body }, done);
    else createCamera.mutate(body, done);
  });

  const saving = createCamera.isPending || updateCamera.isPending;
  const result = testCamera.data;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{camera ? m.editTitle : m.addTitle}</DialogTitle>
          <DialogDescription>{camera ? m.editDescription : m.addDescription}</DialogDescription>
        </DialogHeader>
        <form id="camera-form" onSubmit={onSubmit} noValidate>
          <FieldGroup>
            <Field data-invalid={Boolean(form.formState.errors.name)}>
              <FieldLabel htmlFor="camera-name">{m.name}</FieldLabel>
              <Input
                id="camera-name"
                placeholder={m.namePlaceholder}
                autoComplete="off"
                aria-invalid={Boolean(form.formState.errors.name)}
                {...form.register("name")}
              />
              <FieldError errors={[form.formState.errors.name]} />
            </Field>

            <Field>
              <FieldLabel htmlFor="camera-source-type">{m.sourceType}</FieldLabel>
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
                          {t.sourceTypes[type]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
              <FieldDescription>{m.hints[sourceType]}</FieldDescription>
            </Field>

            <Field data-invalid={Boolean(form.formState.errors.url)}>
              <FieldLabel htmlFor="camera-url">{sourceType === "file" ? m.filePath : m.url}</FieldLabel>
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

            {sourceType === "rtsp" ? (
              <Field data-invalid={Boolean(form.formState.errors.plate_url)}>
                <FieldLabel htmlFor="camera-plate-url">{m.plateUrl}</FieldLabel>
                <Input
                  id="camera-plate-url"
                  placeholder={m.plateUrlPlaceholder}
                  autoComplete="off"
                  spellCheck={false}
                  className="font-mono text-xs"
                  aria-invalid={Boolean(form.formState.errors.plate_url)}
                  {...form.register("plate_url")}
                />
                <FieldDescription>{m.plateUrlHint}</FieldDescription>
                <FieldError errors={[form.formState.errors.plate_url]} />
              </Field>
            ) : null}

            <div className="grid gap-5 sm:grid-cols-2">
              <Field data-invalid={Boolean(form.formState.errors.inference_fps)}>
                <FieldLabel htmlFor="camera-fps">{m.fps}</FieldLabel>
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
                <FieldDescription>{m.fpsHint}</FieldDescription>
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
                <FieldLabel htmlFor="camera-enabled">{m.enabled}</FieldLabel>
              </Field>
            </div>

            <div className="space-y-3">
              <Button type="button" variant="outline" onClick={runTest} disabled={testCamera.isPending}>
                {testCamera.isPending ? <Spinner /> : <PlugZap aria-hidden />}
                {testCamera.isPending ? m.testing : m.test}
              </Button>
              {result ? (
                result.success ? (
                  <div className="space-y-2">
                    <Alert>
                      <CircleCheck className="text-success" aria-hidden />
                      <AlertTitle>{f.server(result.message)}</AlertTitle>
                      <AlertDescription>
                        {m.testDetails(String(result.width), String(result.height), f.decimal(result.elapsed_ms / 1000, 1))}
                      </AlertDescription>
                    </Alert>
                    {result.preview ? (
                      <img
                        src={result.preview}
                        alt={m.previewAlt}
                        className="aspect-video w-full rounded-lg border object-contain"
                      />
                    ) : null}
                  </div>
                ) : (
                  <Alert variant="destructive">
                    <CircleAlert aria-hidden />
                    <AlertTitle>{m.testFailed}</AlertTitle>
                    <AlertDescription>{f.server(result.message)}</AlertDescription>
                  </Alert>
                )
              ) : null}
            </div>
          </FieldGroup>
        </form>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            {t.common.cancel}
          </Button>
          <Button type="submit" form="camera-form" disabled={saving}>
            {saving ? <Spinner /> : null}
            {camera ? t.common.saveChanges : m.submitAdd}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
