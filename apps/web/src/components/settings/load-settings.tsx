"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Cpu, HardDrive, MemoryStick, MonitorCog, ScanSearch } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { Area, AreaChart, CartesianGrid, ReferenceLine, XAxis, YAxis } from "recharts";
import { z } from "zod";

import { ErrorState } from "@/components/shared/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { Field, FieldContent, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { Messages } from "@/i18n/messages";
import { useI18n } from "@/i18n/provider";
import type { Activity, AppSettings, LoadLevel, SystemResources } from "@/lib/api/client";
import { queryKeys, useSystemResources, useUpdateSettings } from "@/lib/api/queries";
import { cn } from "@/lib/utils";

const LEVEL_TONE: Record<LoadLevel, string> = {
  normal: "border-success/30 bg-success/10 text-success",
  busy: "border-warning/40 bg-warning/10 text-warning",
  high: "border-warning/40 bg-warning/15 text-warning",
  overload: "border-destructive/30 bg-destructive/10 text-destructive",
};

const ACTIVITY_DOT: Record<Activity, string> = {
  active: "bg-success",
  standby: "bg-warning",
  idle: "bg-muted-foreground/50",
};

function deviceLabel(device: string, m: Messages["load"], gpuName: string | null | undefined) {
  if (device.startsWith("cuda")) return gpuName ?? m.devices.gpu;
  if (device === "mps") return m.devices.mps;
  if (device === "intel:gpu") return m.devices.igpu;
  return m.devices.cpu;
}

function Meter({
  label,
  icon,
  value,
  percent,
  limit,
  children,
}: {
  label: string;
  icon: ReactNode;
  value: string;
  percent: number;
  limit?: number;
  children: ReactNode;
}) {
  const tone = percent >= 90 ? "bg-destructive" : limit !== undefined && percent > limit ? "bg-warning" : "bg-primary";
  return (
    <div className="space-y-3 rounded-xl border p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="text-muted-foreground flex items-center gap-2 text-sm [&_svg]:size-4">
          {icon}
          {label}
        </span>
        <span className="text-xl font-semibold tabular-nums">{value}</span>
      </div>
      <div
        className="bg-muted relative h-2 overflow-hidden rounded-full"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(percent)}
      >
        <div
          className={cn("h-full rounded-full transition-[width] duration-500", tone)}
          style={{ width: `${Math.min(100, Math.max(0, percent))}%` }}
        />
        {limit !== undefined ? (
          <div className="bg-foreground/40 absolute inset-y-0 w-px" style={{ left: `${limit}%` }} aria-hidden />
        ) : null}
      </div>
      <div className="text-muted-foreground space-y-0.5 text-xs">{children}</div>
    </div>
  );
}

function LoadHistory({ data }: { data: SystemResources }) {
  const { t, f } = useI18n();
  const m = t.load;
  const config = {
    cpu_percent: { label: m.total, color: "var(--chart-2)" },
    process_cpu_percent: { label: "YardLens", color: "var(--chart-1)" },
  } satisfies ChartConfig;
  const points = data.history.map((point) => ({ ...point, label: f.time(point.at) }));
  return (
    <div className="space-y-2">
      <div className="text-muted-foreground text-xs">{m.history}</div>
      <ChartContainer config={config} className="aspect-auto h-36 w-full">
        <AreaChart data={points} margin={{ left: 0, right: 4, top: 4 }}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="label" hide />
          <YAxis domain={[0, 100]} ticks={[0, 50, 100]} tickLine={false} axisLine={false} width={44} unit="%" />
          <ReferenceLine y={data.load.cpu_limit_percent} stroke="var(--muted-foreground)" strokeDasharray="4 4" />
          <ChartTooltip content={<ChartTooltipContent indicator="line" />} />
          <Area
            dataKey="cpu_percent"
            type="monotone"
            stroke="var(--color-cpu_percent)"
            fill="var(--color-cpu_percent)"
            fillOpacity={0.15}
            isAnimationActive={false}
          />
          <Area
            dataKey="process_cpu_percent"
            type="monotone"
            stroke="var(--color-process_cpu_percent)"
            fill="var(--color-process_cpu_percent)"
            fillOpacity={0.3}
            isAnimationActive={false}
          />
        </AreaChart>
      </ChartContainer>
    </div>
  );
}

