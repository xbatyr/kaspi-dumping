"use client";

import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Copy,
  Link2,
  Loader2,
  Trophy,
} from "lucide-react";

import { relativeTime } from "@/lib/format";
import type { Status } from "@/lib/types";

/** The first thing the merchant sees: is the shop ready, and what is the bot doing. */
export function StatusPanel({ status, feedUrl }: { status: Status; feedUrl: string }) {
  const [copied, setCopied] = useState(false);
  const blockers = Object.entries(status.blockers);
  const localFeed = /^https?:\/\/(?:localhost|127\.0\.0\.1)(?::|\/|$)/i.test(feedUrl);

  async function copyFeed() {
    try {
      await navigator.clipboard.writeText(feedUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  return (
    <section className="mb-5 grid gap-3 lg:grid-cols-3">
      <div className="rounded-xl border border-slate-200 bg-white p-4 lg:col-span-2">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Metric
            label="Товаров в прайсе"
            value={`${status.products_ready} из ${status.products_total}`}
            tone={status.products_ready > 0 ? "good" : "warn"}
            icon={CheckCircle2}
          />
          <Metric
            label="На первом месте"
            value={`${status.first_place}`}
            hint={`из ${status.rules_active} активных`}
            tone={status.first_place > 0 ? "good" : "plain"}
            icon={Trophy}
          />
          <Metric
            label="Смен цен сегодня"
            value={`${status.changes_today}`}
            tone="plain"
          />
          <Metric
            label="Бот работал"
            value={relativeTime(status.last_run_at)}
            tone={status.last_run_at ? "plain" : "warn"}
            icon={Clock}
          />
        </div>

        {blockers.length > 0 && (
          <div className="mt-4 rounded-lg bg-amber-50 p-3">
            <p className="flex items-center gap-1.5 text-sm font-medium text-amber-900">
              <AlertTriangle className="size-4" />
              Прайс временно недоступен из-за этих товаров
            </p>
            <ul className="mt-1.5 space-y-0.5 text-sm text-amber-800">
              {blockers.map(([reason, count]) => (
                <li key={reason}>
                  {reason} — <b>{count}</b>
                </li>
              ))}
            </ul>
          </div>
        )}

        {!status.worker_enabled && <p className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">Бот выключен в настройках магазина. Цены сейчас не пересчитываются.</p>}
        {!status.global_strategy_configured && <p className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">Общая стратегия не сохранена. Настройте её во вкладке «Стратегии».</p>}
        {status.global_strategy_configured && status.rules_active === 0 && <p className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">Нет товаров с включённым демпингом. Задайте Min/Max для товаров во вкладке «Стратегии».</p>}

        {status.rules_paused > 0 && (
          <p className="mt-3 text-sm text-slate-500">
            На паузе правил: <b>{status.rules_paused}</b> — бот их не трогает.
          </p>
        )}
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <p className="flex items-center gap-1.5 text-sm font-medium text-slate-900">
          <Link2 className="size-4 text-slate-400" />
          Ссылка для кабинета Kaspi
        </p>
        <p className="mt-1 text-xs text-slate-500">
          Кабинет продавца → Товары → Загрузка прайс-листа → Автоматическая загрузка.
          Kaspi забирает её примерно раз в час.
        </p>
        {localFeed && <p className="mt-2 text-xs text-amber-700">Это локальный адрес для проверки. Для Kaspi нужен доступный из интернета HTTPS-адрес.</p>}
        <div className="mt-2 flex items-center gap-2">
          <code className="flex-1 truncate rounded-lg bg-slate-50 px-2 py-1.5 text-xs text-slate-700">
            {feedUrl}
          </code>
          <button
            type="button"
            onClick={copyFeed}
            className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-slate-200 px-2 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
          >
            {copied ? <CheckCircle2 className="size-3.5 text-emerald-600" /> : <Copy className="size-3.5" />}
            {copied ? "Скопировано" : "Копировать"}
          </button>
        </div>
        {!status.feed_ready && (
          <p className="mt-2 flex items-start gap-1.5 text-xs text-amber-700">
            <Loader2 className="mt-0.5 size-3.5 shrink-0" />
            Проверьте товары без склада или цены: пока есть неполные позиции,
            ссылка не отдаёт прайс.
          </p>
        )}
      </div>
    </section>
  );
}

function Metric({
  label,
  value,
  hint,
  tone,
  icon: Icon,
}: {
  label: string;
  value: string;
  hint?: string;
  tone: "good" | "warn" | "plain";
  icon?: typeof Trophy;
}) {
  const color =
    tone === "good" ? "text-emerald-700" : tone === "warn" ? "text-amber-700" : "text-slate-900";
  return (
    <div>
      <p className="text-xs text-slate-500">{label}</p>
      <p className={`mt-0.5 flex items-center gap-1 text-lg font-semibold ${color}`}>
        {Icon && <Icon className="size-4" />}
        {value}
      </p>
      {hint && <p className="text-xs text-slate-400">{hint}</p>}
    </div>
  );
}
