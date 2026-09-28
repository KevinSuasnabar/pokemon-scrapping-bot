"""`falabella.parse_search_page` / `parse_product_detail_stock` — fixtures
trimmed from real live captures, 2026-09-28 (query "pikachu"; the Pokémon
30th-anniversary product line wasn't in stock anywhere at capture time, so
these fixtures exercise the marketplace filter and confirmation-mechanics
using whatever real listings existed then, not the target product itself —
see test_falabella_fetch.py for the confirmation *behavior* with synthetic
data covering both a positive and a zero-quantity case)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from tracker.adapters.stores.falabella import parse_product_detail_stock, parse_search_page
from tracker.domain.model import Availability

NOW = datetime(2026, 1, 15, tzinfo=UTC)


def test_parse_search_page_keeps_only_falabella_direct_sellers(fixture_body) -> None:
    """The fixture has 3 FALABELLA_PERU listings + 4 marketplace listings —
    only the 3 direct ones should survive."""
    body = fixture_body("falabella", "search.html")
    offers = parse_search_page(body, NOW)

    assert len(offers) == 3
    assert {o.external_id for o in offers} == {"21312651", "80039731", "80034564"}
    assert all(o.store == "falabella" for o in offers)


def test_parse_search_page_extracts_real_prices(fixture_body) -> None:
    body = fixture_body("falabella", "search.html")
    offers = {o.external_id: o for o in parse_search_page(body, NOW)}

    figure = offers["21312651"]
    assert figure.title == "Figura Pikachu Con Sonido Y Movimiento"
    assert figure.price is not None
    assert figure.price.amount == Decimal("90.90")  # internetPrice, not the crossed normalPrice


def test_parse_search_page_every_offer_starts_out_of_stock(fixture_body) -> None:
    """Confirmation (search()'s job, not parse()'s) is what can promote an
    offer to IN_STOCK — parse() alone never does, since `availability` in
    search results isn't a stock signal at all (see module docstring)."""
    body = fixture_body("falabella", "search.html")
    offers = parse_search_page(body, NOW)
    assert all(o.availability is Availability.OUT_OF_STOCK for o in offers)


def test_parse_product_detail_stock_reads_the_real_contradiction(fixture_body) -> None:
    """The exact real incident that justified this whole feature: this
    fixture's `isOutOfStock` field says False (available) while
    `stockUnits` says 0 for the same SKU — confirms we read the number, not
    the lying boolean."""
    body = fixture_body("falabella", "product.html")
    assert parse_product_detail_stock(body, "21312651") == 0


def test_parse_product_detail_stock_returns_none_for_unknown_sku(fixture_body) -> None:
    body = fixture_body("falabella", "product.html")
    assert parse_product_detail_stock(body, "99999999") is None


def test_parse_product_detail_stock_returns_none_when_no_next_data() -> None:
    assert parse_product_detail_stock("<html><body>no data</body></html>", "1") is None
