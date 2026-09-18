"""`ripley.parse_search_page` reading Phase 0's live-captured spike fixture
(`tests/fixtures/ripley/spike_search_pokemon.html`) — no synthetic HTML,
straight from the real Task 0 probe."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tracker.adapters.stores.ripley import parse_search_page
from tracker.domain.errors import StoreParseError
from tracker.domain.model import Availability, Language

NOW = datetime(2026, 1, 15, tzinfo=UTC)
BASE_URL = "https://simple.ripley.com.pe"


def test_parse_search_page_reads_embedded_next_data(fixture_body) -> None:
    body = fixture_body("ripley", "spike_search_pokemon.html")
    offers = parse_search_page(body, NOW, BASE_URL)

    assert len(offers) == 48  # live capture, 2026-09-17: 48 products for query "pokemon"
    assert all(offer.store == "ripley" for offer in offers)
    assert all(offer.observed_at == NOW for offer in offers)


def test_parse_search_page_maps_known_product_fields(fixture_body) -> None:
    body = fixture_body("ripley", "spike_search_pokemon.html")
    offers = {offer.external_id: offer for offer in parse_search_page(body, NOW, BASE_URL)}

    lego = offers["2032372390012"]
    assert "EEVEE" in lego.title
    assert lego.price is not None
    assert lego.price.amount == Decimal("399.20")
    assert lego.availability is Availability.IN_STOCK
    assert lego.url.startswith(BASE_URL + "/")
    assert lego.url.endswith(f"-{lego.external_id}p")


def test_parse_search_page_detects_language_and_type_from_title(fixture_body) -> None:
    body = fixture_body("ripley", "spike_search_pokemon.html")
    offers = {offer.external_id: offer for offer in parse_search_page(body, NOW, BASE_URL)}

    english_etb = offers["2032374556447"]  # "POKEMON TCG PITCH ELITE TRAINER INGLES ..."
    assert english_etb.language is Language.ENGLISH

    spanish_offer = offers["2032375476478"]  # "... FIRST PARTNER S3 ESPAÑOL ..."
    assert spanish_offer.language is Language.SPANISH


def test_parse_search_page_missing_next_data_raises() -> None:
    with pytest.raises(StoreParseError):
        parse_search_page("<html><body>no data here</body></html>", NOW, BASE_URL)


def test_parse_search_page_malformed_next_data_raises() -> None:
    html = '<html><body><script id="__NEXT_DATA__" type="application/json">{"props": {}}</script></body></html>'
    with pytest.raises(StoreParseError):
        parse_search_page(html, NOW, BASE_URL)
