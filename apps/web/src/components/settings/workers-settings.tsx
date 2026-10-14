"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import {
  AlertTriangle,
  CircleCheck,
  Clock,
  Copy,
  MoreHorizontal,
  Pencil,
  Plus,
  Server,
  Shuffle,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { EmptyState, ErrorState } from "@/components/shared/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Field, FieldContent, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { Messages } from "@/i18n/messages";
import { useI18n } from "@/i18n/provider";
import type { Cluster, LoadLevel, Worker, WorkerCreated } from "@/lib/api/client";
import {
  useCameras,
  useCreateWorker,
  useDeleteWorker,
  useLocalProcessing,
  useRebalance,
  useRenameWorker,
  useWorkers,
} from "@/lib/api/queries";
import { cn } from "@/lib/utils";

// Clocks further apart than this get a warning; the server corrects event times either way.
const CLOCK_WARNING_SECONDS = 2;

const LEVEL_TONE: Record<LoadLevel, string> = {
  normal: "border-success/30 bg-success/10 text-success",
  busy: "border-warning/40 bg-warning/10 text-warning",
  high: "border-warning/40 bg-warning/15 text-warning",
  overload: "border-destructive/30 bg-destructive/10 text-destructive",
};

function deviceLabel(device: string, m: Messages["load"], gpu: string | null | undefined) {
  if (device.startsWith("cuda")) return gpu ?? m.devices.gpu;
  if (device === "mps") return m.devices.mps;
  if (device === "intel:gpu") return m.devices.igpu;
  return m.devices.cpu;
}

function nameSchema(t: Messages) {
  const m = t.workers;
  return z.object({ name: z.string().trim().min(1, m.nameRequired).max(64, m.nameTooLong) });
}

type NameValues = z.infer<ReturnType<typeof nameSchema>>;

function Stat({ label, value, percent }: { label: string; value: string; percent?: number }) {
  return (
    <div className="min-w-0 space-y-1.5">
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="text-muted-foreground truncate">{label}</span>
        <span className="font-medium tabular-nums">{value}</span>
      </div>
      {percent !== undefined ? (
        <div className="bg-muted h-1.5 overflow-hidden rounded-full" aria-hidden>
          <div
            className={cn(
              "h-full rounded-full transition-[width] duration-500",
              percent >= 90 ? "bg-destructive" : percent >= 75 ? "bg-warning" : "bg-primary",
            )}
            style={{ width: `${Math.min(100, Math.max(0, percent))}%` }}
          />
        </div>
      ) : null}
    </div>
  );
}

