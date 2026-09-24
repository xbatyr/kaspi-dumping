"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, CheckCircle2, Loader2, Play, Send, Shield, Store } from "lucide-react";

import { saveSettings } from "@/lib/client";
import type { Settings } from "@/lib/types";

/** Everything the merchant fills in once, without touching a config file. */
export function SettingsForm({ settings }: { settings: Settings }) {
  const router = useRouter();
  const [merchantId, setMerchantId] = useState(settings.merchant_id);
  const [company, setCompany] = useState(settings.company);
  const [rating, setRating] = useState(
    settings.merchant_rating === null ? "" : String(settings.merchant_rating),
  );
  const [proxies, setProxies] = useState(settings.proxies.join("\n"));
  const [enabled, setEnabled] = useState(settings.worker_enabled);
  const [interval, setInterval] = useState(String(settings.interval_seconds));
  const [requestInterval, setRequestInterval] = useState(String(settings.request_interval));
  const [chats, setChats] = useState(settings.telegram_chat_ids.join(", "));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function save() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await saveSettings({
        merchant_id: merchantId.trim(),
        company: company.trim(),
        merchant_rating: rating.trim() ? Number(rating.replace(",", ".")) : null,
        proxies: proxies
          .split(/[\n,]/)
          .map((line) => line.trim())
          .filter(Boolean),
        worker_enabled: enabled,
        interval_seconds: Number(interval) || 300,
        request_interval: Number(requestInterval) || 2,
        telegram_chat_ids: chats
          .split(/[\n,]/)
          .map((line) => line.trim())
          .filter(Boolean),
      });
      setSaved(true);
      router.refresh();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Не удалось сохранить");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4">
      <Card icon={Store} title="Магазин" hint="Без этих двух полей бот не запустится.">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field
            label="ID магазина на Kaspi"
            value={merchantId}
            onChange={setMerchantId}
            placeholder="30123456"
            hint="Ваш merchantId: по нему бот отличает ваше предложение от чужих."
          />
          <Field
            label="Название компании"
            value={company}
            onChange={setCompany}
            placeholder='ТОО "Ромашка"'
            hint="Как в кабинете продавца: попадёт в прайс-лист."
          />
        </div>
        <Field
          label="Рейтинг магазина (необязательно)"
          value={rating}
          onChange={setRating}
          placeholder="4.8"
          hint="Нужен стратегии «цена первого места», когда вашего предложения нет на карточке."
        />
      </Card>

      <Card
        icon={Play}
        title="Работа бота"
        hint="Выключатель действует со следующего цикла — перезапускать ничего не нужно."
      >
        <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-slate-200 p-3">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
            className="size-4 cursor-pointer accent-slate-900"
          />
          <span>
            <span className="block text-sm font-medium text-slate-900">Бот включён</span>
            <span className="block text-xs text-slate-500">
              Пока выключен, цены не меняются вообще.
            </span>
          </span>
        </label>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field
            label="Пауза между циклами, сек"
            value={interval}
            onChange={setInterval}
            placeholder="300"
            hint="Kaspi забирает прайс примерно раз в час, чаще 5 минут смысла мало."
          />
          <Field
            label="Пауза между запросами, сек"
            value={requestInterval}
            onChange={setRequestInterval}
            placeholder="2"
            hint="Чем меньше, тем выше риск, что Kaspi ограничит ваш IP."
          />
        </div>
      </Card>

      <Card
        icon={Shield}
        title="Прокси"
        hint="Казахстанские прокси, по одному в строке. Без них Kaspi заблокирует IP сервера."
      >
        <textarea
          value={proxies}
          onChange={(event) => setProxies(event.target.value)}
          rows={3}
          spellCheck={false}
          placeholder="http://user:password@1.2.3.4:8080"
          className="w-full rounded-lg border border-slate-300 p-3 font-mono text-xs outline-none focus:border-slate-900"
        />
      </Card>

      <Card icon={Send} title="Telegram" hint="Изменения цен получают все, кто отправил боту /start.">
        <p className="text-sm text-slate-700">Бот: <a href="https://t.me/repricerkaspibot" target="_blank" rel="noreferrer" className="font-medium underline">@repricerkaspibot</a>. Токен задаётся при установке и здесь не меняется.</p>
        <Field
          label="Chat ID владельцев через запятую"
          value={chats}
          onChange={setChats}
          placeholder="123456789"
          hint="Только эти чаты могут останавливать демпинг и менять цены через команды. Подписка на уведомления доступна всем через /start."
        />
      </Card>

      {error && (
        <p className="flex items-start gap-2 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          {error}
        </p>
      )}
      {saved && (
        <p className="flex items-center gap-2 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          <CheckCircle2 className="size-4" />
          Сохранено. Бот подхватит настройки на следующем цикле.
        </p>
      )}

      <button
        type="button"
        onClick={save}
        disabled={saving}
        className="inline-flex cursor-pointer items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-60"
      >
        {saving && <Loader2 className="size-4 animate-spin" />}
        Сохранить настройки
      </button>
    </div>
  );
}

function Card({
  icon: Icon,
  title,
  hint,
  children,
}: {
  icon: typeof Store;
  title: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-4">
      <div>
        <h2 className="flex items-center gap-1.5 text-sm font-semibold text-slate-900">
          <Icon className="size-4 text-slate-400" />
          {title}
        </h2>
        <p className="mt-0.5 text-xs text-slate-500">{hint}</p>
      </div>
      {children}
    </section>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  hint,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  hint?: string;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-slate-600">{label}</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900"
      />
      {hint && <span className="mt-1 block text-xs text-slate-400">{hint}</span>}
    </label>
  );
}
