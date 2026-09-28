"""`tailoy.parse_search_html` — Magento product grid, no JSON API exists.

Live capture, 2026-09-28: `tests/fixtures/tailoy/search.html`, 27 raw
`li.product-item` cards for query "pokemon tcg 30", 25 of which have a
title+price pair (the other 2 are incomplete decoy cards, skipped like
Ilahui's duplicate cards). Of those 25, 8 are the real "30th Anniversary"
family — confirmed live to ALL be pre-order ("Ver Preventa",
`type="button"`), not real stock, which is exactly the regression this file
guards against (an earlier unverified claim assumed search-result presence
meant real stock).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from selectolax.parser import HTMLParser

from tracker.adapters.stores.tailoy import (
    _external_id_from_url,
    _is_really_available,
    parse_search_html,
)
from tracker.domain.model import Availability, Language, ProductType

NOW = datetime(2026, 1, 15, tzinfo=UTC)
BASE_URL = "https://www.tailoy.com.pe"


def test_parse_search_html_reads_real_captured_fixture(fixture_body) -> None:
    body = fixture_body("tailoy", "search.html")
    offers = parse_search_html(body, NOW, BASE_URL)

    assert len(offers) == 25
    assert all(offer.store == "tailoy" for offer in offers)


def test_30th_anniversary_family_is_correctly_out_of_stock_preorder(fixture_body) -> None:
    """Regression: presence in search results is NOT proof of real stock —
    all 8 of these are pre-order listings ("Ver Preventa"), confirmed by
    inspecting the actual button markup (type="button", not type="submit")."""
    body = fixture_body("tailoy", "search.html")
    offers = {o.external_id: o for o in parse_search_html(body, NOW, BASE_URL)}

    preorder_skus = ("55972002", "55972003", "55971003", "55971002", "55970002", "55969002", "55969003", "55970003")
    for sku in preorder_skus:
        assert offers[sku].availability is Availability.OUT_OF_STOCK

    english_pack = offers["55971002"]
    assert english_pack.title == "Pack Pokémon Tcg 30Th Día X3 Inglés"
    assert english_pack.language is Language.ENGLISH
    assert english_pack.product_type is ProductType.BOOSTER_PACK
    assert english_pack.price is not None
    assert english_pack.price.amount == Decimal("89.90")
    assert english_pack.url.endswith("-55971002.html")


def test_a_genuinely_purchasable_product_is_in_stock(fixture_body) -> None:
    """Contrast case: a real, non-preorder Pokémon product on the same
    search results page (type="submit", title="Agregar") must be IN_STOCK."""
    body = fixture_body("tailoy", "search.html")
    offers = parse_search_html(body, NOW, BASE_URL)
    in_stock = [o for o in offers if o.availability is Availability.IN_STOCK]
    assert len(in_stock) > 0
    assert all("30Th" not in o.title for o in in_stock)  # none of the preorder family


def test_external_id_from_url_extracts_trailing_numeric_id() -> None:
    url = "https://www.tailoy.com.pe/pokemon-tcg-30-aniversario-pack-x3-tech-sticker-night-en-ingles-55971003.html"
    assert _external_id_from_url(url) == "55971003"


def test_external_id_from_url_returns_none_when_no_match() -> None:
    assert _external_id_from_url("https://www.tailoy.com.pe/some-category/") is None


def test_is_really_available_true_for_submit_button_without_preorder_title() -> None:
    html = '<li><button type="submit" title="Agregar">Agregar</button></li>'
    card = HTMLParser(html).css_first("li")
    assert _is_really_available(card) is True


def test_is_really_available_false_for_button_type_redirect() -> None:
    html = '<li><button type="button" title="Ver Preventa">Ver Preventa</button></li>'
    card = HTMLParser(html).css_first("li")
    assert _is_really_available(card) is False


def test_is_really_available_false_when_submit_button_title_mentions_agotado() -> None:
    """Belt-and-suspenders: even a type="submit" button is distrusted if its
    own title says otherwise — the combined check the user asked for."""
    html = '<li><button type="submit" title="Agotado">Agotado</button></li>'
    card = HTMLParser(html).css_first("li")
    assert _is_really_available(card) is False


def test_is_really_available_false_when_no_button_present() -> None:
    html = "<li><span>no button here</span></li>"
    card = HTMLParser(html).css_first("li")
    assert _is_really_available(card) is False
