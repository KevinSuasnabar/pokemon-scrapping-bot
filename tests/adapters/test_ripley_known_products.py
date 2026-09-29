"""Ripley's `SupportsKnownProducts.fetch_known` — direct detail-page fetch
by SKU, bypassing search entirely. Confirmed live 2026-09-29: a real
product URL with its descriptive slug swapped for an arbitrary placeholder
still resolved to the same product, so `fetch_known` always uses a fixed
placeholder slug rather than needing the real product name."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from tracker.adapters.stores.ripley import RipleyAdapter, _parse_known_product_detail
from tracker.application.dto import RawPayload
from tracker.domain.errors import StoreFetchError, StoreParseError
from tracker.domain.model import Availability, Language, ProductType

NOW = datetime(2026, 1, 15, tzinfo=UTC)


def _detail_page(name: str, price: object, quantity: object) -> str:
    payload = {
        "props": {
            "pageProps": {
                "detailProps": {
                    "data": {"product": {"name": name, "price": price, "xcatentryQuantity": quantity}}
                }
            }
        }
    }
    return (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(payload)}</script></body></html>"
    )


class _FakeTransport:
    """Maps a URL to a canned response or a `StoreFetchError`."""

    def __init__(self, responses: dict[str, str | Exception]) -> None:
        self._responses = responses
        self.requested_urls: list[str] = []

    def fetch_html(self, url: str) -> RawPayload:
        self.requested_urls.append(url)
        response = self._responses.get(url)
        if isinstance(response, Exception):
            raise response
        if response is None:
            raise StoreFetchError(f"no canned response for {url}")
        return RawPayload(
            store="ripley", source_url=url, content_type="text/html", body=response, fetched_at=NOW
        )


# --- _parse_known_product_detail (pure) ---


def test_parse_known_product_detail_extracts_fields() -> None:
    body = _detail_page("Pokemon TCG 30 Aniversario ETB En Ingles", "289.90", 5)
    offer = _parse_known_product_detail(body, "123", "https://simple.ripley.com.pe/x-123p", NOW)

    assert offer.external_id == "123"
    assert offer.title == "Pokemon TCG 30 Aniversario ETB En Ingles"
    assert offer.price is not None
    assert offer.availability is Availability.IN_STOCK
    assert offer.language is Language.ENGLISH
    assert offer.product_type is ProductType.ETB


def test_parse_known_product_detail_zero_quantity_is_out_of_stock() -> None:
    body = _detail_page("Some Product", "99.90", 0)
    offer = _parse_known_product_detail(body, "123", "https://x/123p", NOW)
    assert offer.availability is Availability.OUT_OF_STOCK


def test_parse_known_product_detail_missing_next_data_raises() -> None:
    with pytest.raises(StoreParseError):
        _parse_known_product_detail("<html><body>no data</body></html>", "123", "https://x/123p", NOW)


def test_parse_known_product_detail_missing_name_raises() -> None:
    body = (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        '{"props": {"pageProps": {"detailProps": {"data": {"product": {}}}}}}'
        "</script></body></html>"
    )
    with pytest.raises(StoreParseError):
        _parse_known_product_detail(body, "123", "https://x/123p", NOW)


# --- RipleyAdapter.fetch_known ---


def test_fetch_known_uses_placeholder_slug_url() -> None:
    detail_url = "https://simple.ripley.com.pe/producto-123p"
    transport = _FakeTransport({detail_url: _detail_page("Some Product", "99.90", 3)})
    adapter = RipleyAdapter(transport=transport)

    offers = adapter.fetch_known(["123"], NOW)

    assert len(offers) == 1
    assert offers[0].external_id == "123"
    assert transport.requested_urls == [detail_url]


def test_fetch_known_skips_sku_that_fails_to_fetch() -> None:
    good_url = "https://simple.ripley.com.pe/producto-2p"
    transport = _FakeTransport({good_url: _detail_page("Good Product", "99.90", 3)})
    adapter = RipleyAdapter(transport=transport)

    offers = adapter.fetch_known(["1", "2"], NOW)

    assert len(offers) == 1
    assert offers[0].external_id == "2"


def test_fetch_known_falls_back_to_no_p_url_when_the_p_form_fails() -> None:
    """Regression, confirmed live 2026-09-29: a color-variant product
    (Ripley's EX Box Azul, Tech Sticker Amarillo) 404s on the `-{sku}p`
    form that a plain product needs, and only resolves at `-{sku}`
    (no "p") — there's no way to know which shape a SKU needs up front."""
    p_url = "https://simple.ripley.com.pe/producto-999p"
    no_p_url = "https://simple.ripley.com.pe/producto-999"
    transport = _FakeTransport({no_p_url: _detail_page("Variant Product", "199.90", 2)})
    adapter = RipleyAdapter(transport=transport)

    offers = adapter.fetch_known(["999"], NOW)

    assert len(offers) == 1
    assert offers[0].external_id == "999"
    assert offers[0].url == no_p_url
    assert transport.requested_urls == [p_url, no_p_url]  # "p" tried first, then the fallback


def test_fetch_known_skips_sku_when_both_url_forms_fail() -> None:
    transport = _FakeTransport({})  # neither form has a canned response
    adapter = RipleyAdapter(transport=transport)

    offers = adapter.fetch_known(["999"], NOW)

    assert offers == []
    assert transport.requested_urls == [
        "https://simple.ripley.com.pe/producto-999p",
        "https://simple.ripley.com.pe/producto-999",
    ]


def test_fetch_known_skips_sku_with_unparseable_response() -> None:
    bad_url = "https://simple.ripley.com.pe/producto-1p"
    good_url = "https://simple.ripley.com.pe/producto-2p"
    transport = _FakeTransport(
        {bad_url: "<html>no next data</html>", good_url: _detail_page("Good Product", "99.90", 3)}
    )
    adapter = RipleyAdapter(transport=transport)

    offers = adapter.fetch_known(["1", "2"], NOW)

    assert len(offers) == 1
    assert offers[0].external_id == "2"
