"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { CamerasSettings } from "@/components/settings/cameras-settings";
import { DetectionSettings } from "@/components/settings/detection-settings";
import { IntegrationsSettings } from "@/components/settings/integrations-settings";
import { LoadSettings } from "@/components/settings/load-settings";
import { SystemSettings } from "@/components/settings/system-settings";
import { WorkersSettings } from "@/components/settings/workers-settings";
import { PageHeader } from "@/components/shared/page-header";
import { ErrorState } from "@/components/shared/states";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useI18n } from "@/i18n/provider";
import { useSettings } from "@/lib/api/queries";

const TABS = ["cameras", "detection", "integrations", "load", "workers", "system"] as const;

type Tab = (typeof TABS)[number];

function isTab(value: string | null): value is Tab {
  return TABS.some((tab) => tab === value);
}

function SettingsFormSkeleton() {
  return (
    <Card>
      <CardHeader className="gap-2">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-4 w-72" />
      </CardHeader>
      <CardContent className="space-y-6">
        {Array.from({ length: 3 }, (_, index) => (
          <div key={index} className="space-y-2">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-9 w-72" />
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

export function SettingsView() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const requested = searchParams.get("tab");
  const tab: Tab = isTab(requested) ? requested : "cameras";
  const settings = useSettings();
  const { t } = useI18n();

  const settingsPanel = (render: (data: NonNullable<typeof settings.data>) => React.ReactNode) =>
    settings.isPending ? (
      <SettingsFormSkeleton />
    ) : settings.isError ? (
      <ErrorState title={t.errors.loadSettings} error={settings.error} onRetry={() => void settings.refetch()} />
    ) : (
      render(settings.data)
    );

  return (
    <>
      <PageHeader title={t.nav.settings} description={t.settings.description} />
      <Tabs
        value={tab}
        onValueChange={(value) => router.replace(`${pathname}?tab=${value}`, { scroll: false })}
        className="gap-6"
      >
        <TabsList className="w-full justify-start overflow-x-auto overflow-y-hidden sm:w-fit">
          {TABS.map((item) => (
            <TabsTrigger key={item} value={item}>
              {t.settings.tabs[item]}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="cameras">
          <CamerasSettings />
        </TabsContent>
        <TabsContent value="detection">{settingsPanel((data) => <DetectionSettings settings={data} />)}</TabsContent>
        <TabsContent value="integrations">
          {settingsPanel((data) => <IntegrationsSettings settings={data} />)}
        </TabsContent>
        <TabsContent value="load">{settingsPanel((data) => <LoadSettings settings={data} />)}</TabsContent>
        <TabsContent value="workers">
          <WorkersSettings />
        </TabsContent>
        <TabsContent value="system">
          <SystemSettings />
        </TabsContent>
      </Tabs>
    </>
  );
}
