"""Tai Loy's `SupportsKnownProducts.fetch_known` — direct product-page fetch
by full URL. Regression fixture (`product_sylveon.html`, captured live
2026-09-29): a real, in-stock, purchasable product that Tai Loy's own search
never returned for any query tried (not even its own name) — a Magento
search-index lag, not a bug in our matching logic."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from tracker.adapters.stores.tailoy import TaiLoyAdapter, _parse_known_product_detail
from tracker.domain.errors import StoreParseError
from tracker.domain.model import Availability, Language, ProductType

NOW = datetime(2026, 1, 15, tzinfo=UTC)
BASE_URL = "https://www.tailoy.com.pe"
SYLVEON_URL = "https://www.tailoy.com.pe/pokemon-tcg-30-aniversario-ex-box-sylveon-en-ingles-55969003.html"


@pytest.fixture(autouse=True)
def _no_real_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tracker.infrastructure.http.client.time.sleep", lambda seconds: None)


def _client_with_handler(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- _parse_known_product_detail (pure), against the real fixture ---


def test_parse_known_product_detail_matches_the_real_ghost_product(fixture_body) -> None:
    """The exact real-world case this feature exists for: the H1 says "30Th"
    with no "Aniversario"/"Anniversary" — `is_target_offer()` would reject
    it, which is precisely why `fetch_known` results bypass that filter."""
    body = fixture_body("tailoy", "product_sylveon.html")
    offer = _parse_known_product_detail(body, SYLVEON_URL, NOW)

    assert offer.title == "Caja Pokémon Tcg 30Th Sylveon Inglés"
    assert offer.external_id == "55969003"
    assert offer.price is not None
    assert offer.price.amount == Decimal("129.90")
    assert offer.availability is Availability.IN_STOCK
    assert offer.language is Language.ENGLISH
    assert offer.product_type is ProductType.COLLECTION_BOX


def test_parse_known_product_detail_no_h1_raises() -> None:
    with pytest.raises(StoreParseError):
        _parse_known_product_detail("<html><body>no title</body></html>", SYLVEON_URL, NOW)


def test_parse_known_product_detail_no_addtocart_button_is_out_of_stock() -> None:
    body = "<html><body><h1>Some Product</h1><div class='price'>S/99.90</div></body></html>"
    offer = _parse_known_product_detail(body, SYLVEON_URL, NOW)
    assert offer.availability is Availability.OUT_OF_STOCK


def test_parse_known_product_detail_ignores_unrelated_submit_buttons() -> None:
    """Regression: a detail page also has a search-box submit button — the
    add-to-cart check must key off `#product-addtocart-button`, not the
    first `button[type="submit"]` in document order."""
    body = (
        "<html><body>"
        '<button type="submit" title="Buscar">search</button>'
        "<h1>Some Product</h1>"
        "<div class='price'>S/99.90</div>"
        '<button id="product-addtocart-button" type="button" title="Ver Preventa">preorder</button>'
        "</body></html>"
    )
    offer = _parse_known_product_detail(body, SYLVEON_URL, NOW)
    assert offer.availability is Availability.OUT_OF_STOCK  # type="button", not "submit" -> preorder


# --- TaiLoyAdapter.fetch_known ---


def test_fetch_known_fetches_the_full_url_directly(fixture_body) -> None:
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, text=fixture_body("tailoy", "product_sylveon.html"))

    adapter = TaiLoyAdapter(_client_with_handler(handler), BASE_URL)
    offers = adapter.fetch_known([SYLVEON_URL], NOW)

    assert len(offers) == 1
    assert offers[0].external_id == "55969003"
    assert seen_urls == [SYLVEON_URL]


def test_fetch_known_skips_a_url_that_fails_to_fetch(fixture_body) -> None:
    good_url = SYLVEON_URL
    bad_url = "https://www.tailoy.com.pe/does-not-exist-999.html"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == bad_url:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, text=fixture_body("tailoy", "product_sylveon.html"))

    adapter = TaiLoyAdapter(_client_with_handler(handler), BASE_URL)
    offers = adapter.fetch_known([bad_url, good_url], NOW)

    assert len(offers) == 1
    assert offers[0].external_id == "55969003"
