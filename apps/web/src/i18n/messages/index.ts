import type { Locale } from "@/i18n/config";
import { en } from "@/i18n/messages/en";
import { ru, type Messages } from "@/i18n/messages/ru";

export type { Messages };

export const MESSAGES: Record<Locale, Messages> = { ru, en };
