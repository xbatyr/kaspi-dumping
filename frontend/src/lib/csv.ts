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
  const headers = splitRow(lines[0], separator);
  const columns = mapColumns(headers);
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
    const row = index + 2;
    // A row with more or fewer cells than the header has shifted: an unquoted
    // separator inside a title ("iPhone 13, 128Gb") moves every later value one
    // column along, and the stock or the price then reads the wrong cell. No
    // guessing which cell is which; the row is refused.
    if (cells.length !== headers.length) {
      errors.push(
        `строка ${row}: ${cells.length} колонок вместо ${headers.length} — значения съехали. ` +
          `Возьмите в кавычки текст, где есть «${separator === "\t" ? "табуляция" : separator}».`,
      );
      return;
    }
    const value = (name: string) => {
      const at = columns[name];
      return at === undefined ? "" : (cells[at] ?? "").trim();
    };
    // Every numeric cell is read strictly: digits, spaces and a currency sign at
    // most. "Apple iPhone 13" in a number column is an error, never 13.
    const fail = (message: string) => {
      errors.push(`строка ${row}: ${message}`);
    };
    const sku = value("sku");
    const title = value("title");
    const kaspiId = kaspiCardId(value("kaspi_product_id"));

    if (!sku) return fail("пустой SKU");
    if (kaspiId === null) {
      return fail(
        `ID карточки Kaspi «${value("kaspi_product_id")}» — нужны только цифры или ссылка на карточку`,
      );
    }
    // A title that is only a number is almost always a price or a stock that
    // slid into the wrong column.
    if (/^[\d\s.,₸]+$/.test(title)) return fail(`название «${title}» похоже на число`);

    const basePrice = money(value("base_price"));
    if (basePrice === INVALID) return fail(`цена «${value("base_price")}» — нужно целое число тенге`);
    const stock = wholeNumber(value("stock"), 0, 2_147_483_647);
    if (stock === INVALID) return fail(`остаток «${value("stock")}» — нужно целое число от 0`);
    const step = wholeNumber(value("step"), 1, 1_000_000);
    if (step === INVALID) return fail(`шаг «${value("step")}» — нужно целое число от 1`);
    const position = wholeNumber(value("target_position"), 1, 20);
    if (position === INVALID) return fail(`позиция «${value("target_position")}» — от 1 до 20`);

    const known = bySku.get(sku);
    // Repeating a SKU adds a city or a pickup point, not a different product: a
    // repeat with another card ID is a mix-up between two rows.
    if (known && known.kaspi_product_id !== kaspiId) {
      return fail(`SKU ${sku} уже был с карточкой ${known.kaspi_product_id}, здесь ${kaspiId}`);
    }

    const product =
      bySku.get(sku) ??
      ({
        sku,
        title: title || sku,
        kaspi_product_id: kaspiId,
        brand: value("brand") || null,
        base_price: basePrice,
        availabilities: [],
        rules: [],
      } satisfies ProductDraft);
    bySku.set(sku, product);

    const store = value("store_id");
    if (store && !product.availabilities.some((item) => item.store_id === store)) {
      product.availabilities.push({
        store_id: store,
        available: true,
        stock_count: stock,
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
      const min = money(value("min_price"));
      const max = money(value("max_price"));
      if (min === INVALID || max === INVALID) {
        return fail("мин. и макс. цена — целые числа тенге");
      }
      if (!min || !max) return fail("для города нужны мин. и макс. цена");
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
        step: step ?? 1,
        target_position: strategy === "target_position" ? (position ?? 2) : null,
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

/** Marks a cell that is filled in but is not the number it should be. */
const INVALID = Symbol("invalid");

/**
 * Whole tenge, as Excel writes it: "330000", "330 000", "330 000 ₸", "330000,00".
 * Kopecks other than zero, letters or a second number in the cell are refused.
 */
function money(raw: string): string | null | typeof INVALID {
  const cleaned = raw.replace(/[\s\u00a0\u202f]/g, "").replace(/(₸|тг\.?|kzt)$/i, "");
  if (!cleaned) return null;
  const match = /^(\d{1,10})(?:[.,]0{1,2})?$/.exec(cleaned);
  if (!match || Number(match[1]) <= 0) return INVALID;
  return String(Number(match[1]));
}

function wholeNumber(raw: string, min: number, max: number): number | null | typeof INVALID {
  const cleaned = raw.replace(/[\s\u00a0\u202f]/g, "");
  if (!cleaned) return null;
  if (!/^\d{1,10}$/.test(cleaned)) return INVALID;
  const value = Number(cleaned);
  return value < min || value > max ? INVALID : value;
}

/** The digits of a card ID, typed as is or taken from a kaspi.kz/shop/p/…-123/ link. */
function kaspiCardId(raw: string): string | null {
  const value = raw.trim();
  if (/^\d{1,64}$/.test(value)) return value;
  const link = /kaspi\.kz\/shop\/p\/[^?#\s]*?-(\d{5,64})\/?(?:[?#].*)?$/i.exec(value);
  return link ? link[1] : null;
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
