import { cookies } from "next/headers";

import { DEFAULT_LOCALE, isLocale, LOCALE_COOKIE, type Locale } from "@/i18n/config";
import { MESSAGES, type Messages } from "@/i18n/messages";

/** The language picked in the header (stored in a cookie); Russian until someone picks another. */
export async function getLocale(): Promise<Locale> {
  const value = (await cookies()).get(LOCALE_COOKIE)?.value;
  return isLocale(value) ? value : DEFAULT_LOCALE;
}

export async function getMessages(): Promise<Messages> {
  return MESSAGES[await getLocale()];
}
