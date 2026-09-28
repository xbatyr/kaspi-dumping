import { Bot, LockKeyhole } from "lucide-react";

import { safeDestination } from "@/lib/session";

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ error?: string; next?: string }> }) {
  const { error, next } = await searchParams;
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-10">
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
        <div className="flex size-12 items-center justify-center rounded-xl bg-emerald-700 text-white"><Bot className="size-6" /></div>
        <h1 className="mt-6 text-2xl font-semibold tracking-tight text-slate-900">Вход в Kaspi Repricer</h1>
        <p className="mt-2 text-sm leading-6 text-slate-500">Управление товарами, стратегиями и ценами вашего магазина.</p>
        {error === "1" && <p role="alert" className="mt-5 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">Неверный логин или пароль. Проверьте данные и попробуйте снова.</p>}
        <form action="/auth/login" method="post" className="mt-6 space-y-5">
          <input type="hidden" name="next" value={safeDestination(next ?? null)} />
          <div>
            <label htmlFor="username" className="block text-sm font-medium text-slate-700">Логин</label>
            <input id="username" name="username" autoComplete="username" required autoFocus className="mt-2 block min-h-12 w-full rounded-lg border border-slate-300 bg-white px-3 text-base outline-none focus:border-emerald-600" />
          </div>
          <div>
            <label htmlFor="password" className="block text-sm font-medium text-slate-700">Пароль</label>
            <input id="password" name="password" type="password" autoComplete="current-password" required className="mt-2 block min-h-12 w-full rounded-lg border border-slate-300 bg-white px-3 text-base outline-none focus:border-emerald-600" />
          </div>
          <button type="submit" className="flex min-h-12 w-full items-center justify-center gap-2 rounded-lg bg-emerald-700 px-4 font-medium text-white hover:bg-emerald-800"><LockKeyhole className="size-4" />Войти</button>
        </form>
        <p className="mt-6 text-center text-xs text-slate-400">Доступ только для владельца магазина</p>
      </div>
    </main>
  );
}
