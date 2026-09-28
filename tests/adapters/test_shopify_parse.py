"""`shopify.parse_suggest_json` — shared across every Shopify store (Ilahui,
Pharmax): this is Shopify's own predictive-search API shape, not a
per-store theme detail, so one fixture-driven test suffices for all of them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tracker.adapters.stores.shopify import parse_suggest_json
from tracker.domain.errors import StoreParseError
from tracker.domain.model import Availability, Language

NOW = datetime(2026, 1, 15, tzinfo=UTC)
BASE_URL = "https://ilahuiperu.com"


def test_parse_suggest_json_maps_fields(fixture_body) -> None:
    body = fixture_body("ilahui", "suggest.json")
    offers = parse_suggest_json("ilahui", body, NOW, BASE_URL)

    assert len(offers) == 3
    spanish_etb, english_etb, english_pack = offers

    assert spanish_etb.language is Language.SPANISH
    assert spanish_etb.availability is Availability.IN_STOCK

    assert english_etb.store == "ilahui"
    assert english_etb.external_id == "9595590344941"
    assert english_etb.language is Language.ENGLISH
    assert english_etb.availability is Availability.OUT_OF_STOCK
    assert english_etb.price is not None
    assert english_etb.price.amount == Decimal("289.90")
    assert english_etb.url == (
        "https://ilahuiperu.com/products/pokemon-tcg-30-aniversario-etb-en-ingles"
        "?_pos=2&_psq=pokemon+30+aniversario&_psid=b4bfb3b5f&_ss=e"
    )

    assert english_pack.availability is Availability.IN_STOCK


def test_parse_suggest_json_invalid_json_raises() -> None:
    with pytest.raises(StoreParseError):
        parse_suggest_json("ilahui", "not json", NOW, BASE_URL)


def test_parse_suggest_json_unexpected_shape_raises() -> None:
    with pytest.raises(StoreParseError):
        parse_suggest_json("ilahui", '{"resources": {}}', NOW, BASE_URL)


def test_parse_suggest_json_skips_only_the_malformed_product_entry(capsys) -> None:
    """Regression: one malformed product entry must not lose the other
    valid products on the same page (same reasoning as vtex.py's
    parse_products — see that test for the real-world incident this
    mirrors)."""
    body = (
        '{"resources": {"results": {"products": ['
        '{"id": 1, "title": "Valid Product One", "url": "/p1", "available": true},'
        '{"id": 2},'  # missing "title"/"url" -> malformed
        '{"id": 3, "title": "Valid Product Two", "url": "/p3", "available": false}'
        "]}}}"
    )
    offers = parse_suggest_json("ilahui", body, NOW, BASE_URL)

    assert {o.external_id for o in offers} == {"1", "3"}
    assert "skipping malformed suggest.json product" in capsys.readouterr().err


def test_parse_suggest_json_uses_the_given_store_slug() -> None:
    """The whole point of extracting this into shopify.py: any Shopify
    store's offers carry ITS OWN slug, not a hardcoded one."""
    body = '{"resources": {"results": {"products": [{"id": 1, "title": "T", "url": "/p1", "available": true}]}}}'
    offers = parse_suggest_json("pharmax", body, NOW, "https://pharmax.com.pe")
    assert offers[0].store == "pharmax"
