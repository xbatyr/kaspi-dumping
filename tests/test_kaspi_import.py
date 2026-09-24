"""A small, anonymised sample of the merchant's active Kaspi XML export."""

from __future__ import annotations

from decimal import Decimal

import pytest

from repricer.uploader.kaspi_import import parse_kaspi_xml


SAMPLE = b'''<?xml version="1.0" encoding="UTF-8"?>
<kaspi_catalog xmlns="kaspiShopping" date="2026-09-24 21:11">
  <company>Example</company><merchantid>30123456</merchantid>
  <offers><offer sku="130342357_673822557">
    <model>Example camera</model><brand></brand>
    <availabilities><availability available="yes" storeId="SHOP_A" preOrder="0" stockCount="2.0"/></availabilities>
    <cityprices><cityprice cityId="710000000">127019</cityprice></cityprices>
  </offer></offers>
</kaspi_catalog>'''


def test_real_export_shape_imports_without_automatic_card_matching() -> None:
    catalog = parse_kaspi_xml(SAMPLE)
    offer = catalog.offers[0]

    assert catalog.merchant_id == "30123456"
    assert offer.sku == "130342357_673822557"
    assert offer.brand == ""
    assert offer.availabilities[0].stock_count == 2
    assert offer.city_prices == {"710000000": Decimal(127019)}
    assert offer.kaspi_product_id == ""


def test_card_id_can_be_inferred_from_this_sku_convention() -> None:
    offer = parse_kaspi_xml(SAMPLE, infer_card_ids_from_sku=True).offers[0]

    assert offer.kaspi_product_id == "130342357"
    assert offer.inferred_card_id is True


def test_fractional_stock_is_refused() -> None:
    with pytest.raises(ValueError, match="stockCount"):
        parse_kaspi_xml(SAMPLE.replace(b'2.0', b'2.5'))
