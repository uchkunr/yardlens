"use client";

import { usePathname } from "next/navigation";
import { Suspense } from "react";

import { NAV_ITEMS, isActivePath } from "@/components/layout/app-sidebar";
import { LanguageSwitcher } from "@/components/layout/language-switcher";
import { ThemeToggle } from "@/components/layout/theme-toggle";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { useI18n } from "@/i18n/provider";

function SectionTitle() {
  const pathname = usePathname();
  const { t } = useI18n();
  const section = NAV_ITEMS.find((item) => isActivePath(pathname, item.href));
  return <span className="text-sm font-medium">{section ? t.nav[section.key] : "YardLens"}</span>;
}

export function AppHeader() {
  return (
    <header className="bg-background/80 sticky top-0 z-20 flex h-14 shrink-0 items-center gap-2 border-b px-4 backdrop-blur md:rounded-t-xl lg:px-6">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mx-1 data-[orientation=vertical]:h-4" />
      <Suspense fallback={<span className="text-sm font-medium">YardLens</span>}>
        <SectionTitle />
      </Suspense>
      <div className="ml-auto flex items-center gap-1">
        <LanguageSwitcher />
        <ThemeToggle />
      </div>
    </header>
  );
}
