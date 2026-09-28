"""`vtex.parse_products` — fixture-driven field mapping. No HTTP layer at all."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tracker.adapters.stores.vtex import parse_products
from tracker.domain.errors import StoreParseError
from tracker.domain.model import Availability, Language, ProductType

NOW = datetime(2026, 1, 15, tzinfo=UTC)
BASE_URL = "https://www.plazavea.com.pe"


def test_parse_products_maps_all_fields(fixture_body) -> None:
    body = fixture_body("plaza_vea", "search_page_1.json")
    offers = parse_products("plaza_vea", body, NOW, BASE_URL)

    assert len(offers) == 3
    first = offers[0]
    assert first.store == "plaza_vea"
    assert first.external_id == "102340707"
    assert first.title.startswith("Colección de Figuritas Tech Sticker")
    assert first.url == (
        "https://www.plazavea.com.pe/coleccion-tech-sticker-tcg-pokemon-30-aniversario-ingles-20711650/p"
    )
    assert first.price is not None
    assert first.price.amount == Decimal("89.9")
    assert first.availability is Availability.IN_STOCK
    assert first.language is Language.ENGLISH
    assert first.observed_at == NOW


def test_parse_products_detects_spanish_and_etb(fixture_body) -> None:
    body = fixture_body("plaza_vea", "search_page_1.json")
    offers = parse_products("plaza_vea", body, NOW, BASE_URL)
    spanish_etb = offers[1]
    assert spanish_etb.language is Language.SPANISH
    assert spanish_etb.product_type is ProductType.ETB


def test_parse_products_zero_available_quantity_is_out_of_stock(fixture_body) -> None:
    body = fixture_body("plaza_vea", "search_page_1.json")
    offers = parse_products("plaza_vea", body, NOW, BASE_URL)
    plush = offers[2]
    assert plush.availability is Availability.OUT_OF_STOCK


def test_parse_products_second_page(fixture_body) -> None:
    body = fixture_body("plaza_vea", "search_page_2.json")
    offers = parse_products("plaza_vea", body, NOW, BASE_URL)
    assert len(offers) == 1
    assert offers[0].external_id == "102341234"


def test_parse_products_invalid_json_raises_store_parse_error() -> None:
    with pytest.raises(StoreParseError):
        parse_products("plaza_vea", "{not valid json", NOW, BASE_URL)


def test_parse_products_non_array_raises_store_parse_error() -> None:
    with pytest.raises(StoreParseError):
        parse_products("plaza_vea", '{"not": "a list"}', NOW, BASE_URL)


def test_parse_products_skips_malformed_product_instead_of_raising(capsys) -> None:
    """Regression (real Oechsle incident, 12 occurrences within a 20-minute
    window, 2026-09-18): one malformed product must not lose every other
    valid product on the same page."""
    body = '[{"productId": "1"}]'  # missing productName/items/sellers/...
    offers = parse_products("plaza_vea", body, NOW, BASE_URL)
    assert offers == []
    assert "skipping malformed product" in capsys.readouterr().err


def test_parse_products_skips_only_the_malformed_entry(fixture_body, capsys) -> None:
    """A malformed entry alongside otherwise-valid ones: only the bad one is
    dropped, the rest still parse normally."""
    body = fixture_body("plaza_vea", "search_page_1.json")
    products = json.loads(body)
    products.insert(1, {"productId": "broken-entry"})  # missing everything else
    offers = parse_products("plaza_vea", json.dumps(products), NOW, BASE_URL)

    assert "broken-entry" not in [o.external_id for o in offers]
    assert len(offers) == len(products) - 1
    assert "skipping malformed product" in capsys.readouterr().err


def test_parse_products_oechsle_fixture(fixture_body) -> None:
    body = fixture_body("oechsle", "search_page_1.json")
    offers = parse_products("oechsle", body, NOW, "https://www.oechsle.pe")
    assert len(offers) == 2
    assert offers[0].store == "oechsle"
    assert offers[0].language is Language.ENGLISH


def test_parse_products_metro_fixture(fixture_body) -> None:
    """Live capture, 2026-09-28: query "pokemon 30 aniversario" matched one
    unrelated generic Pokémon figure — no 30th-anniversary TCG product was
    in Metro's catalog at capture time, same as several other stores at
    various points in this project. Confirms the shared VTEX parser works
    unchanged for a 5th VTEX storefront."""
    body = fixture_body("metro", "search_page_1.json")
    offers = parse_products("metro", body, NOW, "https://www.metro.pe")
    assert len(offers) == 1
    assert offers[0].store == "metro"
    assert offers[0].external_id == "952148"


def _product_with_sellers(sellers: list[dict[str, object]], product_id: str = "999") -> str:
    return json.dumps(
        [
            {
                "productId": product_id,
                "productName": "Pokemon TCG 30 Aniversario ETB En Ingles",
                "linkText": "some-product",
                "items": [{"sellers": sellers}],
            }
        ]
    )


def test_parse_products_prefers_direct_seller_over_marketplace_default(fixture_body) -> None:
    """Regression, confirmed live on Oechsle 2026-09-28: a marketplace seller
    can appear FIRST in the `sellers` array (`sellerDefault: true`) ahead of
    the store's own listing — sellerId "1" must always win regardless of
    array order."""
    body = _product_with_sellers(
        [
            {
                "sellerId": "COMPRAFACILEXPRESS",
                "sellerName": "COMPRAFACIL EXPRESS",
                "sellerDefault": True,
                "commertialOffer": {"Price": 965.0, "AvailableQuantity": 25},
            },
            {
                "sellerId": "1",
                "sellerName": "oechsle",
                "sellerDefault": False,
                "commertialOffer": {"Price": 189.9, "AvailableQuantity": 3},
            },
        ]
    )
    offers = parse_products("oechsle", body, NOW, "https://www.oechsle.pe")
    assert len(offers) == 1
    assert offers[0].price is not None
    assert offers[0].price.amount == Decimal("189.9")
    assert offers[0].availability is Availability.IN_STOCK


def test_parse_products_skips_marketplace_only_listing() -> None:
    """A product Oechsle doesn't carry itself at all (no sellerId "1" entry)
    is a pure marketplace resale — same philosophy as Ripley/Falabella's
    marketplace filter: skip it, don't report the reseller's price as the
    store's own."""
    body = _product_with_sellers(
        [
            {
                "sellerId": "COMPRAFACILEXPRESS",
                "sellerName": "COMPRAFACIL EXPRESS",
                "commertialOffer": {"Price": 369.0, "AvailableQuantity": 25},
            }
        ]
    )
    offers = parse_products("oechsle", body, NOW, "https://www.oechsle.pe")
    assert offers == []


def test_parse_products_direct_seller_zero_price_is_not_published() -> None:
    """A direct seller with no stock reports `Price: 0` alongside
    `AvailableQuantity: 0` — that's "not offered", not a real S/ 0.00 price."""
    body = _product_with_sellers(
        [{"sellerId": "1", "sellerName": "oechsle", "commertialOffer": {"Price": 0, "AvailableQuantity": 0}}]
    )
    offers = parse_products("oechsle", body, NOW, "https://www.oechsle.pe")
    assert len(offers) == 1
    assert offers[0].price is None
    assert offers[0].availability is Availability.OUT_OF_STOCK


def test_parse_products_wong_fixture(fixture_body) -> None:
    """Live capture, 2026-09-28: same query, same product name as Metro's
    fixture, but a DIFFERENT external_id — confirmed live these are
    genuinely separate catalogs, not a shared backend, despite both being
    Cencosud-Peru VTEX storefronts."""
    body = fixture_body("wong", "search_page_1.json")
    offers = parse_products("wong", body, NOW, "https://www.wong.pe")
    assert len(offers) == 1
    assert offers[0].store == "wong"
    assert offers[0].external_id == "979185"
