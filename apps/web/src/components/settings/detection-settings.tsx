"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { Check, ChevronsUpDown, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
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
import type { Messages } from "@/i18n/messages";
import { useI18n } from "@/i18n/provider";
import type { AppSettings } from "@/lib/api/client";
import { useUpdateSettings } from "@/lib/api/queries";
import { cn } from "@/lib/utils";

function detectionSchema(t: Messages) {
  return z.object({
    model: z.string().min(1),
    confidence: z.number().min(0.05).max(0.95),
    classes: z.array(z.string()).min(1, t.detection.atLeastOne),
    cargo_classes: z.array(z.string()),
  });
}

type DetectionValues = z.infer<ReturnType<typeof detectionSchema>>;

export function ClassPicker({
  id,
  placeholder,
  value,
  onChange,
  options,
  invalid,
}: {
  id: string;
  placeholder: string;
  value: string[];
  onChange: (value: string[]) => void;
  options: string[];
  invalid: boolean;
}) {
  const [open, setOpen] = useState(false);
  const { t, f } = useI18n();
  const m = t.detection;
  const toggle = (name: string) =>
    onChange(value.includes(name) ? value.filter((item) => item !== name) : [...value, name]);

  return (
    <div className="space-y-3">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            id={id}
            variant="outline"
            role="combobox"
            aria-expanded={open}
            aria-invalid={invalid}
            className="w-full justify-between font-normal sm:w-72"
          >
            {value.length === 0 ? placeholder : m.selected(f.count(value.length))}
            <ChevronsUpDown className="opacity-50" aria-hidden />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-72 p-0" align="start">
          <Command>
            <CommandInput placeholder={m.search} />
            <CommandList>
              <CommandEmpty>{m.noClass}</CommandEmpty>
              <CommandGroup>
                {options.map((name) => {
                  const selected = value.includes(name);
                  return (
                    <CommandItem key={name} value={name} keywords={[f.className(name)]} onSelect={() => toggle(name)}>
                      <Check className={cn("size-4", selected ? "opacity-100" : "opacity-0")} aria-hidden />
                      {f.className(name)}
                    </CommandItem>
                  );
                })}
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
      {value.length > 0 ? (
        <ul className="flex flex-wrap gap-1.5" aria-label={m.selectedLabel}>
          {value.map((name) => (
            <li key={name}>
              <Badge variant="secondary" className="gap-1 pr-1">
                {f.className(name)}
                <button
                  type="button"
                  onClick={() => toggle(name)}
                  className="hover:bg-muted-foreground/20 focus-visible:ring-ring/50 rounded-sm p-0.5 outline-none focus-visible:ring-2"
                  aria-label={m.removeClass(f.className(name))}
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
  const { t, f } = useI18n();
  const m = t.detection;
  const update = useUpdateSettings(m.saved);
  const schema = useMemo(() => detectionSchema(t), [t]);
  const form = useForm<DetectionValues>({
    resolver: zodResolver(schema),
    defaultValues: settings.detection,
  });

  const { reset } = form;
  useEffect(() => reset(settings.detection), [settings.detection, reset]);
  const model = useWatch({ control: form.control, name: "model" });
  const classes = useWatch({ control: form.control, name: "classes" });

  const onSubmit = form.handleSubmit((values) => update.mutate({ detection: values }));
  const modelChanging = update.isPending && model !== settings.detection.model;

  return (
    <Card>
      <form onSubmit={onSubmit} noValidate>
        <CardHeader>
          <CardTitle>{m.title}</CardTitle>
          <CardDescription>{m.description}</CardDescription>
        </CardHeader>
        <CardContent className="py-6">
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="detection-model">{m.model}</FieldLabel>
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
                {m.modelHints[model] ?? m.modelFallback}. {m.weightsNote}
              </FieldDescription>
            </Field>

            <Controller
              control={form.control}
              name="confidence"
              render={({ field }) => (
                <Field>
                  <div className="flex items-center justify-between sm:w-72">
                    <FieldLabel htmlFor="detection-confidence">{m.confidence}</FieldLabel>
                    <span className="text-sm font-medium tabular-nums">{f.decimal(field.value, 2)}</span>
                  </div>
                  <Slider
                    id="detection-confidence"
                    min={0.05}
                    max={0.95}
                    step={0.05}
                    value={[field.value]}
                    onValueChange={([next]) => field.onChange(next ?? field.value)}
                    className="sm:w-72"
                    aria-label={m.confidence}
                  />
                  <FieldDescription>{m.confidenceHint}</FieldDescription>
                </Field>
              )}
            />

            <Controller
              control={form.control}
              name="classes"
              render={({ field, fieldState }) => (
                <Field data-invalid={fieldState.invalid}>
                  <FieldLabel htmlFor="detection-classes">{m.classes}</FieldLabel>
                  <ClassPicker
                    id="detection-classes"
                    placeholder={m.selectClasses}
                    value={field.value}
                    onChange={(next) => {
                      field.onChange(next);
                      // Cargo must stay among the detected classes.
                      const cargo = form.getValues("cargo_classes");
                      if (cargo.some((name) => !next.includes(name))) {
                        form.setValue(
                          "cargo_classes",
                          cargo.filter((name) => next.includes(name)),
                          { shouldDirty: true },
                        );
                      }
                    }}
                    options={settings.available_classes}
                    invalid={fieldState.invalid}
                  />
                  <FieldDescription>{m.classesHint}</FieldDescription>
                  <FieldError errors={[fieldState.error]} />
                </Field>
              )}
            />

            <Controller
              control={form.control}
              name="cargo_classes"
              render={({ field, fieldState }) => (
                <Field data-invalid={fieldState.invalid}>
                  <FieldLabel htmlFor="detection-cargo">{m.cargo}</FieldLabel>
                  <ClassPicker
                    id="detection-cargo"
                    placeholder={m.selectCargo}
                    value={field.value}
                    onChange={field.onChange}
                    options={classes}
                    invalid={fieldState.invalid}
                  />
                  <FieldDescription>{m.cargoHint}</FieldDescription>
                  <FieldError errors={[fieldState.error]} />
                </Field>
              )}
            />
          </FieldGroup>
        </CardContent>
        <CardFooter className="justify-end gap-2 border-t">
          <Button type="button" variant="ghost" onClick={() => reset(settings.detection)} disabled={!form.formState.isDirty}>
            {t.common.discard}
          </Button>
          <Button type="submit" disabled={update.isPending || !form.formState.isDirty}>
            {update.isPending ? <Spinner /> : null}
            {modelChanging ? m.loadingModel : t.common.saveChanges}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
