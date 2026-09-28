"""Ripley's real-stock confirmation step — `parse_product_detail_stock`
(pure) and `RipleyAdapter.search()`'s use of it.

User-requested (2026-09-28), after finding Falabella's search-index boolean
directly contradicted its own real `stockUnits` count on the same product:
don't trust search results' `inStock` boolean alone — confirm via a
per-product detail-page fetch before reporting something as available.
Confirmation only runs for offers search already reported IN_STOCK, so a
genuinely out-of-stock item costs no extra request.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from tracker.adapters.stores.ripley import RipleyAdapter, parse_product_detail_stock
from tracker.application.dto import RawPayload
from tracker.domain.errors import StoreFetchError
from tracker.domain.model import Availability

NOW = datetime(2026, 1, 15, tzinfo=UTC)


def _detail_page(quantity: object) -> str:
    payload = {
        "props": {
            "pageProps": {
                "detailProps": {"data": {"product": {"xcatentryQuantity": quantity}}}
            }
        }
    }
    return (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(payload)}</script></body></html>"
    )


def _search_page(sku: str, name: str, in_stock: bool) -> str:
    payload = {
        "props": {
            "pageProps": {
                "findabilityProps": {
                    "data": {
                        "products": [
                            {"sku": sku, "name": name, "inStock": in_stock, "seller": "RIPLEY"}
                        ]
                    }
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


# --- parse_product_detail_stock (pure) ---


def test_parse_product_detail_stock_extracts_quantity() -> None:
    assert parse_product_detail_stock(_detail_page(5)) == 5


def test_parse_product_detail_stock_zero() -> None:
    assert parse_product_detail_stock(_detail_page(0)) == 0


def test_parse_product_detail_stock_missing_next_data_returns_none() -> None:
    assert parse_product_detail_stock("<html><body>no data</body></html>") is None


def test_parse_product_detail_stock_missing_field_returns_none() -> None:
    body = '<html><body><script id="__NEXT_DATA__" type="application/json">{"props": {}}</script></body></html>'
    assert parse_product_detail_stock(body) is None


def test_parse_product_detail_stock_non_numeric_returns_none() -> None:
    assert parse_product_detail_stock(_detail_page("not a number")) is None


# --- RipleyAdapter.search() confirmation behavior ---


def test_search_skips_confirmation_for_already_out_of_stock_offers() -> None:
    """No extra request should ever happen for something search already
    says is unavailable — confirmation is proportional to candidates."""
    search_url = "https://simple.ripley.com.pe/search/pokemon"
    transport = _FakeTransport({search_url: _search_page("1", "Some Product", in_stock=False)})
    adapter = RipleyAdapter(transport=transport)

    offers = adapter.search("pokemon", NOW)

    assert len(offers) == 1
    assert offers[0].availability is Availability.OUT_OF_STOCK
    assert transport.requested_urls == [search_url]  # no detail-page fetch


def test_search_confirms_and_keeps_in_stock_when_quantity_positive() -> None:
    search_url = "https://simple.ripley.com.pe/search/pokemon"
    detail_url = "https://simple.ripley.com.pe/some-product-1p"
    transport = _FakeTransport(
        {
            search_url: _search_page("1", "Some Product", in_stock=True),
            detail_url: _detail_page(3),
        }
    )
    adapter = RipleyAdapter(transport=transport)

    offers = adapter.search("pokemon", NOW)

    assert len(offers) == 1
    assert offers[0].availability is Availability.IN_STOCK
    assert detail_url in transport.requested_urls


def test_search_downgrades_to_out_of_stock_when_detail_quantity_is_zero() -> None:
    """The exact real-world case this feature exists for: search said
    available, the product page's real quantity says otherwise."""
    search_url = "https://simple.ripley.com.pe/search/pokemon"
    detail_url = "https://simple.ripley.com.pe/some-product-1p"
    transport = _FakeTransport(
        {
            search_url: _search_page("1", "Some Product", in_stock=True),
            detail_url: _detail_page(0),
        }
    )
    adapter = RipleyAdapter(transport=transport)

    offers = adapter.search("pokemon", NOW)

    assert offers[0].availability is Availability.OUT_OF_STOCK


@pytest.mark.parametrize(
    "detail_response",
    [
        StoreFetchError("network error"),
        "<html><body>no next data at all</body></html>",
    ],
    ids=["fetch_fails", "unparseable_page"],
)
def test_search_fails_closed_when_confirmation_cannot_be_obtained(detail_response) -> None:
    """Fail closed, not open: if we can't confirm, don't report it as
    available on the strength of the (possibly stale) search index alone."""
    search_url = "https://simple.ripley.com.pe/search/pokemon"
    detail_url = "https://simple.ripley.com.pe/some-product-1p"
    transport = _FakeTransport(
        {search_url: _search_page("1", "Some Product", in_stock=True), detail_url: detail_response}
    )
    adapter = RipleyAdapter(transport=transport)

    offers = adapter.search("pokemon", NOW)

    assert offers[0].availability is Availability.OUT_OF_STOCK
