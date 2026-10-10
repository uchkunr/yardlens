"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { Check, ChevronsUpDown, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { z } from "zod";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Spinner } from "@/components/ui/spinner";
import type { AppSettings } from "@/lib/api/client";
import { useUpdateSettings } from "@/lib/api/queries";
import { classLabel } from "@/lib/format";
import { cn } from "@/lib/utils";

const MODEL_HINTS: Record<string, string> = {
  "yolo26n.pt": "Nano · fastest, recommended for CPU",
  "yolo26s.pt": "Small · more accurate, ~2× slower",
  "yolo26m.pt": "Medium · best accuracy, needs a GPU for many cameras",
  "yolo11n.pt": "YOLO11 nano · previous generation",
};

const detectionSchema = z.object({
  model: z.string().min(1),
  confidence: z.number().min(0.05).max(0.95),
  classes: z.array(z.string()).min(1, "Select at least one class"),
});

type DetectionValues = z.infer<typeof detectionSchema>;

function ClassPicker({
  value,
  onChange,
  options,
  invalid,
}: {
  value: string[];
  onChange: (value: string[]) => void;
  options: string[];
  invalid: boolean;
}) {
  const [open, setOpen] = useState(false);
  const toggle = (name: string) =>
    onChange(value.includes(name) ? value.filter((item) => item !== name) : [...value, name]);

  return (
    <div className="space-y-3">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            id="detection-classes"
            variant="outline"
            role="combobox"
            aria-expanded={open}
            aria-invalid={invalid}
            className="w-full justify-between font-normal sm:w-72"
          >
            {value.length === 0 ? "Select classes" : `${value.length} selected`}
            <ChevronsUpDown className="opacity-50" aria-hidden />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-72 p-0" align="start">
          <Command>
            <CommandInput placeholder="Search COCO classes…" />
            <CommandList>
              <CommandEmpty>No class found.</CommandEmpty>
              <CommandGroup>
                {options.map((name) => {
                  const selected = value.includes(name);
                  return (
                    <CommandItem key={name} value={name} onSelect={() => toggle(name)}>
                      <Check className={cn("size-4", selected ? "opacity-100" : "opacity-0")} aria-hidden />
                      {classLabel(name)}
                    </CommandItem>
                  );
                })}
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
      {value.length > 0 ? (
        <ul className="flex flex-wrap gap-1.5" aria-label="Selected classes">
          {value.map((name) => (
            <li key={name}>
              <Badge variant="secondary" className="gap-1 pr-1">
                {classLabel(name)}
                <button
                  type="button"
                  onClick={() => toggle(name)}
                  className="hover:bg-muted-foreground/20 focus-visible:ring-ring/50 rounded-sm p-0.5 outline-none focus-visible:ring-2"
                  aria-label={`Remove ${name}`}
                >
                  <X className="size-3" aria-hidden />
                </button>
              </Badge>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function DetectionSettings({ settings }: { settings: AppSettings }) {
  const update = useUpdateSettings("Detection settings saved");
  const form = useForm<DetectionValues>({
    resolver: zodResolver(detectionSchema),
    defaultValues: settings.detection,
  });

  const { reset } = form;
  useEffect(() => reset(settings.detection), [settings.detection, reset]);
  const model = useWatch({ control: form.control, name: "model" });

  const onSubmit = form.handleSubmit((values) => update.mutate({ detection: values }));
  const modelChanging = update.isPending && model !== settings.detection.model;

  return (
    <Card>
      <form onSubmit={onSubmit} noValidate>
        <CardHeader>
          <CardTitle>Detection</CardTitle>
          <CardDescription>Applies to every camera. Changes take effect on the next frame.</CardDescription>
        </CardHeader>
        <CardContent className="py-6">
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="detection-model">Model</FieldLabel>
              <Controller
                control={form.control}
                name="model"
                render={({ field }) => (
                  <Select value={field.value} onValueChange={field.onChange}>
                    <SelectTrigger id="detection-model" className="w-full sm:w-72">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {settings.available_models.map((model) => (
                        <SelectItem key={model} value={model}>
                          {model}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
              <FieldDescription>
                {MODEL_HINTS[model] ?? "Ultralytics YOLO weights"}. Weights are downloaded on first use.
              </FieldDescription>
            </Field>

            <Controller
              control={form.control}
              name="confidence"
              render={({ field }) => (
                <Field>
                  <div className="flex items-center justify-between sm:w-72">
                    <FieldLabel htmlFor="detection-confidence">Confidence threshold</FieldLabel>
                    <span className="text-sm font-medium tabular-nums">{field.value.toFixed(2)}</span>
                  </div>
                  <Slider
                    id="detection-confidence"
                    min={0.05}
                    max={0.95}
                    step={0.05}
                    value={[field.value]}
                    onValueChange={([next]) => field.onChange(next ?? field.value)}
                    className="sm:w-72"
                    aria-label="Confidence threshold"
                  />
                  <FieldDescription>Lower finds more objects but adds false positives.</FieldDescription>
                </Field>
              )}
            />

            <Controller
              control={form.control}
              name="classes"
              render={({ field, fieldState }) => (
                <Field data-invalid={fieldState.invalid}>
                  <FieldLabel htmlFor="detection-classes">Classes</FieldLabel>
                  <ClassPicker
                    value={field.value}
                    onChange={field.onChange}
                    options={settings.available_classes}
                    invalid={fieldState.invalid}
                  />
                  <FieldDescription>COCO classes to detect and track. Vehicles: car, truck, bus, motorcycle.</FieldDescription>
                  <FieldError errors={[fieldState.error]} />
                </Field>
              )}
            />
          </FieldGroup>
        </CardContent>
        <CardFooter className="justify-end gap-2 border-t">
          <Button type="button" variant="ghost" onClick={() => reset(settings.detection)} disabled={!form.formState.isDirty}>
            Discard
          </Button>
          <Button type="submit" disabled={update.isPending || !form.formState.isDirty}>
            {update.isPending ? <Spinner /> : null}
            {modelChanging ? "Loading model…" : "Save changes"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
