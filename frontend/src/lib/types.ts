/** Mirrors the Pydantic schemas in repricer/api/schemas.py. */

export type Strategy =
  | "beat_first"
  | "match_first"
  | "follow_second"
  | "target_position"
  | "fixed_price"
  | "manual";

/** Prices arrive as strings: the backend normalises Decimals so "362000" never
 *  turns into "362000.00" between two calls. */
export type Money = string;

export interface RuleStatus {
  computed_price: Money;
  expected_position: number;
  competitor_top1_price: Money | null;
  competitor_top1_merchant_id: string | null;
  strategy_used: Strategy;
  reason: string;
  changed_at: string;
}

export interface Rule {
  id: number;
  city_id: string;
  strategy: Strategy;
  min_price: Money;
  max_price: Money;
  /** Set when the limit was entered as a share of the product's own price;
   *  the tenge figures above then follow that price. */
  min_percent: string | null;
  max_percent: string | null;
  step: number;
  target_position: number | null;
  ignored_merchants: string[];
  is_active: boolean;
  current_price: Money | null;
  last_evaluated_at: string | null;
  market_snapshot?: { position: number | null; offer_count: number; observed_price: Money | null; leader_price: Money; leader_name?: string; leader_merchant_id?: string; expected_position: number } | null;
  last_change: RuleStatus | null;
}

export interface Margin {
  price: Money;
  commission: Money;
  tax: Money;
  delivery: Money;
  purchase_price: Money;
  profit: Money;
  margin_percent: string;
  markup_percent: string | null;
  break_even_price: Money | null;
  /** No purchase price is known, so the profit shown is the optimistic one. */
  estimated: boolean;
}

export interface ProductMargins {
  current: Margin | null;
  minimum: Margin | null;
  maximum: Margin | null;
}

export interface ProductRules {
  sku: string;
  title: string;
  kaspi_product_id: string;
  image_url: string | null;
  brand: string | null;
  base_price: Money | null;
  is_active: boolean;
  /** Switched on and in stock somewhere: what the "на продаже" filter means. */
  on_sale: boolean;
  purchase_price: Money | null;
  category: string | null;
  commission_percent: string | null;
  delivery_cost: Money | null;
  auto_decrease: boolean;
  auto_increase: boolean;
  availabilities: AvailabilityDraft[];
  rules: Rule[];
  margins: ProductMargins;
}

export interface Category {
  name: string | null;
  products: number;
}

/** The filters above the catalogue, kept in the page URL. */
export interface CatalogFilters {
  q: string;
  bot: string;
  sale: string;
  category: string;
  sort: string;
}

export interface BulkTools {
  skus?: string[];
  set_min_percent?: string;
  set_max_percent?: string;
  raise_to_max?: boolean;
  disable_decrease_when_off_sale?: boolean;
}

export interface BulkToolsResult {
  products_seen: number;
  limits_set: number;
  prices_raised: number;
  decrease_disabled: number;
  skipped: Record<string, number>;
}

export interface RuleList {
  items: ProductRules[];
  total: number;
  limit: number;
  offset: number;
}

export interface City {
  id: string;
  name: string;
}

export interface GlobalStrategy {
  strategy: Strategy | null;
  step: number;
  target_position: number | null;
  ignored_merchants: string[];
  city_ids: string[];
  configured_products: number;
}

export interface GlobalStrategyDraft {
  strategy: Strategy;
  step: number;
  target_position: number | null;
  ignored_merchants: string[];
  city_ids: string[];
}

export interface BulkRuleChanges {
  strategy?: Strategy;
  min_price?: string;
  max_price?: string;
  step?: number;
  target_position?: number;
  is_active?: boolean;
}

export interface AvailabilityDraft {
  store_id: string;
  available: boolean;
  stock_count: number | null;
  preorder_days?: number | null;
}

export interface ProductManagement {
  purchase_price?: Money | null;
  category?: string | null;
  commission_percent?: string | null;
  delivery_cost?: Money | null;
  auto_decrease?: boolean;
  auto_increase?: boolean;
  is_active?: boolean;
  availabilities?: AvailabilityDraft[];
}

/** One side may be given in tenge or in percent, never both. */
export interface PriceLimitsDraft {
  min_price?: string;
  max_price?: string;
  min_percent?: string;
  max_percent?: string;
  step?: number;
}

export interface RuleDraftInline {
  city_id: string;
  strategy: Strategy;
  min_price: string;
  max_price: string;
  step?: number;
  target_position?: number | null;
}

/** What the "add products" dialog sends: one product with its pickup points and,
 *  optionally, ready-made settings per city. */
export interface ProductDraft {
  sku: string;
  title: string;
  kaspi_product_id: string;
  brand: string | null;
  base_price: string | null;
  is_active: boolean;
  availabilities: AvailabilityDraft[];
  rules: RuleDraftInline[];
}

export interface ImportResult {
  created: number;
  updated: number;
  errors: { sku: string; reason: string }[];
}

export interface XmlImportResult {
  total: number;
  created: number;
  updated: number;
  unlinked: number;
  inferred_cards: number;
  preview: boolean;
}

export interface Status {
  products_total: number;
  products_ready: number;
  blockers: Record<string, number>;
  rules_active: number;
  rules_paused: number;
  first_place: number;
  last_run_at: string | null;
  changes_today: number;
  feed_ready: boolean;
  worker_enabled: boolean;
  global_strategy_configured: boolean;
}

export interface HistoryEntry {
  id: number;
  product_sku: string;
  city_id: string;
  old_price: Money | null;
  new_price: Money;
  competitor_top1_price: Money | null;
  competitor_top1_merchant_id: string | null;
  strategy_used: Strategy;
  reason: string;
  expected_position: number;
  competitor_count: number;
  created_at: string;
}

export interface HistoryPage {
  sku: string;
  items: HistoryEntry[];
  total: number;
}

export interface Settings {
  merchant_id: string;
  company: string;
  merchant_rating: number | null;
  proxies: string[];
  worker_enabled: boolean;
  interval_seconds: number;
  request_interval: number;
  telegram_token_hint: string;
  telegram_configured: boolean;
  telegram_chat_ids: string[];
  is_ready: boolean;
  tax_percent: string;
  commission_percent: string;
  delivery_cost: Money;
}

export interface SettingsDraft {
  merchant_id: string;
  company: string;
  merchant_rating: number | null;
  proxies: string[];
  worker_enabled: boolean;
  interval_seconds: number;
  request_interval: number;
  telegram_chat_ids: string[];
  tax_percent: string;
  commission_percent: string;
  delivery_cost: string;
}

export interface KaspiCard {
  kaspi_product_id: string;
  title: string;
  brand: string | null;
  price: Money | null;
  rating: number | null;
  reviews_count: number;
  link: string;
  image_url: string | null;
}
