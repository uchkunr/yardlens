export const LOCALES = ["ru", "en"] as const;

export type Locale = (typeof LOCALES)[number];

export const DEFAULT_LOCALE: Locale = "en";

/** Each language under its own name, so it is recognizable whatever language the UI is in. */
export const LOCALE_NAMES: Record<Locale, string> = { ru: "Русский", en: "English" };

/** Read on the server, so the first render is already in the chosen language. */
export const LOCALE_COOKIE = "yardlens-locale";

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value);
}
