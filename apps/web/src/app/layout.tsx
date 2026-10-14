import type { Metadata, Viewport } from "next";
import { Geist_Mono, Jost } from "next/font/google";
import { Suspense, type ReactNode } from "react";

import { AppHeader } from "@/components/layout/app-header";
import { AppSidebar } from "@/components/layout/app-sidebar";
import { BackendBanner } from "@/components/layout/backend-banner";
import { Providers } from "@/components/providers";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { DEFAULT_LOCALE } from "@/i18n/config";
import { I18nProvider } from "@/i18n/provider";
import { getLocale, getMessages } from "@/i18n/server";

import "./globals.css";

const jost = Jost({
  variable: "--font-sans",
  subsets: ["latin", "cyrillic"],
  display: "swap",
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
  display: "swap",
});

export async function generateMetadata(): Promise<Metadata> {
  const t = await getMessages();
  return {
    title: { default: "YardLens", template: "%s · YardLens" },
    description: t.meta.description,
  };
}

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#0a0a0a" },
  ],
};

/** Everything below depends on the language cookie, so it renders per request. */
async function LocalizedApp({ children }: { children: ReactNode }) {
  const locale = await getLocale();
  return (
    <I18nProvider locale={locale}>
      <Providers>
        <SidebarProvider>
          <AppSidebar />
          <SidebarInset className="min-w-0">
            <AppHeader />
            <BackendBanner />
            <main className="flex flex-1 flex-col gap-6 p-4 lg:p-6">{children}</main>
          </SidebarInset>
        </SidebarProvider>
      </Providers>
    </I18nProvider>
  );
}

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang={DEFAULT_LOCALE} className={`${jost.variable} ${geistMono.variable} antialiased`} suppressHydrationWarning>
      <body>
        <Suspense>
          <LocalizedApp>{children}</LocalizedApp>
        </Suspense>
      </body>
    </html>
  );
}