function LoadMonitor({ data }: { data: SystemResources }) {
  const { t, f } = useI18n();
  const m = t.load;
  const { load, cpu, memory, disk, gpu, inference } = data;
  const fps = t.camera.fps(f.decimal(load.stream_fps, 0));
  const share = f.percent(load.fps_scale);
  const hint =
    load.level === "normal"
      ? m.levelHints.normal
      : load.level === "busy"
        ? m.levelHints.busy(fps)
        : m.levelHints[load.level](fps, share);
  const backend = inference.backend === "openvino" ? "OpenVINO" : "PyTorch";

  return (
    <Card>
      <CardHeader>
        <CardTitle>{m.title}</CardTitle>
        <CardDescription>{m.description}</CardDescription>
        <CardAction>
          <Badge variant="outline" className={cn("gap-1.5 font-normal", LEVEL_TONE[load.level])}>
            <span className="size-1.5 rounded-full bg-current" aria-hidden />
            {m.levels[load.level]}
          </Badge>
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-5">
        {load.level === "normal" ? (
          <p className="text-muted-foreground text-sm">{hint}</p>
        ) : (
          <Alert variant={load.level === "overload" ? "destructive" : "default"}>
            <AlertTriangle aria-hidden />
            <AlertTitle>{m.levels[load.level]}</AlertTitle>
            <AlertDescription>{hint}</AlertDescription>
          </Alert>
        )}

        {disk?.images_paused ? (
          <Alert variant="destructive">
            <HardDrive aria-hidden />
            <AlertTitle>{m.disk}</AlertTitle>
            <AlertDescription>{m.imagesPaused}</AlertDescription>
          </Alert>
        ) : null}

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <Meter
            label={m.cpu}
            icon={<Cpu aria-hidden />}
            value={f.percent(cpu.percent / 100)}
            percent={cpu.percent}
            limit={load.cpu_limit_percent}
          >
            <div>{m.yardlensShare(f.percent(cpu.process_percent / 100))}</div>
            <div>{m.cores(f.count(cpu.physical_cores), f.count(cpu.logical_cpus))}</div>
          </Meter>
          <Meter label={m.memory} icon={<MemoryStick aria-hidden />} value={f.percent(memory.percent / 100)} percent={memory.percent}>
            <div>{m.memoryUsed(f.bytes(memory.used_bytes), f.bytes(memory.total_bytes))}</div>
            <div>{m.yardlensShare(f.bytes(memory.process_bytes))}</div>
          </Meter>
          {disk ? (
            <Meter label={m.disk} icon={<HardDrive aria-hidden />} value={f.percent(disk.percent / 100)} percent={disk.percent}>
              <div>{m.diskFree(f.bytes(disk.free_bytes), f.bytes(disk.total_bytes))}</div>
            </Meter>
          ) : null}
          {gpu ? (
            <Meter
              label={m.gpu}
              icon={<MonitorCog aria-hidden />}
              value={gpu.percent !== null ? f.percent(gpu.percent / 100) : "-"}
              percent={gpu.percent ?? 0}
            >
              <div className="truncate">{gpu.name}</div>
              {gpu.memory_used_bytes !== null && gpu.memory_total_bytes !== null ? (
                <div>{m.videoMemory(f.bytes(gpu.memory_used_bytes), f.bytes(gpu.memory_total_bytes))}</div>
              ) : null}
            </Meter>
          ) : null}
          <Meter
            label={m.inference}
            icon={<ScanSearch aria-hidden />}
            value={f.percent(inference.busy_percent / 100)}
            percent={inference.busy_percent}
          >
            <div className="truncate">
              {m.inferenceOn(backend, deviceLabel(inference.device, m, gpu?.name))}
              {inference.threads ? `, ${m.threads(f.count(inference.threads))}` : null}
            </div>
            <div>{m.busy(f.percent(inference.busy_percent / 100))}</div>
          </Meter>
        </div>

        {inference.optimizing ? (
          <p className="text-muted-foreground flex items-center gap-2 text-sm">
            <Spinner /> {m.optimizing}
          </p>
        ) : null}

        <LoadHistory data={data} />
      </CardContent>
    </Card>
  );
}

