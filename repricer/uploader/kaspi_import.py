"""Read a merchant's Kaspi XML price list before connecting it to repricing.

The export does not normally contain the public product-card ID. Imported
products therefore retain their current prices as manual rules until a card is
linked and the owner deliberately chooses an automatic strategy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

MAX_XML_BYTES = 5_000_000
MAX_OFFERS = 10_000


@dataclass(frozen=True, slots=True)
class ImportedAvailability:
    store_id: str
    available: bool
    stock_count: int | None
    preorder_days: int | None


@dataclass(frozen=True, slots=True)
class ImportedOffer:
    sku: str
    title: str
    brand: str
    kaspi_product_id: str
    base_price: Decimal | None
    city_prices: dict[str, Decimal]
    availabilities: tuple[ImportedAvailability, ...]
    inferred_card_id: bool = False


@dataclass(frozen=True, slots=True)
class ImportedCatalog:
    merchant_id: str
    offers: tuple[ImportedOffer, ...]


def parse_kaspi_xml(
    content: bytes, *, infer_card_ids_from_sku: bool = False
) -> ImportedCatalog:
    if len(content) > MAX_XML_BYTES:
        raise ValueError("XML-файл слишком большой (максимум 5 МБ)")
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise ValueError("XML с DTD или сущностями не поддерживается")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError("Не удалось прочитать XML-файл") from exc
    if _name(root) != "kaspi_catalog":
        raise ValueError("Ожидается корневой элемент kaspi_catalog")
    merchant_id = _text(root, "merchantid")
    if not merchant_id:
        raise ValueError("В прайсе отсутствует merchantid")
    offers_node = _child(root, "offers")
    if offers_node is None:
        raise ValueError("В прайсе отсутствует раздел offers")
    nodes = [node for node in offers_node if _name(node) == "offer"]
    if not nodes:
        raise ValueError("В прайсе нет товаров")
    if len(nodes) > MAX_OFFERS:
        raise ValueError("В одном файле поддерживается не более 10000 товаров")

    offers: list[ImportedOffer] = []
    seen: set[str] = set()
    for node in nodes:
        sku = (node.get("sku") or "").strip()
        if not sku or len(sku) > 128:
            raise ValueError("У товара отсутствует SKU или он слишком длинный")
        if sku in seen:
            raise ValueError(f"SKU {sku}: повторяется в файле")
        seen.add(sku)
        title = _text(node, "model")
        brand = _text(node, "brand")
        if not title or len(title) > 512 or len(brand) > 128:
            raise ValueError(f"SKU {sku}: неверное название или бренд")
        product_id = (node.get("kaspiProductId") or node.get("productId") or "").strip()
        inferred_card_id = False
        if not product_id and infer_card_ids_from_sku:
            match = re.fullmatch(r"(\d{7,12})(?:_\d{9})?", sku)
            if match:
                product_id = match.group(1)
                inferred_card_id = True
        if product_id and not re.fullmatch(r"\d{1,64}", product_id):
            raise ValueError(f"SKU {sku}: неверный ID карточки")

        availability_node = _child(node, "availabilities")
        availabilities: list[ImportedAvailability] = []
        store_ids: set[str] = set()
        if availability_node is not None:
            for availability in availability_node:
                if _name(availability) != "availability":
                    continue
                store_id = (availability.get("storeId") or "").strip()
                if not store_id or len(store_id) > 64 or store_id in store_ids:
                    raise ValueError(f"SKU {sku}: неверный или повторный storeId")
                store_ids.add(store_id)
                status = (availability.get("available") or "").lower()
                if status not in {"yes", "no"}:
                    raise ValueError(f"SKU {sku}: available должен быть yes или no")
                stock = _count(
                    availability.get("stockCount"), 0, 2_147_483_647, sku,
                    "stockCount", allow_integral_decimal=True,
                )
                preorder = _count(availability.get("preOrder"), 0, 30, sku, "preOrder")
                availabilities.append(ImportedAvailability(store_id, status == "yes", stock, preorder))
        if not availabilities:
            raise ValueError(f"SKU {sku}: не указан ни один склад")

        base = _price(_text(node, "price"), sku) if _child(node, "price") is not None else None
        city_prices: dict[str, Decimal] = {}
        city_node = _child(node, "cityprices")
        if city_node is not None:
            for price_node in city_node:
                if _name(price_node) not in {"cityprice", "price"}:
                    continue
                city_id = (price_node.get("cityId") or "").strip()
                if not re.fullmatch(r"\d{1,16}", city_id) or city_id in city_prices:
                    raise ValueError(f"SKU {sku}: неверный или повторный cityId")
                city_prices[city_id] = _price((price_node.text or "").strip(), sku)
        if base is None and not city_prices:
            raise ValueError(f"SKU {sku}: отсутствует цена")
        offers.append(ImportedOffer(
            sku, title, brand, product_id, base, city_prices, tuple(availabilities), inferred_card_id
        ))
    return ImportedCatalog(merchant_id, tuple(offers))


def _name(node: ET.Element) -> str:
    return node.tag.rpartition("}")[2]


def _child(node: ET.Element, name: str) -> ET.Element | None:
    return next((child for child in node if _name(child) == name), None)


def _text(node: ET.Element, name: str) -> str:
    child = _child(node, name)
    return (child.text or "").strip() if child is not None else ""


def _price(raw: str, sku: str) -> Decimal:
    try:
        price = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"SKU {sku}: неверная цена") from exc
    if not price.is_finite() or price <= 0 or price != price.to_integral_value() or price >= 10**10:
        raise ValueError(f"SKU {sku}: цена должна быть целым положительным числом")
    return price


def _count(
    raw: str | None, minimum: int, maximum: int | None, sku: str, field: str,
    *, allow_integral_decimal: bool = False,
) -> int | None:
    if raw is None:
        return None
    if raw.isdecimal():
        value = int(raw)
    elif allow_integral_decimal:
        try:
            decimal = Decimal(raw)
        except InvalidOperation as exc:
            raise ValueError(f"SKU {sku}: неверное значение {field}") from exc
        if not decimal.is_finite() or decimal != decimal.to_integral_value():
            raise ValueError(f"SKU {sku}: неверное значение {field}")
        value = int(decimal)
    else:
        raise ValueError(f"SKU {sku}: неверное значение {field}")
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"SKU {sku}: неверное значение {field}")
    return value
