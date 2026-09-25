"""An image belongs to the exact linked Kaspi card, not a similar search hit."""

from sqlalchemy.orm import Session

from repricer.db.models import Product
from repricer.images import refresh_missing_images
from repricer.scraper.kaspi_client import ProductCard


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def search_products(self, product_id: str, city_id: str, *, limit: int) -> list[ProductCard]:
        self.calls.append(product_id)
        return [
            ProductCard("999", "Similar", None, None, None, 0, "/p/-999/",
                        "https://resources.cdn-kaspi.kz/img/m/p/wrong.jpg"),
            ProductCard(product_id, "Exact", None, None, None, 0, f"/p/-{product_id}/",
                        "https://resources.cdn-kaspi.kz/img/m/p/right.jpg"),
        ]


def test_backfill_saves_only_exact_card_and_does_not_repeat(session: Session) -> None:
    product = Product(merchant_id="shop", sku="SKU", kaspi_product_id="123", title="Product")
    session.add(product)
    session.commit()
    client = FakeClient()

    assert refresh_missing_images(session, client=client) == (1, 1)  # type: ignore[arg-type]
    assert product.image_url == "https://resources.cdn-kaspi.kz/img/m/p/right.jpg"
    assert refresh_missing_images(session, client=client) == (0, 0)  # type: ignore[arg-type]
    assert client.calls == ["123"]
