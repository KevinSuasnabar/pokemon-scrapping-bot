"""`vtex.parse_products` — fixture-driven field mapping. No HTTP layer at all."""

from __future__ import annotations

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


def test_parse_products_missing_required_field_raises_store_parse_error() -> None:
    with pytest.raises(StoreParseError):
        parse_products("plaza_vea", '[{"productId": "1"}]', NOW, BASE_URL)


def test_parse_products_oechsle_fixture(fixture_body) -> None:
    body = fixture_body("oechsle", "search_page_1.json")
    offers = parse_products("oechsle", body, NOW, "https://www.oechsle.pe")
    assert len(offers) == 2
    assert offers[0].store == "oechsle"
    assert offers[0].language is Language.ENGLISH
