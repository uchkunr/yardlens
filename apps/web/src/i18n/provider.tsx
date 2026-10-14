"use client";

import { createContext, useContext, useEffect, useMemo, type ReactNode } from "react";

import type { Locale } from "@/i18n/config";
import { createFormatters, type Formatters } from "@/i18n/format";
import { MESSAGES, type Messages } from "@/i18n/messages";

type I18n = {
  locale: Locale;
  /** UI copy in the current language. */
  t: Messages;
  /** Formatters for the current language. */
  f: Formatters;
};

const I18nContext = createContext<I18n | null>(null);

export function I18nProvider({ locale, children }: { locale: Locale; children: ReactNode }) {
  const value = useMemo(() => {
    const t = MESSAGES[locale];
    return { locale, t, f: createFormatters(locale, t) };
  }, [locale]);

  // The root layout's <html lang> is static; keep it in step with the chosen language.
  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  return <I18nContext value={value}>{children}</I18nContext>;
}

export function useI18n(): I18n {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n() needs an <I18nProvider> above it");
  return value;
}
