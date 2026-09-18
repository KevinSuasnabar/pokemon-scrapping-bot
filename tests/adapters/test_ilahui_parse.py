"""Ilahui parse — both branches (`suggest.json` primary, HTML fallback)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tracker.adapters.stores.ilahui import parse_search_html, parse_suggest_json
from tracker.domain.errors import StoreParseError
from tracker.domain.model import Availability, Language

NOW = datetime(2026, 1, 15, tzinfo=UTC)
BASE_URL = "https://ilahuiperu.com"


def test_parse_suggest_json_maps_fields(fixture_body) -> None:
    body = fixture_body("ilahui", "suggest.json")
    offers = parse_suggest_json(body, NOW, BASE_URL)

    assert len(offers) == 3
    spanish_etb, english_etb, english_pack = offers

    assert spanish_etb.language is Language.SPANISH
    assert spanish_etb.availability is Availability.IN_STOCK

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
        parse_suggest_json("not json", NOW, BASE_URL)


def test_parse_suggest_json_unexpected_shape_raises() -> None:
    with pytest.raises(StoreParseError):
        parse_suggest_json('{"resources": {}}', NOW, BASE_URL)


def test_parse_search_html_dedupes_and_skips_incomplete_cards(fixture_body) -> None:
    body = fixture_body("ilahui", "search.html")
    offers = parse_search_html(body, NOW, BASE_URL)

    # The fixture has 3 `div[data-product-id]` blocks for the English ETB
    # (one empty duplicate + one real card) plus one Spanish box card — only
    # the two real cards should produce offers.
    assert len(offers) == 2
    external_ids = {offer.external_id for offer in offers}
    assert external_ids == {"9595590344941", "9593969705197"}


def test_parse_search_html_maps_availability_and_price(fixture_body) -> None:
    body = fixture_body("ilahui", "search.html")
    offers = {offer.external_id: offer for offer in parse_search_html(body, NOW, BASE_URL)}

    english_etb = offers["9595590344941"]
    assert english_etb.language is Language.ENGLISH
    assert english_etb.availability is Availability.OUT_OF_STOCK
    assert english_etb.price is not None
    assert english_etb.price.amount == Decimal("289.90")

    spanish_box = offers["9593969705197"]
    assert spanish_box.language is Language.SPANISH
    assert spanish_box.availability is Availability.IN_STOCK
