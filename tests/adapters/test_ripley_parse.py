"""`ripley.parse_search_page` reading Phase 0's live-captured spike fixture
(`tests/fixtures/ripley/spike_search_pokemon.html`) — no synthetic HTML,
straight from the real Task 0 probe."""

from __future__ import annotations

import json
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

    # Live capture, 2026-09-17: 48 raw products for query "pokemon", of which
    # 25 are seller="MARKETPLACE" (filtered out, see
    # test_parse_search_page_excludes_marketplace_listings below) and 23 are
    # seller="RIPLEY" (kept).
    assert len(offers) == 23
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


def _next_data_html(products: list[dict]) -> str:
    payload = {"props": {"pageProps": {"findabilityProps": {"data": {"products": products}}}}}
    return (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(payload)}</script></body></html>"
    )


def test_parse_search_page_skips_only_the_malformed_product_entry(capsys) -> None:
    """Regression: one malformed product entry (missing required fields)
    must not lose the other valid products on the same page (same reasoning
    as vtex.py's parse_products — see that test for the real-world incident
    this mirrors)."""
    html = _next_data_html(
        [
            {"sku": "1", "name": "Valid Product One", "inStock": True},
            {"sku": "2"},  # missing "name" -> malformed
            {"sku": "3", "name": "Valid Product Two", "inStock": False},
        ]
    )
    offers = parse_search_page(html, NOW, BASE_URL)

    assert {o.external_id for o in offers} == {"1", "3"}
    assert "skipping malformed product entry" in capsys.readouterr().err


def test_parse_search_page_excludes_marketplace_listings() -> None:
    """User-requested filter (2026-09-28): Ripley's own `seller` field
    reliably distinguishes direct sales ("RIPLEY") from third-party
    marketplace resellers ("MARKETPLACE"), who were observed live pricing
    the same kind of item far above Ripley's own price (S/789-1659 vs
    S/289.90 for an ETB). Only direct-sold listings are tracked."""
    html = _next_data_html(
        [
            {"sku": "1", "name": "Direct Sale ETB", "inStock": True, "seller": "RIPLEY"},
            {"sku": "2", "name": "Marketplace Resale ETB", "inStock": True, "seller": "MARKETPLACE"},
            {"sku": "3", "name": "No Seller Field At All", "inStock": True},
        ]
    )
    offers = parse_search_page(html, NOW, BASE_URL)

    external_ids = {o.external_id for o in offers}
    assert "1" in external_ids
    assert "2" not in external_ids
    # A product with no "seller" field at all isn't a known marketplace
    # listing — absence of the field is not the same as seller="MARKETPLACE",
    # so it's kept rather than silently dropped.
    assert "3" in external_ids
