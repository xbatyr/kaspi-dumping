import type { City, ProductDraft, RuleDraftInline, Strategy } from "@/lib/types";

export const CSV_TEMPLATE = `sku;название;id_kaspi;бренд;склад;остаток;город;мин_цена;макс_цена;стратегия
IPH13-128;Apple iPhone 13 128Gb;102298404;Apple;PP1;4;Алматы;330000;420000;стать первым
IPH13-128;Apple iPhone 13 128Gb;102298404;Apple;PP1;4;Астана;335000;430000;цена первого места
CASE-13;Чехол для iPhone 13;112233445;Deppa;PP1;40;Алматы;2000;9000;прижиматься`;

/** Column names the merchant may use, Russian first. */
const ALIASES: Record<string, string[]> = {
  sku: ["sku", "артикул", "код"],
  title: ["название", "наименование", "title", "товар"],
  kaspi_product_id: ["id_kaspi", "kaspi_product_id", "id карточки", "карточка", "kaspi id"],
  brand: ["бренд", "brand", "производитель"],
  base_price: ["базовая_цена", "base_price", "цена"],
  store_id: ["склад", "store_id", "точка", "пвз"],
  stock: ["остаток", "stock", "количество", "кол-во"],
  city: ["город", "city", "city_id"],
  min_price: ["мин_цена", "min_price", "минимальная цена", "мин"],
  max_price: ["макс_цена", "max_price", "максимальная цена", "макс"],
  strategy: ["стратегия", "strategy"],
  step: ["шаг", "step"],
  target_position: ["позиция", "target_position", "место"],
};

const STRATEGY_ALIASES: [Strategy, string[]][] = [
  ["beat_first", ["beat_first", "стать первым", "демпинг", "первый"]],
  ["match_first", ["match_first", "цена первого", "паритет", "цена первого места"]],
  ["follow_second", ["follow_second", "прижиматься", "второе место", "второй"]],
  ["target_position", ["target_position", "борьба", "держать место", "позиция"]],
  ["fixed_price", ["fixed_price", "фиксированная", "фикс"]],
  ["manual", ["manual", "вручную", "ручная"]],
];

export interface ParsedCatalog {
  items: ProductDraft[];
  errors: string[];
}

/**
 * Reads the price list the merchant pasted in.
 *
 * One row is one product in one city: repeat the SKU to set up several cities or
 * several pickup points. Columns can be named in Russian, separated by comma,
 * semicolon or tab, because that is what comes out of Excel.
 */
export function parseCatalogCsv(text: string, cities: City[]): ParsedCatalog {
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length < 2) {
    return { items: [], errors: ["Нужна строка заголовков и хотя бы одна строка товара"] };
  }

  const separator = pickSeparator(lines[0]);
  const columns = mapColumns(splitRow(lines[0], separator));
  const missing = ["sku", "title", "kaspi_product_id"].filter((name) => !(name in columns));
  if (missing.length > 0) {
    return {
      items: [],
      errors: [`Не нашёл колонки: ${missing.map(humanColumn).join(", ")}. Первая строка — заголовки.`],
    };
  }

  const errors: string[] = [];
  const bySku = new Map<string, ProductDraft>();
  const cityByName = new Map(cities.map((city) => [city.name.toLowerCase(), city.id]));

  lines.slice(1).forEach((line, index) => {
    const cells = splitRow(line, separator);
    const value = (name: string) => {
      const at = columns[name];
      return at === undefined ? "" : (cells[at] ?? "").trim();
    };
    const row = index + 2;
    const sku = value("sku");
    const kaspiId = value("kaspi_product_id").replace(/\D/g, "");

    if (!sku) {
      errors.push(`строка ${row}: пустой SKU`);
      return;
    }
    if (!kaspiId) {
      errors.push(`строка ${row}: не разобрал ID карточки Kaspi (нужны цифры из ссылки)`);
      return;
    }

    const product =
      bySku.get(sku) ??
      ({
        sku,
        title: value("title") || sku,
        kaspi_product_id: kaspiId,
        brand: value("brand") || null,
        base_price: number(value("base_price")),
        is_active: true,
        availabilities: [],
        rules: [],
      } satisfies ProductDraft);
    bySku.set(sku, product);

    const store = value("store_id");
    if (store && !product.availabilities.some((item) => item.store_id === store)) {
      product.availabilities.push({
        store_id: store,
        available: true,
        stock_count: value("stock") ? Number(value("stock").replace(/\s/g, "")) || 0 : null,
      });
    }

    const cityCell = value("city");
    if (cityCell) {
      const cityId = /^\d+$/.test(cityCell)
        ? cityCell
        : cityByName.get(cityCell.toLowerCase());
      if (!cityId) {
        errors.push(`строка ${row}: неизвестный город «${cityCell}»`);
        return;
      }
      const min = number(value("min_price"));
      const max = number(value("max_price"));
      if (!min || !max) {
        errors.push(`строка ${row}: для города нужны мин. и макс. цена`);
        return;
      }
      if (Number(min) > Number(max)) {
        errors.push(`строка ${row}: мин. цена больше макс.`);
        return;
      }
      const strategy = parseStrategy(value("strategy"));
      if (!strategy) {
        errors.push(`строка ${row}: непонятная стратегия «${value("strategy")}»`);
        return;
      }
      const rule: RuleDraftInline = {
        city_id: cityId,
        strategy,
        min_price: min,
        max_price: max,
        step: value("step") ? Number(value("step")) || 1 : 1,
        target_position:
          strategy === "target_position"
            ? Number(value("target_position")) || 2
            : null,
      };
      const existing = product.rules.findIndex((item) => item.city_id === cityId);
      if (existing >= 0) product.rules[existing] = rule;
      else product.rules.push(rule);
    }
  });

  return { items: [...bySku.values()], errors };
}

function parseStrategy(raw: string): Strategy | null {
  const value = raw.trim().toLowerCase();
  if (!value) return "beat_first";
  const found = STRATEGY_ALIASES.find(([, aliases]) =>
    aliases.some((alias) => value.startsWith(alias) || alias.startsWith(value)),
  );
  return found ? found[0] : null;
}

function mapColumns(headers: string[]): Record<string, number> {
  const columns: Record<string, number> = {};
  headers.forEach((header, index) => {
    const name = header.trim().toLowerCase();
    for (const [field, aliases] of Object.entries(ALIASES)) {
      if (aliases.includes(name) && !(field in columns)) columns[field] = index;
    }
  });
  return columns;
}

function humanColumn(field: string): string {
  return ALIASES[field]?.[1] ?? field;
}

function number(raw: string): string | null {
  const cleaned = raw.replace(/[^\d.,]/g, "").replace(",", ".");
  return cleaned ? String(Math.round(Number(cleaned))) : null;
}

function pickSeparator(header: string): string {
  for (const candidate of ["\t", ";", ","]) {
    if (header.includes(candidate)) return candidate;
  }
  return ",";
}

function splitRow(line: string, separator: string): string[] {
  const cells: string[] = [];
  let current = "";
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (char === '"') {
      if (quoted && line[index + 1] === '"') {
        current += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (char === separator && !quoted) {
      cells.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  cells.push(current);
  return cells.map((cell) => cell.trim());
}
