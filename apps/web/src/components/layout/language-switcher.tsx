"use client";

import { Languages } from "lucide-react";
import { useRouter } from "next/navigation";
import { useTransition } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { isLocale, LOCALE_COOKIE, LOCALE_NAMES, LOCALES } from "@/i18n/config";
import { useI18n } from "@/i18n/provider";

const ONE_YEAR_SECONDS = 365 * 24 * 60 * 60;

export function LanguageSwitcher() {
  const { locale, t } = useI18n();
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  const change = (next: string) => {
    if (!isLocale(next) || next === locale) return;
    document.cookie = `${LOCALE_COOKIE}=${next}; path=/; max-age=${ONE_YEAR_SECONDS}; samesite=lax`;
    // The server reads the cookie; a refresh re-renders in the new language and keeps client state.
    startTransition(() => router.refresh());
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" aria-label={t.common.changeLanguage} disabled={pending}>
          <Languages aria-hidden />
          <span className="text-xs font-medium uppercase">{locale}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-36">
        <DropdownMenuRadioGroup value={locale} onValueChange={change}>
          {LOCALES.map((item) => (
            <DropdownMenuRadioItem key={item} value={item} lang={item}>
              {LOCALE_NAMES[item]}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
