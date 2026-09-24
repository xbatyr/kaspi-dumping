/** Mutations from the browser. These go to /api/* on this origin, where the
 *  route handler forwards them to FastAPI with the API key attached. */

import type {
  ImportResult,
  KaspiCard,
  ProductDraft,
  Rule,
  RuleDraft,
  SettingsDraft,
} from "@/lib/types";

async function send(path: string, method: string, body: unknown): Promise<Response> {
  const response = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response;
}

/** FastAPI returns a string detail for our own errors and a list of field
 *  errors for schema violations; both should read as one line. */
async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          const field = Array.isArray(item?.loc) ? item.loc.at(-1) : undefined;
          return field ? `${field}: ${item.msg}` : item.msg;
        })
        .join("; ");
    }
  } catch {
    // fall through to the status line
  }
  return `Сервер ответил ${response.status}`;
}

export async function toggleRules(ruleIds: number[], isActive: boolean): Promise<void> {
  if (ruleIds.length === 0) return;
  await send("/api/rules/bulk-toggle", "POST", { is_active: isActive, rule_ids: ruleIds });
}

/**
 * Save one dialog's worth of settings across several cities.
 *
 * The API keeps one rule per city, so a multi-city dialog becomes one call per
 * city: create what is missing, update what exists. A city the user unchecked
 * keeps its rule but is switched off, which preserves its price history and
 * lets the same settings come back with one click.
 */
export async function saveRuleForCities(
  sku: string,
  draft: RuleDraft,
  existing: Rule[],
): Promise<void> {
  const byCity = new Map(existing.map((rule) => [rule.city_id, rule]));
  const settings = {
    strategy: draft.strategy,
    min_price: draft.min_price,
    max_price: draft.max_price,
    step: draft.step,
    target_position: draft.target_position,
    ignored_merchants: draft.ignored_merchants,
  };

  for (const cityId of draft.cityIds) {
    const rule = byCity.get(cityId);
    if (rule) {
      await send(`/api/rules/${rule.id}`, "PUT", { ...settings, is_active: true });
    } else {
      await send("/api/rules", "POST", {
        ...settings,
        product_sku: sku,
        city_id: cityId,
        is_active: true,
      });
    }
  }

  const dropped = existing.filter(
    (rule) => !draft.cityIds.includes(rule.city_id) && rule.is_active,
  );
  for (const rule of dropped) {
    await send(`/api/rules/${rule.id}`, "PUT", {
      strategy: rule.strategy,
      min_price: rule.min_price,
      max_price: rule.max_price,
      step: rule.step,
      target_position: rule.target_position,
      ignored_merchants: rule.ignored_merchants,
      is_active: false,
    });
  }
}


/** Add or update products. The endpoint upserts by SKU, so re-sending a
 *  corrected price list is safe. */
export async function importProducts(items: ProductDraft[]): Promise<ImportResult> {
  const response = await send("/api/products/import", "POST", { items });
  return (await response.json()) as ImportResult;
}


export async function saveSettings(draft: SettingsDraft): Promise<void> {
  await send("/api/settings", "PUT", draft);
}


/** Ask Kaspi for cards matching a name, so the owner never has to hunt for an ID. */
export async function searchKaspi(text: string, cityId: string): Promise<KaspiCard[]> {
  const query = new URLSearchParams({ text, city_id: cityId });
  const response = await fetch(`/api/kaspi/search?${query}`);
  if (!response.ok) throw new Error(await errorMessage(response));
  return (await response.json()) as KaspiCard[];
}
