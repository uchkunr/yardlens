"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { Send } from "lucide-react";
import { useEffect, useMemo } from "react";
import { Controller, useForm } from "react-hook-form";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { LOCALE_NAMES, LOCALES } from "@/i18n/config";
import type { Messages } from "@/i18n/messages";
import { useI18n } from "@/i18n/provider";
import type { AppSettings, EventType } from "@/lib/api/client";
import { useTestTelegram, useUpdateSettings } from "@/lib/api/queries";
import { EVENT_TYPES } from "@/lib/format";

function telegramSchema(t: Messages) {
  return z
    .object({
      enabled: z.boolean(),
      bot_token: z.string().trim().max(256),
      chat_id: z.string().trim().max(64),
      notify_events: z.array(z.enum(EVENT_TYPES)),
      min_interval_seconds: z.number({ error: t.common.enterNumber }).int().min(0).max(3600),
      language: z.enum(LOCALES),
    })
    .superRefine((value, context) => {
      if (!value.enabled) return;
      if (!value.chat_id) context.addIssue({ code: "custom", path: ["chat_id"], message: t.telegram.chatRequired });
    });
}

type TelegramValues = z.infer<ReturnType<typeof telegramSchema>>;

function toValues(settings: AppSettings): TelegramValues {
  const { telegram } = settings;
  return {
    enabled: telegram.enabled,
    bot_token: "",
    chat_id: telegram.chat_id ?? "",
    notify_events: telegram.notify_events,
    min_interval_seconds: telegram.min_interval_seconds,
    language: telegram.language,
  };
}

export function IntegrationsSettings({ settings }: { settings: AppSettings }) {
  const { t } = useI18n();
  const m = t.telegram;
  const update = useUpdateSettings(m.saved);
  const sendTest = useTestTelegram();
  const schema = useMemo(() => telegramSchema(t), [t]);
  const form = useForm<TelegramValues>({ resolver: zodResolver(schema), defaultValues: toValues(settings) });
  const tokenSet = settings.telegram.bot_token_set;

  const { reset } = form;
  useEffect(() => reset(toValues(settings)), [settings, reset]);

  const onSubmit = form.handleSubmit((values) => {
    if (values.enabled && !values.bot_token && !tokenSet) {
      form.setError("bot_token", { message: m.tokenRequired });
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
        language: values.language,
      },
    });
  });

  const testMessage = () => {
    const { bot_token, chat_id, language } = form.getValues();
    sendTest.mutate({ bot_token: bot_token || undefined, chat_id: chat_id || undefined, language });
  };

  const errors = form.formState.errors;

  return (
    <Card>
      <form onSubmit={onSubmit} noValidate>
        <CardHeader>
          <CardTitle>{m.title}</CardTitle>
          <CardDescription>{m.description}</CardDescription>
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
              <FieldLabel htmlFor="telegram-enabled">{m.enable}</FieldLabel>
            </Field>

            <div className="grid gap-5 md:grid-cols-2">
              <Field data-invalid={Boolean(errors.bot_token)}>
                <FieldLabel htmlFor="telegram-token">{m.token}</FieldLabel>
                <Input
                  id="telegram-token"
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder={tokenSet ? m.tokenSaved(settings.telegram.bot_token_hint ?? m.tokenHidden) : "123456789:AA…"}
                  aria-invalid={Boolean(errors.bot_token)}
                  {...form.register("bot_token")}
                />
                <FieldDescription>{tokenSet ? m.tokenKeep : m.tokenStored}</FieldDescription>
                <FieldError errors={[errors.bot_token]} />
              </Field>
              <Field data-invalid={Boolean(errors.chat_id)}>
                <FieldLabel htmlFor="telegram-chat">{m.chatId}</FieldLabel>
                <Input
                  id="telegram-chat"
                  autoComplete="off"
                  placeholder="-1001234567890"
                  aria-invalid={Boolean(errors.chat_id)}
                  {...form.register("chat_id")}
                />
                <FieldDescription>{m.chatHint}</FieldDescription>
                <FieldError errors={[errors.chat_id]} />
              </Field>
            </div>

            <Controller
              control={form.control}
              name="notify_events"
              render={({ field }) => (
                <FieldSet>
                  <FieldLegend variant="label">{m.notifyOn}</FieldLegend>
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
                            {t.eventTypes[type]}
                          </FieldLabel>
                        </Field>
                      );
                    })}
                  </div>
                </FieldSet>
              )}
            />

            <div className="grid gap-5 sm:grid-cols-2 md:w-fit md:grid-cols-[15rem_15rem]">
              <Field data-invalid={Boolean(errors.min_interval_seconds)}>
                <FieldLabel htmlFor="telegram-interval">{m.interval}</FieldLabel>
                <Input
                  id="telegram-interval"
                  type="number"
                  inputMode="numeric"
                  min={0}
                  max={3600}
                  aria-invalid={Boolean(errors.min_interval_seconds)}
                  {...form.register("min_interval_seconds", { valueAsNumber: true })}
                />
                <FieldDescription>{m.intervalHint}</FieldDescription>
                <FieldError errors={[errors.min_interval_seconds]} />
              </Field>
              <Field>
                <FieldLabel htmlFor="telegram-language">{m.language}</FieldLabel>
                <Controller
                  control={form.control}
                  name="language"
                  render={({ field }) => (
                    <Select value={field.value} onValueChange={field.onChange}>
                      <SelectTrigger id="telegram-language" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {LOCALES.map((item) => (
                          <SelectItem key={item} value={item} lang={item}>
                            {LOCALE_NAMES[item]}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                />
              </Field>
            </div>
          </FieldGroup>
        </CardContent>
        <CardFooter className="flex-wrap justify-between gap-2 border-t">
          <Button type="button" variant="outline" onClick={testMessage} disabled={sendTest.isPending}>
            {sendTest.isPending ? <Spinner /> : <Send aria-hidden />}
            {m.test}
          </Button>
          <div className="flex gap-2">
            <Button type="button" variant="ghost" onClick={() => reset(toValues(settings))} disabled={!form.formState.isDirty}>
              {t.common.discard}
            </Button>
            <Button type="submit" disabled={update.isPending || !form.formState.isDirty}>
              {update.isPending ? <Spinner /> : null}
              {t.common.saveChanges}
            </Button>
          </div>
        </CardFooter>
      </form>
    </Card>
  );
}
