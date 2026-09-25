/** Mutations from the browser. These go to /api/* on this origin, where the
 *  route handler forwards them to FastAPI with the API key attached. */

import type {
  BulkRuleChanges,
  GlobalStrategyDraft,
  ImportResult,
  KaspiCard,
  ProductDraft,
  ProductManagement,
  SettingsDraft,
  XmlImportResult,
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

export async function manageProduct(sku: string, changes: ProductManagement): Promise<void> {
  await send(`/api/products/${encodeURIComponent(sku)}/management`, "PATCH", changes);
}

export async function updateRulesBulk(
  ruleIds: number[],
  changes: BulkRuleChanges,
): Promise<number> {
  const response = await send("/api/rules/bulk-update", "POST", {
    rule_ids: ruleIds,
    ...changes,
  });
  const result = (await response.json()) as { updated: number };
  return result.updated;
}

export async function saveGlobalStrategy(draft: GlobalStrategyDraft): Promise<void> {
  await send("/api/strategy", "PUT", draft);
}

export async function saveProductLimits(sku: string, minPrice: string, maxPrice: string, step: number): Promise<void> {
  await send(`/api/strategy/products/${encodeURIComponent(sku)}/limits`, "PUT", {
    min_price: minPrice,
    max_price: maxPrice,
    step,
  });
}


/** Add or update products. The endpoint upserts by SKU, so re-sending a
 *  corrected price list is safe. */
export async function importProducts(items: ProductDraft[]): Promise<ImportResult> {
  const response = await send("/api/products/import", "POST", { items });
  return (await response.json()) as ImportResult;
}

export async function importKaspiXml(
  xml: string, preview: boolean, inferCardIds: boolean,
): Promise<XmlImportResult> {
  const response = await fetch(
    `/api/products/import-xml?preview=${preview}&infer_card_ids=${inferCardIds}`, {
    method: "POST",
    headers: { "Content-Type": "application/xml; charset=utf-8" },
    body: xml,
    },
  );
  if (!response.ok) throw new Error(await errorMessage(response));
  return (await response.json()) as XmlImportResult;
}

export async function linkKaspiCard(sku: string, cardId: string): Promise<void> {
  const path = `/api/products/${encodeURIComponent(sku)}`;
  const response = await fetch(path);
  if (!response.ok) throw new Error(await errorMessage(response));
  const product = (await response.json()) as ProductDraft;
  await send(path, "PUT", { ...product, kaspi_product_id: cardId, rules: [] });
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
