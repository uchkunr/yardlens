import { format, formatDistanceStrict, parseISO, type Locale as DateFnsLocale } from "date-fns";
import { enUS, ru } from "date-fns/locale";

import { CLASS_NAMES_RU } from "@/i18n/classes";
import type { Locale } from "@/i18n/config";
import type { Messages } from "@/i18n/messages/ru";
import { translateServerMessage } from "@/i18n/server-messages";
import { ApiError } from "@/lib/api/client";

const INTL_LOCALES: Record<Locale, string> = { ru: "ru-RU", en: "en-US" };

const DATE_FNS_LOCALES: Record<Locale, DateFnsLocale> = { ru, en: enUS };

export type Formatters = ReturnType<typeof createFormatters>;

/** Numbers, dates, durations, class names and error messages in the given language. */
export function createFormatters(locale: Locale, messages: Messages) {
  const tag = INTL_LOCALES[locale];
  const dateLocale = DATE_FNS_LOCALES[locale];
  const integer = new Intl.NumberFormat(tag);
  const percent = new Intl.NumberFormat(tag, { style: "percent" });
  const gigabytes = new Intl.NumberFormat(tag, { style: "unit", unit: "gigabyte", maximumFractionDigits: 1 });
  const megabytes = new Intl.NumberFormat(tag, { style: "unit", unit: "megabyte", maximumFractionDigits: 0 });
  const dateTime = new Intl.DateTimeFormat(tag, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const calendarDate = new Intl.DateTimeFormat(tag, { day: "2-digit", month: "2-digit", year: "numeric" });
  const clock = new Intl.DateTimeFormat(tag, { hour: "2-digit", minute: "2-digit" });
  // Chart axis: "09:00" in Russian, "9 AM" in English.
  const hour = new Intl.DateTimeFormat(tag, locale === "ru" ? { hour: "2-digit", minute: "2-digit" } : { hour: "numeric" });
  const { time, errors } = messages;
  // "2 д 5 ч" in Russian, "2d 5h" in English.
  const unit = (value: number, name: string) => (locale === "ru" ? `${value} ${name}` : `${value}${name}`);

  return {
    count: (value: number) => integer.format(value),

    decimal: (value: number, digits: number) =>
      new Intl.NumberFormat(tag, { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value),

    percent: (ratio: number) => percent.format(ratio),

    /** "15,6 ГБ" / "15.6 GB"; megabytes below a gigabyte. */
    bytes: (value: number) =>
      value >= 2 ** 30 ? gigabytes.format(value / 2 ** 30) : megabytes.format(value / 2 ** 20),

    dateTime: (iso: string) => dateTime.format(new Date(iso)),

    /** "14.10.2026" or "10/14/2026". */
    date: (iso: string) => calendarDate.format(new Date(iso)),

    /** A `yyyy-MM-dd` calendar day in full: "14.10.2026" or "10/14/2026". */
    day: (day: string) => calendarDate.format(parseISO(day)),

    /** "09:47" or "09:47 AM". */
    time: (iso: string) => clock.format(new Date(iso)),

    hour: (iso: string) => hour.format(new Date(iso)),

    relative(iso: string, now: number = Date.now()) {
      const date = new Date(iso);
      if (Math.abs(now - date.getTime()) < 10_000) return time.justNow;
      return formatDistanceStrict(date, now, { addSuffix: true, locale: dateLocale });
    },

    duration(totalSeconds: number) {
      const seconds = Math.max(0, Math.floor(totalSeconds));
      const days = Math.floor(seconds / 86_400);
      const hours = Math.floor((seconds % 86_400) / 3_600);
      const minutes = Math.floor((seconds % 3_600) / 60);
      if (days > 0) return `${unit(days, time.days)} ${unit(hours, time.hours)}`;
      if (hours > 0) return `${unit(hours, time.hours)} ${unit(minutes, time.minutes)}`;
      if (minutes > 0) return unit(minutes, time.minutes);
      return unit(seconds, time.seconds);
    },

    /** A `yyyy-MM-dd` calendar day: "14 окт." or "Oct 14". */
    shortDate: (day: string) => format(parseISO(day), locale === "ru" ? "d MMM" : "MMM d", { locale: dateLocale }),

    /** A COCO class name as shown to people: "Автомобиль" or "Car". */
    className(name: string | null | undefined) {
      if (!name) return "-";
      const label = locale === "ru" ? (CLASS_NAMES_RU[name] ?? name) : name;
      return label.charAt(0).toUpperCase() + label.slice(1);
    },

    /** A message the API produced (camera errors, test results) in this language. */
    server: (message: string) => translateServerMessage(message, locale),

    error(error: unknown) {
      if (error instanceof ApiError) {
        switch (error.kind) {
          case "unreachable":
            return errors.unreachable;
          case "unavailable":
            return errors.unavailable;
          case "failed":
            return errors.requestFailed(String(error.status));
          case "server":
            return translateServerMessage(error.message, locale);
        }
      }
      return error instanceof Error ? error.message : errors.generic;
    },
  };
}
