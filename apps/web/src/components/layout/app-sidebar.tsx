"use client";

import { Activity, Cctv, LayoutDashboard, ScanEye, Settings, Truck } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense } from "react";

import { useLiveEvents } from "@/components/shared/live-events-provider";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";
import { useI18n } from "@/i18n/provider";
import { useHealth } from "@/lib/api/queries";
import { cn } from "@/lib/utils";

export const NAV_ITEMS = [
  { href: "/", key: "overview", icon: LayoutDashboard },
  { href: "/loading", key: "loading", icon: Truck },
  { href: "/cameras", key: "cameras", icon: Cctv },
  { href: "/events", key: "events", icon: Activity },
  { href: "/settings", key: "settings", icon: Settings },
] as const;

export function isActivePath(pathname: string, href: string) {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

function NavItems({ pathname }: { pathname: string | null }) {
  const { setOpenMobile } = useSidebar();
  const { t } = useI18n();
  return (
    <SidebarMenu>
      {NAV_ITEMS.map((item) => {
        const active = pathname !== null && isActivePath(pathname, item.href);
        const label = t.nav[item.key];
        return (
          <SidebarMenuItem key={item.href}>
            <SidebarMenuButton asChild isActive={active} tooltip={label}>
              <Link href={item.href} aria-current={active ? "page" : undefined} onClick={() => setOpenMobile(false)}>
                <item.icon aria-hidden />
                <span>{label}</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        );
      })}
    </SidebarMenu>
  );
}

function ActiveNavItems() {
  return <NavItems pathname={usePathname()} />;
}

export function AppSidebar() {
  const { setOpenMobile } = useSidebar();
  const { t } = useI18n();

  return (
    <Sidebar collapsible="icon" variant="inset">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" asChild tooltip="YardLens">
              <Link href="/" onClick={() => setOpenMobile(false)}>
                <span className="bg-primary text-primary-foreground flex size-8 shrink-0 items-center justify-center rounded-lg">
                  <ScanEye className="size-4" aria-hidden />
                </span>
                <span className="grid leading-tight">
                  <span className="font-semibold tracking-tight">YardLens</span>
                  <span className="text-muted-foreground text-xs">{t.common.tagline}</span>
                </span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            {/* The active item depends on the URL; the fallback renders the same menu without it. */}
            <Suspense fallback={<NavItems pathname={null} />}>
              <ActiveNavItems />
            </Suspense>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <ConnectionIndicator />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}

function ConnectionIndicator() {
  const health = useHealth();
  const { connected } = useLiveEvents();
  const { t } = useI18n();
  const apiUp = health.isSuccess;
  const label = !apiUp ? t.connection.apiOffline : connected ? t.connection.live : t.connection.connecting;
  const detail = health.data ? `v${health.data.version}` : null;

  return (
    <div
      className="text-muted-foreground flex items-center gap-2 px-2 py-1.5 text-xs group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0"
      role="status"
      aria-live="polite"
    >
      <span
        className={cn(
          "size-2 shrink-0 rounded-full",
          apiUp && connected ? "bg-success" : apiUp ? "bg-warning" : "bg-destructive",
        )}
        aria-hidden
      />
      <span className="truncate group-data-[collapsible=icon]:sr-only">
        {label}
        {detail ? <span className="text-muted-foreground/70"> · {detail}</span> : null}
      </span>
    </div>
  );
}
