"use client";

import { useState } from "react";

import { ClassPicker } from "@/components/settings/detection-settings";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { useI18n } from "@/i18n/provider";
import type { AppSettings, Camera, CameraDetection } from "@/lib/api/client";
import { useModelClasses, useUpdateCamera } from "@/lib/api/queries";

type Values = {
  model: string;
  confidence: number;
  classes: string[];
  cargo: string[];
};

const sameSet = (a: string[], b: string[]) =>
  a.length === b.length && a.every((item) => b.includes(item));

type General = {
  model: string;
  confidence: number;
  classes: string[];
  cargo: string[];
};

function generalOf(settings: AppSettings): General {
  const {
    model,
    confidence = 0.35,
    classes = [],
    cargo_classes: cargo = [],
  } = settings.detection;
  return { model, confidence, classes, cargo };
}

/** What the camera uses now: its own settings over the general ones. */
function effective(camera: Camera, general: General): Values {
  const own = camera.detection;
  const classes = own?.classes ?? general.classes;
  return {
    model: own?.model ?? general.model,
    confidence: own?.confidence ?? general.confidence,
    classes,
    cargo:
      own?.cargo_classes ??
      general.cargo.filter((name) => classes.includes(name)),
  };
}

/** Only what differs from the general settings is stored, so later general changes still apply. */
function differences(values: Values, general: General): CameraDetection {
  const inheritedCargo = general.cargo.filter((name) =>
    values.classes.includes(name),
  );
  return {
    model: values.model === general.model ? null : values.model,
    confidence:
      values.confidence === general.confidence ? null : values.confidence,
    classes: sameSet(values.classes, general.classes) ? null : values.classes,
    cargo_classes: sameSet(values.cargo, inheritedCargo) ? null : values.cargo,
  };
}

export function CameraDetectionDialog({
  camera,
  settings,
  onOpenChange,
}: {
  camera: Camera;
  settings: AppSettings;
  onOpenChange: (open: boolean) => void;
}) {
  const { t, f } = useI18n();
  const m = t.cameraDetection;
  const d = t.detection;
  const general = generalOf(settings);
  const [own, setOwn] = useState(camera.detection !== null);
  const [picked, setPicked] = useState<Values>(() =>
    effective(camera, general),
  );
  const update = useUpdateCamera();

  const otherModel = picked.model !== general.model;
  const modelClasses = useModelClasses(picked.model, own && otherModel);
  const options = otherModel
    ? (modelClasses.data ?? [])
    : settings.available_classes;
  // Classes picked for another model drop out once this model's own list is known.
  const classes =
    otherModel && !modelClasses.data
      ? picked.classes
      : picked.classes.filter((name) => options.includes(name));
  const values: Values = {
    ...picked,
    classes,
    cargo: picked.cargo.filter((name) => classes.includes(name)),
  };

  const set = (patch: Partial<Values>) => setPicked({ ...values, ...patch });
  const save = () =>
    update.mutate(
      {
        id: camera.id,
        body: { detection: own ? differences(values, general) : null },
      },
      { onSuccess: () => onOpenChange(false) },
    );
  const loadingClasses = own && otherModel && modelClasses.isPending;

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{m.title(camera.name)}</DialogTitle>
          <DialogDescription>{m.description}</DialogDescription>
        </DialogHeader>
        <FieldGroup>
          <Field orientation="horizontal">
            <Switch
              id="camera-own-detection"
              checked={own}
              onCheckedChange={setOwn}
            />
            <FieldContent>
              <FieldLabel htmlFor="camera-own-detection">{m.own}</FieldLabel>
              <FieldDescription>{own ? m.ownOn : m.ownOff}</FieldDescription>
            </FieldContent>
          </Field>

          {own ? (
            <>
              <Field>
                <FieldLabel htmlFor="camera-model">{d.model}</FieldLabel>
                <Select
                  value={values.model}
                  onValueChange={(model) => set({ model })}
                >
                  <SelectTrigger id="camera-model" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {settings.available_models.map((model) => (
                      <SelectItem key={model} value={model}>
                        {model === general.model
                          ? m.generalModel(model)
                          : model}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>

              <Field>
                <div className="flex items-center justify-between">
                  <FieldLabel htmlFor="camera-confidence">
                    {d.confidence}
                  </FieldLabel>
                  <span className="text-sm font-medium tabular-nums">
                    {f.decimal(values.confidence, 2)}
                  </span>
                </div>
                <Slider
                  id="camera-confidence"
                  min={0.05}
                  max={0.95}
                  step={0.05}
                  value={[values.confidence]}
                  onValueChange={([next]) =>
                    set({ confidence: next ?? values.confidence })
                  }
                  aria-label={d.confidence}
                />
              </Field>

              {loadingClasses ? (
                <p className="text-muted-foreground flex items-center gap-2 text-sm">
                  <Spinner /> {m.loadingClasses}
                </p>
              ) : (
                <>
                  <Field>
                    <FieldLabel htmlFor="camera-classes">
                      {d.classes}
                    </FieldLabel>
                    <ClassPicker
                      id="camera-classes"
                      placeholder={d.selectClasses}
                      value={values.classes}
                      onChange={(classes) =>
                        set({
                          classes,
                          cargo: values.cargo.filter((name) =>
                            classes.includes(name),
                          ),
                        })
                      }
                      options={options}
                      invalid={values.classes.length === 0}
                    />
                    {values.classes.length === 0 ? (
                      <FieldDescription className="text-destructive">
                        {d.atLeastOne}
                      </FieldDescription>
                    ) : null}
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="camera-cargo">{d.cargo}</FieldLabel>
                    <ClassPicker
                      id="camera-cargo"
                      placeholder={d.selectCargo}
                      value={values.cargo}
                      onChange={(cargo) => set({ cargo })}
                      options={values.classes}
                      invalid={false}
                    />
                    <FieldDescription>{m.cargoHint}</FieldDescription>
                  </Field>
                </>
              )}
            </>
          ) : null}
        </FieldGroup>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            {t.common.cancel}
          </Button>
          <Button
            type="button"
            onClick={save}
            disabled={
              update.isPending ||
              loadingClasses ||
              (own && values.classes.length === 0)
            }
          >
            {update.isPending ? <Spinner /> : null}
            {t.common.saveChanges}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
