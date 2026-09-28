"""Pharmax's theme-specific `parse_search_html` (fallback path; Impulse
theme, `.ProductItem` markup — different from Ilahui's theme). The shared
`suggest.json` path is tested once, generically, in test_shopify_parse.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from tracker.adapters.stores.pharmax import _external_id_from_href, parse_search_html
from tracker.domain.model import Availability

NOW = datetime(2026, 1, 15, tzinfo=UTC)
BASE_URL = "https://pharmax.com.pe"


def test_parse_search_html_reads_real_captured_fixture(fixture_body) -> None:
    """Live capture, 2026-09-28: 25 Pokémon-related products on Pharmax's
    search page (none happened to be sold out at capture time — see the
    module docstring on pharmax.py for why availability is UNKNOWN here)."""
    body = fixture_body("pharmax", "search.html")
    offers = parse_search_html(body, NOW, BASE_URL)

    assert len(offers) == 25
    assert all(offer.store == "pharmax" for offer in offers)
    assert all(offer.availability is Availability.UNKNOWN for offer in offers)


def test_parse_search_html_maps_known_product_fields(fixture_body) -> None:
    body = fixture_body("pharmax", "search.html")
    offers = {offer.external_id: offer for offer in parse_search_html(body, NOW, BASE_URL)}

    product = offers["pkw2766"]
    assert product.title == "SET DE JUEGO AMBIENTAL POKEMON ASST"
    assert product.price is not None
    assert product.price.amount == Decimal("109.90")
    assert product.url == "https://pharmax.com.pe/products/pkw2766?_pos=1&_sid=5ca8ad7c1&_ss=r"


def test_external_id_from_href_extracts_the_product_handle() -> None:
    assert _external_id_from_href("/products/pkw2766?_pos=1&_sid=abc") == "pkw2766"
    assert _external_id_from_href("/products/some-handle") == "some-handle"


def test_external_id_from_href_returns_none_for_non_product_links() -> None:
    assert _external_id_from_href("/collections/jugueteria") is None
    assert _external_id_from_href("") is None