function CameraLoad({ data }: { data: SystemResources }) {
  const { t, f } = useI18n();
  const m = t.load;
  const distributed = data.cameras.some((camera) => camera.worker_id !== null && camera.worker_id !== undefined);
  return (
    <Card className="gap-0 overflow-hidden pb-0">
      <CardHeader className="pb-4">
        <CardTitle>{m.camerasTitle}</CardTitle>
        <CardDescription>{m.camerasDescription}</CardDescription>
      </CardHeader>
      {data.cameras.length === 0 ? (
        <CardContent className="text-muted-foreground pb-6 text-sm">{m.noCameras}</CardContent>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="pl-6">{m.camera}</TableHead>
              {distributed ? <TableHead className="hidden md:table-cell">{t.workers.node}</TableHead> : null}
              <TableHead>{m.mode}</TableHead>
              <TableHead className="text-right">{m.rate}</TableHead>
              <TableHead className="hidden pr-6 sm:table-cell">{m.decoding}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.cameras.map((camera) => (
              <TableRow key={camera.id}>
                <TableCell className="max-w-48 truncate pl-6 font-medium">{camera.name}</TableCell>
                {distributed ? (
                  <TableCell className="text-muted-foreground hidden max-w-40 truncate md:table-cell">
                    {camera.worker_name ?? t.workers.thisServer}
                  </TableCell>
                ) : null}
                <TableCell>
                  <span className="flex items-center gap-2">
                    <span className={cn("size-2 shrink-0 rounded-full", ACTIVITY_DOT[camera.activity])} aria-hidden />
                    {t.camera.modes[camera.activity]}
                  </span>
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {m.rateValue(f.decimal(camera.target_fps, 1), f.decimal(camera.max_fps, 0))}
                </TableCell>
                <TableCell className="text-muted-foreground hidden pr-6 sm:table-cell">
                  {camera.hardware_decoding ? m.decodeGpu : m.decodeCpu}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Card>
  );
}

function performanceSchema(t: Messages) {
  return z.object({
    cpu_limit_percent: z.number().int().min(30).max(95),
    adaptive_fps: z.boolean(),
    idle_fps: z.number({ error: t.load.idleFpsRange }).min(0.2, t.load.idleFpsRange).max(5, t.load.idleFpsRange),
  });
}

type PerformanceValues = z.infer<ReturnType<typeof performanceSchema>>;

function PerformanceForm({ settings }: { settings: AppSettings }) {
  const { t, f } = useI18n();
  const m = t.load;
  const queryClient = useQueryClient();
  const update = useUpdateSettings(m.saved);
  const form = useForm<PerformanceValues>({
    resolver: zodResolver(performanceSchema(t)),
    defaultValues: settings.performance,
  });
  const { reset } = form;
  useEffect(() => reset(settings.performance), [settings.performance, reset]);
  const adaptive = useWatch({ control: form.control, name: "adaptive_fps" });

  const onSubmit = form.handleSubmit((values) =>
    update.mutate(
      { performance: values },
      { onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.resources }) },
    ),
  );

  return (
    <Card>
      <form onSubmit={onSubmit} noValidate>
        <CardHeader>
          <CardTitle>{m.settingsTitle}</CardTitle>
          <CardDescription>{m.settingsDescription}</CardDescription>
        </CardHeader>
        <CardContent className="py-6">
          <FieldGroup>
            <Controller
              control={form.control}
              name="cpu_limit_percent"
              render={({ field }) => (
                <Field>
                  <div className="flex items-center justify-between sm:w-72">
                    <FieldLabel htmlFor="load-limit">{m.cpuLimit}</FieldLabel>
                    <span className="text-sm font-medium tabular-nums">{f.percent(field.value / 100)}</span>
                  </div>
                  <Slider
                    id="load-limit"
                    min={30}
                    max={95}
                    step={5}
                    value={[field.value]}
                    onValueChange={([next]) => field.onChange(next ?? field.value)}
                    className="sm:w-72"
                    aria-label={m.cpuLimit}
                  />
                  <FieldDescription>{m.cpuLimitHint}</FieldDescription>
                </Field>
              )}
            />

            <Field orientation="horizontal">
              <Controller
                control={form.control}
                name="adaptive_fps"
                render={({ field }) => (
                  <Switch id="load-adaptive" checked={field.value} onCheckedChange={field.onChange} />
                )}
              />
              <FieldContent>
                <FieldLabel htmlFor="load-adaptive">{m.adaptive}</FieldLabel>
                <FieldDescription>{m.adaptiveHint}</FieldDescription>
              </FieldContent>
            </Field>

            <Controller
              control={form.control}
              name="idle_fps"
              render={({ field, fieldState }) => (
                <Field data-invalid={fieldState.invalid} data-disabled={!adaptive}>
                  <FieldLabel htmlFor="load-idle-fps">{m.idleFps}</FieldLabel>
                  <Input
                    id="load-idle-fps"
                    type="number"
                    inputMode="decimal"
                    min={0.2}
                    max={5}
                    step={0.1}
                    disabled={!adaptive}
                    className="sm:w-32"
                    value={Number.isFinite(field.value) ? field.value : ""}
                    onChange={(event) => field.onChange(event.target.valueAsNumber)}
                    onBlur={field.onBlur}
                    aria-invalid={fieldState.invalid}
                  />
                  <FieldDescription>{m.idleFpsHint}</FieldDescription>
                  <FieldError errors={[fieldState.error]} />
                </Field>
              )}
            />
          </FieldGroup>
        </CardContent>
        <CardFooter className="justify-end gap-2 border-t">
          <Button
            type="button"
            variant="ghost"
            onClick={() => reset(settings.performance)}
            disabled={!form.formState.isDirty}
          >
            {t.common.discard}
          </Button>
          <Button type="submit" disabled={update.isPending || !form.formState.isDirty}>
            {update.isPending ? <Spinner /> : null}
            {t.common.saveChanges}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}

function LoadSkeleton() {
  return (
    <Card>
      <CardHeader className="gap-2">
        <Skeleton className="h-5 w-48" />
        <Skeleton className="h-4 w-64" />
      </CardHeader>
      <CardContent className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, index) => (
          <Skeleton key={index} className="h-28 rounded-xl" />
        ))}
      </CardContent>
    </Card>
  );
}

/** Settings → Load: what the machine is doing, how each camera is paced, and the limits. */
export function LoadSettings({ settings }: { settings: AppSettings }) {
  const resources = useSystemResources();
  const { t } = useI18n();
  return (
    <div className="space-y-6">
      {resources.isPending ? (
        <LoadSkeleton />
      ) : resources.isError ? (
        <ErrorState title={t.system.unreachable} error={resources.error} onRetry={() => void resources.refetch()} />
      ) : (
        <>
          <LoadMonitor data={resources.data} />
          <CameraLoad data={resources.data} />
        </>
      )}
      <PerformanceForm settings={settings} />
    </div>
  );
}

