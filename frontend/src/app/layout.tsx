import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import { Bot, Settings } from "lucide-react";

import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin", "cyrillic"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Kaspi Repricer",
  description: "Управление ценами и стратегиями репрайсера на Kaspi.kz",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body className={`${geistSans.variable} ${geistMono.variable} font-sans antialiased`}>
        <header className="border-b border-slate-200 bg-white">
          <div className="mx-auto flex max-w-7xl items-center gap-3 px-4 py-4 sm:px-6">
            <span className="flex size-9 items-center justify-center rounded-lg bg-slate-900 text-white">
              <Bot className="size-5" />
            </span>
            <div className="flex-1">
              <h1 className="text-base font-semibold text-slate-900">Kaspi Repricer</h1>
              <p className="text-xs text-slate-500">Товары, стратегии и цены</p>
            </div>
            <Link
              href="/settings"
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              <Settings className="size-4" />
              Настройки
            </Link>
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">{children}</main>
      </body>
    </html>
  );
}
