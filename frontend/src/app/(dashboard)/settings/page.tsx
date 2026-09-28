import Link from "next/link";
import { AlertTriangle, ArrowLeft } from "lucide-react";

import { SettingsForm } from "@/components/SettingsForm";
import { ApiError, fetchSettings } from "@/lib/api";
import type { Settings } from "@/lib/types";

type Loaded = { ok: true; settings: Settings } | { ok: false; message: string };

async function load(): Promise<Loaded> {
  try {
    return { ok: true, settings: await fetchSettings() };
  } catch (error) {
    return {
      ok: false,
      message: error instanceof ApiError ? error.message : "Не удалось загрузить настройки",
    };
  }
}

export default async function SettingsPage() {
  const result = await load();

  return (
    <div className="mx-auto max-w-2xl">
      <Link
        href="/"
        className="mb-4 inline-flex items-center gap-1.5 text-sm text-slate-600 hover:text-slate-900"
      >
        <ArrowLeft className="size-4" />
        К товарам
      </Link>

      {result.ok ? (
        <SettingsForm settings={result.settings} />
      ) : (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-6">
          <p className="flex items-center gap-2 font-medium text-rose-800">
            <AlertTriangle className="size-5" />
            {result.message}
          </p>
        </div>
      )}
    </div>
  );
}
