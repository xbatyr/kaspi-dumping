import Link from "next/link";
import { Bot, LogOut, Settings, SlidersHorizontal, Package } from "lucide-react";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return <>
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-7xl items-center gap-3 px-4 py-3 sm:px-6 md:py-4">
        <span className="flex size-9 items-center justify-center rounded-lg bg-emerald-700 text-white"><Bot className="size-5" /></span>
        <div className="flex-1">
          <p className="text-base font-semibold text-emerald-900">Kaspi Repricer</p>
          <p className="text-xs text-slate-500">Товары, стратегии и цены</p>
        </div>
        <nav aria-label="Основные разделы" className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-3 border-t border-slate-200 bg-white px-2 pt-1 pb-[max(0.25rem,env(safe-area-inset-bottom))] shadow-[0_-4px_18px_-12px_rgba(15,23,42,0.25)] md:static md:flex md:flex-wrap md:items-center md:gap-1 md:border-0 md:bg-transparent md:p-0 md:shadow-none">
          <Link href="/" className="inline-flex min-h-14 flex-col items-center justify-center gap-0.5 rounded-lg px-1 text-xs font-medium text-slate-700 active:bg-slate-100 md:min-h-0 md:flex-row md:gap-1 md:px-3 md:py-2 md:text-sm md:hover:bg-slate-100"><Package className="size-5 md:size-4" />Товары</Link>
          <Link href="/strategies" className="inline-flex min-h-14 flex-col items-center justify-center gap-0.5 rounded-lg px-1 text-xs font-medium text-slate-700 active:bg-slate-100 md:min-h-0 md:flex-row md:gap-1 md:px-3 md:py-2 md:text-sm md:hover:bg-slate-100"><SlidersHorizontal className="size-5 md:size-4" />Стратегии</Link>
          <Link href="/settings" className="inline-flex min-h-14 flex-col items-center justify-center gap-0.5 rounded-lg px-1 text-xs font-medium text-slate-700 active:bg-slate-100 md:min-h-0 md:flex-row md:gap-1 md:px-3 md:py-2 md:text-sm md:hover:bg-slate-100"><Settings className="size-5 md:size-4" />Настройки</Link>
        </nav>
        <form action="/auth/logout" method="post"><button title="Выйти" aria-label="Выйти из панели" className="inline-flex min-h-10 items-center gap-1 rounded-lg border border-slate-200 px-3 text-sm text-slate-600 hover:bg-slate-50"><LogOut className="size-4" /><span className="hidden sm:inline">Выйти</span></button></form>
      </div>
    </header>
    <main className="mx-auto max-w-7xl px-4 pt-4 pb-24 sm:px-6 md:py-6">{children}</main>
  </>;
}