function WorkerItem({
  worker,
  cameraNames,
  onRename,
  onRemove,
}: {
  worker: Worker;
  cameraNames: Map<number, string>;
  onRename: () => void;
  onRemove: () => void;
}) {
  const { t, f } = useI18n();
  const m = t.workers;
  const { info, load } = worker;
  const offset = worker.clock_offset_seconds;
  const machine = info
    ? [
        m.cores(f.count(info.physical_cores), f.count(info.logical_cpus)),
        info.cpu_limit ? m.cpuLimit(f.decimal(info.cpu_limit, 1)) : null,
        m.memory(f.bytes(info.memory_total_bytes)),
        info.gpu,
      ]
        .filter(Boolean)
        .join(" · ")
    : null;
  const cameras = worker.cameras.map((id) => cameraNames.get(id) ?? `#${id}`);

  return (
    <li className="space-y-4 rounded-xl border p-4">
      <div className="flex items-start gap-3">
        <span
          className={cn(
            "mt-1.5 size-2.5 shrink-0 rounded-full",
            worker.online ? "bg-success" : "bg-muted-foreground/40",
          )}
          aria-hidden
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate font-medium">{worker.name}</span>
            {worker.online && load ? (
              <Badge variant="outline" className={cn("gap-1.5 font-normal", LEVEL_TONE[load.level])}>
                <span className="size-1.5 rounded-full bg-current" aria-hidden />
                {t.load.levels[load.level]}
              </Badge>
            ) : (
              <Badge variant="outline" className="font-normal">
                {worker.online ? m.online : m.offline}
              </Badge>
            )}
          </div>
          <div className="text-muted-foreground mt-0.5 truncate text-xs">
            {worker.online
              ? [info?.hostname, worker.address, worker.version ? m.version(worker.version) : null]
                  .filter(Boolean)
                  .join(" · ")
              : worker.last_seen_at
                ? m.lastSeen(f.relative(worker.last_seen_at))
                : m.neverConnected}
          </div>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" aria-label={m.actionsFor(worker.name)}>
              <MoreHorizontal aria-hidden />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={onRename}>
              <Pencil aria-hidden /> {m.rename}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onSelect={onRemove}>
              <Trash2 aria-hidden /> {m.remove}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {worker.online && load ? (
        <div className="grid gap-4 sm:grid-cols-3">
          <Stat label={m.cpu} value={f.percent(load.cpu_percent / 100)} percent={load.cpu_percent} />
          <Stat
            label={m.inference}
            value={f.percent(load.detector_busy_percent / 100)}
            percent={load.detector_busy_percent}
          />
          {load.gpu_percent !== null && load.gpu_percent !== undefined ? (
            <Stat label={m.gpu} value={f.percent(load.gpu_percent / 100)} percent={load.gpu_percent} />
          ) : (
            <Stat
              label={t.load.memory}
              value={f.percent(load.memory_percent / 100)}
              percent={load.memory_percent}
            />
          )}
        </div>
      ) : null}

      {machine || info ? (
        <div className="text-muted-foreground space-y-0.5 text-xs">
          {machine ? <div className="truncate">{machine}</div> : null}
          {info ? (
            <div className="truncate">
              {t.load.inferenceOn(
                info.backend === "openvino" ? "OpenVINO" : "PyTorch",
                deviceLabel(info.device, t.load, info.gpu),
              )}
              {load ? ` · ${load.model}` : null}
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="text-sm">
        <span className="text-muted-foreground">{m.cameras(f.count(cameras.length))}</span>
        {cameras.length > 0 ? <span className="ml-2">{cameras.join(", ")}</span> : null}
      </div>

      {offset !== null && Math.abs(offset) > CLOCK_WARNING_SECONDS ? (
        <Alert>
          <Clock aria-hidden />
          <AlertDescription>{m.clockOffset(f.decimal(Math.abs(offset), 0))}</AlertDescription>
        </Alert>
      ) : null}
    </li>
  );
}

function LocalNodeCard({ cluster }: { cluster: Cluster }) {
  const { t, f } = useI18n();
  const m = t.workers;
  const localProcessing = useLocalProcessing();
  const pending = localProcessing.isPending ? localProcessing.variables : undefined;
  const enabled = pending ?? cluster.local_processing;
  return (
    <Card>
      <CardHeader>
        <CardTitle>{m.serverTitle}</CardTitle>
        <CardDescription>{m.serverDescription}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Field orientation="horizontal">
          <Switch
            id="local-processing"
            checked={enabled}
            disabled={localProcessing.isPending}
            onCheckedChange={(value) => localProcessing.mutate(value)}
          />
          <FieldContent>
            <FieldLabel htmlFor="local-processing">{m.localProcessing}</FieldLabel>
            <FieldDescription>{m.localProcessingHint}</FieldDescription>
          </FieldContent>
        </Field>
        <p className="text-muted-foreground flex items-center gap-2 text-sm">
          <Server className="size-4" aria-hidden />
          {m.localCameras(f.count(cluster.local_cameras.length))}
        </p>
        {cluster.unplaced_cameras.length > 0 ? (
          <Alert variant="destructive">
            <AlertTriangle aria-hidden />
            <AlertTitle>{m.unplacedTitle}</AlertTitle>
            <AlertDescription>{m.unplaced(f.count(cluster.unplaced_cameras.length))}</AlertDescription>
          </Alert>
        ) : null}
      </CardContent>
    </Card>
  );
}

function NameDialog({
  open,
  onOpenChange,
  title,
  description,
  initial,
  submitLabel,
  pending,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  initial: string;
  submitLabel: string;
  pending: boolean;
  onSubmit: (name: string) => void;
}) {
  const { t } = useI18n();
  const m = t.workers;
  const schema = useMemo(() => nameSchema(t), [t]);
  const form = useForm<NameValues>({ resolver: zodResolver(schema), defaultValues: { name: initial } });
  const { reset } = form;
  useEffect(() => {
    if (open) reset({ name: initial });
  }, [open, initial, reset]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <form id="worker-name-form" onSubmit={form.handleSubmit(({ name }) => onSubmit(name))} noValidate>
          <FieldGroup>
            <Field data-invalid={Boolean(form.formState.errors.name)}>
              <FieldLabel htmlFor="worker-name">{m.name}</FieldLabel>
              <Input
                id="worker-name"
                placeholder={m.namePlaceholder}
                autoComplete="off"
                aria-invalid={Boolean(form.formState.errors.name)}
                {...form.register("name")}
              />
              <FieldError errors={[form.formState.errors.name]} />
            </Field>
          </FieldGroup>
        </form>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            {t.common.cancel}
          </Button>
          <Button type="submit" form="worker-name-form" disabled={pending}>
            {pending ? <Spinner /> : null}
            {submitLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CommandDialog({ created, onClose }: { created: WorkerCreated; onClose: () => void }) {
  const { t } = useI18n();
  const m = t.workers;
  const [variant, setVariant] = useState<"cpu" | "gpu">("cpu");
  const workers = useWorkers();
  const commandRef = useRef<HTMLPreElement>(null);
  const command = created.commands[variant];
  const online = workers.data?.workers.some((worker) => worker.id === created.worker.id && worker.online) ?? false;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      toast.success(m.copied);
    } catch {
      // Clipboard access needs HTTPS; select the text so it can be copied by hand.
      if (commandRef.current) window.getSelection()?.selectAllChildren(commandRef.current);
      toast.error(m.copyFailed);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{m.commandTitle}</DialogTitle>
          <DialogDescription>{m.commandDescription}</DialogDescription>
        </DialogHeader>
        <div className="min-w-0 space-y-3">
          <ToggleGroup
            type="single"
            variant="outline"
            size="sm"
            spacing={0}
            value={variant}
            onValueChange={(value) => {
              if (value === "cpu" || value === "gpu") setVariant(value);
            }}
          >
            <ToggleGroupItem value="cpu">{m.cpuVariant}</ToggleGroupItem>
            <ToggleGroupItem value="gpu">{m.gpuVariant}</ToggleGroupItem>
          </ToggleGroup>
          <pre
            ref={commandRef}
            className="bg-muted max-h-48 overflow-auto rounded-lg p-3 font-mono text-xs leading-relaxed break-all whitespace-pre-wrap select-all"
          >
            {command}
          </pre>
          {variant === "gpu" ? <p className="text-muted-foreground text-xs">{m.gpuHint}</p> : null}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Button type="button" variant="outline" onClick={() => void copy()}>
              <Copy aria-hidden /> {m.copy}
            </Button>
            <span className="flex items-center gap-2 text-sm" role="status">
              {online ? (
                <>
                  <CircleCheck className="text-success size-4" aria-hidden /> {m.connected}
                </>
              ) : (
                <>
                  <Spinner /> <span className="text-muted-foreground">{m.waiting}</span>
                </>
              )}
            </span>
          </div>
        </div>
        <DialogFooter>
          <Button type="button" onClick={onClose}>
            {m.done}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

type DialogState =
  | { kind: "add" }
  | { kind: "command"; created: WorkerCreated }
  | { kind: "rename"; worker: Worker }
  | { kind: "remove"; worker: Worker }
  | { kind: "rebalance" };

function WorkersCard({ cluster }: { cluster: Cluster }) {
  const { t } = useI18n();
  const m = t.workers;
  const cameras = useCameras();
  const createWorker = useCreateWorker();
  const renameWorker = useRenameWorker();
  const deleteWorker = useDeleteWorker();
  const rebalance = useRebalance();
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const close = () => setDialog(null);
  const cameraNames = useMemo(
    () => new Map((cameras.data ?? []).map((camera) => [camera.id, camera.name])),
    [cameras.data],
  );
  const online = cluster.workers.filter((worker) => worker.online).length;
  const add = () => setDialog({ kind: "add" });

  return (
    <Card>
      <CardHeader>
        <CardTitle>{m.title}</CardTitle>
        <CardDescription>{m.description}</CardDescription>
        <CardAction className="flex flex-wrap justify-end gap-2">
          {cluster.workers.length > 0 ? (
            <Button
              variant="outline"
              onClick={() => setDialog({ kind: "rebalance" })}
              disabled={rebalance.isPending || online === 0}
            >
              {rebalance.isPending ? <Spinner /> : <Shuffle aria-hidden />} {m.rebalance}
            </Button>
          ) : null}
          <Button onClick={add}>
            <Plus aria-hidden /> {m.add}
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        {cluster.workers.length === 0 ? (
          <EmptyState
            icon={<Server aria-hidden />}
            title={m.emptyTitle}
            description={m.emptyDescription}
            action={
              <Button onClick={add}>
                <Plus aria-hidden /> {m.add}
              </Button>
            }
          />
        ) : (
          <ul className="space-y-3">
            {cluster.workers.map((worker) => (
              <WorkerItem
                key={worker.id}
                worker={worker}
                cameraNames={cameraNames}
                onRename={() => setDialog({ kind: "rename", worker })}
                onRemove={() => setDialog({ kind: "remove", worker })}
              />
            ))}
          </ul>
        )}
      </CardContent>

      <NameDialog
        open={dialog?.kind === "add"}
        onOpenChange={(open) => !open && close()}
        title={m.addTitle}
        description={m.addDescription}
        initial=""
        submitLabel={m.create}
        pending={createWorker.isPending}
        onSubmit={(name) =>
          createWorker.mutate(name, { onSuccess: (created) => setDialog({ kind: "command", created }) })
        }
      />
      <NameDialog
        open={dialog?.kind === "rename"}
        onOpenChange={(open) => !open && close()}
        title={m.renameTitle}
        description={m.addDescription}
        initial={dialog?.kind === "rename" ? dialog.worker.name : ""}
        submitLabel={t.common.saveChanges}
        pending={renameWorker.isPending}
        onSubmit={(name) => {
          if (dialog?.kind === "rename") renameWorker.mutate({ id: dialog.worker.id, name }, { onSuccess: close });
        }}
      />
      {dialog?.kind === "command" ? <CommandDialog created={dialog.created} onClose={close} /> : null}
      <AlertDialog open={dialog?.kind === "remove"} onOpenChange={(open) => !open && close()}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{m.removeTitle}</AlertDialogTitle>
            <AlertDialogDescription>{m.removeDescription}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                if (dialog?.kind === "remove") deleteWorker.mutate(dialog.worker.id);
                close();
              }}
            >
              {m.removeConfirm}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <AlertDialog open={dialog?.kind === "rebalance"} onOpenChange={(open) => !open && close()}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{m.rebalanceTitle}</AlertDialogTitle>
            <AlertDialogDescription>{m.rebalanceDescription}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                rebalance.mutate(undefined);
                close();
              }}
            >
              {m.rebalanceConfirm}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}

function WorkersSkeleton() {
  return (
    <div className="space-y-6">
      {Array.from({ length: 2 }, (_, index) => (
        <Card key={index}>
          <CardHeader className="gap-2">
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-4 w-72" />
          </CardHeader>
          <CardContent>
            <Skeleton className="h-24 w-full rounded-xl" />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

/** Settings → Workers: this server's share of the cameras and the workers that take the rest. */
export function WorkersSettings() {
  const workers = useWorkers();
  const { t } = useI18n();
  if (workers.isPending) return <WorkersSkeleton />;
  if (workers.isError) {
    return <ErrorState title={t.errors.loadData} error={workers.error} onRetry={() => void workers.refetch()} />;
  }
  return (
    <div className="space-y-6">
      <LocalNodeCard cluster={workers.data} />
      <WorkersCard cluster={workers.data} />
    </div>
  );
}
