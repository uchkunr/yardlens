"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { Send } from "lucide-react";
import { useEffect } from "react";
import { Controller, useForm } from "react-hook-form";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import type { AppSettings, EventType } from "@/lib/api/client";
import { useTestTelegram, useUpdateSettings } from "@/lib/api/queries";
import { EVENT_TYPE_LABELS, EVENT_TYPES } from "@/lib/format";

const telegramSchema = z
  .object({
    enabled: z.boolean(),
    bot_token: z.string().trim().max(256),
    chat_id: z.string().trim().max(64),
    notify_events: z.array(z.enum(["object_entered", "line_crossed_in", "line_crossed_out", "camera_online", "camera_offline"])),
    min_interval_seconds: z.number({ error: "Enter a number" }).int().min(0).max(3600),
  })
  .superRefine((value, context) => {
    if (!value.enabled) return;
    if (!value.chat_id) context.addIssue({ code: "custom", path: ["chat_id"], message: "Chat ID is required" });
  });

type TelegramValues = z.infer<typeof telegramSchema>;

function toValues(settings: AppSettings): TelegramValues {
  const { telegram } = settings;
  return {
    enabled: telegram.enabled,
    bot_token: "",
    chat_id: telegram.chat_id ?? "",
    notify_events: telegram.notify_events,
    min_interval_seconds: telegram.min_interval_seconds,
  };
}

export function IntegrationsSettings({ settings }: { settings: AppSettings }) {
  const update = useUpdateSettings("Telegram settings saved");
  const sendTest = useTestTelegram();
  const form = useForm<TelegramValues>({ resolver: zodResolver(telegramSchema), defaultValues: toValues(settings) });
  const tokenSet = settings.telegram.bot_token_set;

  const { reset } = form;
  useEffect(() => reset(toValues(settings)), [settings, reset]);

  const onSubmit = form.handleSubmit((values) => {
    if (values.enabled && !values.bot_token && !tokenSet) {
      form.setError("bot_token", { message: "Bot token is required" });
      return;
    }
    update.mutate({
      telegram: {
        enabled: values.enabled,
        // An empty field keeps the stored token.
        ...(values.bot_token ? { bot_token: values.bot_token } : {}),
        chat_id: values.chat_id || null,
        notify_events: values.notify_events,
        min_interval_seconds: values.min_interval_seconds,
      },
    });
  });

  const testMessage = () => {
    const { bot_token, chat_id } = form.getValues();
    sendTest.mutate({ bot_token: bot_token || undefined, chat_id: chat_id || undefined });
  };

  const errors = form.formState.errors;

  return (
    <Card>
      <form onSubmit={onSubmit} noValidate>
        <CardHeader>
          <CardTitle>Telegram notifications</CardTitle>
          <CardDescription>
            Send events with their snapshot to a Telegram chat. Create a bot with @BotFather, add it to your chat, then
            paste its token and the chat ID.
          </CardDescription>
        </CardHeader>
        <CardContent className="py-6">
          <FieldGroup>
            <Field orientation="horizontal">
              <Controller
                control={form.control}
                name="enabled"
                render={({ field }) => (
                  <Switch id="telegram-enabled" checked={field.value} onCheckedChange={field.onChange} />
                )}
              />
              <FieldLabel htmlFor="telegram-enabled">Enable notifications</FieldLabel>
            </Field>

            <div className="grid gap-5 md:grid-cols-2">
              <Field data-invalid={Boolean(errors.bot_token)}>
                <FieldLabel htmlFor="telegram-token">Bot token</FieldLabel>
                <Input
                  id="telegram-token"
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder={tokenSet ? `Saved (${settings.telegram.bot_token_hint ?? "hidden"})` : "123456789:AA…"}
                  aria-invalid={Boolean(errors.bot_token)}
                  {...form.register("bot_token")}
                />
                <FieldDescription>
                  {tokenSet ? "Leave empty to keep the saved token." : "Stored on the server, never shown again."}
                </FieldDescription>
                <FieldError errors={[errors.bot_token]} />
              </Field>
              <Field data-invalid={Boolean(errors.chat_id)}>
                <FieldLabel htmlFor="telegram-chat">Chat ID</FieldLabel>
                <Input
                  id="telegram-chat"
                  autoComplete="off"
                  placeholder="-1001234567890"
                  aria-invalid={Boolean(errors.chat_id)}
                  {...form.register("chat_id")}
                />
                <FieldDescription>User, group or channel ID.</FieldDescription>
                <FieldError errors={[errors.chat_id]} />
              </Field>
            </div>

            <Controller
              control={form.control}
              name="notify_events"
              render={({ field }) => (
                <FieldSet>
                  <FieldLegend variant="label">Notify on</FieldLegend>
                  <div className="grid gap-3 sm:grid-cols-2">
                    {EVENT_TYPES.map((type: EventType) => {
                      const id = `notify-${type}`;
                      const checked = field.value.includes(type);
                      return (
                        <Field key={type} orientation="horizontal">
                          <Checkbox
                            id={id}
                            checked={checked}
                            onCheckedChange={(next) =>
                              field.onChange(next === true ? [...field.value, type] : field.value.filter((item) => item !== type))
                            }
                          />
                          <FieldLabel htmlFor={id} className="font-normal">
                            {EVENT_TYPE_LABELS[type]}
                          </FieldLabel>
                        </Field>
                      );
                    })}
                  </div>
                </FieldSet>
              )}
            />

            <Field data-invalid={Boolean(errors.min_interval_seconds)} className="sm:w-60">
              <FieldLabel htmlFor="telegram-interval">Minimum interval (seconds)</FieldLabel>
              <Input
                id="telegram-interval"
                type="number"
                inputMode="numeric"
                min={0}
                max={3600}
                aria-invalid={Boolean(errors.min_interval_seconds)}
                {...form.register("min_interval_seconds", { valueAsNumber: true })}
              />
              <FieldDescription>Per camera and event type, to avoid floods.</FieldDescription>
              <FieldError errors={[errors.min_interval_seconds]} />
            </Field>
          </FieldGroup>
        </CardContent>
        <CardFooter className="flex-wrap justify-between gap-2 border-t">
          <Button type="button" variant="outline" onClick={testMessage} disabled={sendTest.isPending}>
            {sendTest.isPending ? <Spinner /> : <Send aria-hidden />}
            Send test message
          </Button>
          <div className="flex gap-2">
            <Button type="button" variant="ghost" onClick={() => reset(toValues(settings))} disabled={!form.formState.isDirty}>
              Discard
            </Button>
            <Button type="submit" disabled={update.isPending || !form.formState.isDirty}>
              {update.isPending ? <Spinner /> : null}
              Save changes
            </Button>
          </div>
        </CardFooter>
      </form>
    </Card>
  );
}
