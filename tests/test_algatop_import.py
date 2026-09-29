"""Reading AlgaTop's product export: the columns of its cabinet, strictly."""

import io
from decimal import Decimal

import pytest
from openpyxl import Workbook

from repricer.uploader.algatop_import import AlgaTopFileError, parse_algatop

#: The header row exactly as AlgaTop exports it (2026-09-29).
HEADER = [
    "Артикул", "Артикул Kaspi", "Название", "Ссылка", "Статус", "NTIN", "Город", "Цена",
    "Цена 1 места", "Цена закупа", "Автоснижение", "Мин.цена", "Автоповышение", "Макс.цена",
    "Шаг демпинга", "Позиция", "Остатки", "Предзаказ",
]
ALPHA = [
    "106171574_033512519", 106171574, "ALPHA i7-11700F/ RTX 3060 Ti", "https://kaspi.kz/shop/p/-106171574/",
    "Опубликовано", None, "Астана", 599980, 599980, 549492, 1, 539910, 1, 599980, 2, 1, 1, 0,
]
OFF_SALE = [
    "175873331_719000979", 175873331, "Кресло", "", "Снято с продажи", None, "Астана", 360000,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
]


def xlsx(*rows: list[object]) -> bytes:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    for row in rows:
        sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_every_column_of_the_export_is_read() -> None:
    rows, errors = parse_algatop(xlsx(HEADER, ALPHA))

    assert errors == []
    [row] = rows
    assert (row.sku, row.kaspi_product_id, row.city_id) == ("106171574_033512519", "106171574", "710000000")
    assert (row.price, row.min_price, row.max_price) == (Decimal(599980), Decimal(539910), Decimal(599980))
    assert (row.purchase_price, row.step, row.auto_decrease, row.auto_increase) == (
        Decimal(549492), 2, True, True,
    )
    assert (row.published, row.stock, row.preorder_days) == (True, 1, 0)


def test_zero_means_not_set_as_in_algatop() -> None:
    [row], _ = parse_algatop(xlsx(HEADER, OFF_SALE))

    assert row.published is False
    assert (row.min_price, row.max_price, row.purchase_price, row.step) == (None, None, None, None)
    assert (row.auto_decrease, row.auto_increase) == (False, False)


def test_a_csv_saved_from_excel_reads_the_same() -> None:
    text = ";".join(HEADER) + "\n" + ";".join("" if cell is None else str(cell) for cell in ALPHA)

    rows, errors = parse_algatop(text.encode("utf-8-sig"))

    assert errors == [] and rows[0].min_price == Decimal(539910)


def test_bad_rows_are_reported_and_the_rest_is_kept() -> None:
    broken_city = [*ALPHA[:6], "Марс", *ALPHA[7:]]
    broken_price = [*ALPHA[:7], "дорого", *ALPHA[8:]]
    inverted = [*ALPHA[:11], 700000, *ALPHA[12:]]

    rows, errors = parse_algatop(xlsx(HEADER, ALPHA, broken_city, broken_price, inverted))

    assert len(rows) == 1
    assert [error.line for error in errors] == [3, 4, 5]
    assert "Марс" in errors[0].reason
    assert "не число" in errors[1].reason
    assert "выше макс" in errors[2].reason


def test_a_file_without_the_key_columns_is_refused() -> None:
    with pytest.raises(AlgaTopFileError, match="Артикул"):
        parse_algatop(xlsx(["Название", "Цена"], ["Кресло", 1000]))
    with pytest.raises(AlgaTopFileError, match="Город"):
        parse_algatop(xlsx(["Артикул", "Цена"], ["A", 1000]))
