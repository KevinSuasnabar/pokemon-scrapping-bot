"""`FalabellaAdapter.fetch()` and `.search()` — `httpx.MockTransport`.

Covers what the real-capture fixtures in test_falabella_parse.py can't:
deterministic positive/zero-quantity confirmation outcomes and fetch-error
handling, using synthetic (but real-shaped) payloads.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from tracker.adapters.stores.falabella import FalabellaAdapter
from tracker.domain.errors import StoreFetchError
from tracker.domain.model import Availability

BASE_URL = "https://www.falabella.com.pe"


@pytest.fixture(autouse=True)
def _no_real_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tracker.infrastructure.http.client.time.sleep", lambda seconds: None)


def _search_payload(sku_id: str, seller_id: str) -> dict:
    return {
        "props": {
            "pageProps": {
                "results": [
                    {
                        "skuId": sku_id,
                        "displayName": "Test Product",
                        "url": f"{BASE_URL}/falabella-pe/product/{sku_id}/test-product",
                        "prices": [{"type": "eventPrice", "crossed": False, "price": ["99.90"]}],
                        "sellerId": seller_id,
                    }
                ]
            }
        }
    }


def _product_payload(sku_id: str, quantity: int) -> dict:
    return {"props": {"pageProps": {"productData": {"stockUnits": {sku_id: quantity}}}}}


def _html(payload: dict) -> str:
    return (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(payload)}</script></body></html>"
    )


def test_fetch_builds_the_search_url() -> None:
    seen_urls: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(request.url)
        return httpx.Response(200, text="<html></html>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    FalabellaAdapter(client, BASE_URL).fetch("pokemon 30 aniversario")

    assert len(seen_urls) == 1
    assert seen_urls[0].path == "/falabella-pe/search"
    assert "pokemon" in seen_urls[0].params["Ntt"]


@pytest.mark.parametrize("status_code", [403, 500])
def test_fetch_http_error_raises_store_fetch_error(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="blocked")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(StoreFetchError):
        FalabellaAdapter(client, BASE_URL).fetch("pokemon")


def test_search_confirms_and_promotes_to_in_stock_when_quantity_positive() -> None:
    sku_id = "123"

    def handler(request: httpx.Request) -> httpx.Response:
        if "/search" in str(request.url):
            return httpx.Response(200, text=_html(_search_payload(sku_id, "FALABELLA_PERU")))
        return httpx.Response(200, text=_html(_product_payload(sku_id, 5)))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    offers = FalabellaAdapter(client, BASE_URL).search("pokemon", datetime.now(UTC))

    assert len(offers) == 1
    assert offers[0].availability is Availability.IN_STOCK


def test_search_stays_out_of_stock_when_quantity_zero() -> None:
    """The exact real-world case this whole feature exists for."""
    sku_id = "123"

    def handler(request: httpx.Request) -> httpx.Response:
        if "/search" in str(request.url):
            return httpx.Response(200, text=_html(_search_payload(sku_id, "FALABELLA_PERU")))
        return httpx.Response(200, text=_html(_product_payload(sku_id, 0)))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    offers = FalabellaAdapter(client, BASE_URL).search("pokemon", datetime.now(UTC))

    assert offers[0].availability is Availability.OUT_OF_STOCK


def test_search_excludes_marketplace_before_ever_confirming_stock() -> None:
    confirm_requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/search" in url:
            return httpx.Response(200, text=_html(_search_payload("123", "SC02FB7")))
        confirm_requests.append(url)
        return httpx.Response(200, text=_html(_product_payload("123", 5)))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    offers = FalabellaAdapter(client, BASE_URL).search("pokemon", datetime.now(UTC))

    assert offers == []
    assert confirm_requests == []  # never even tried to confirm a marketplace listing


def test_search_fails_closed_when_confirmation_fetch_fails() -> None:
    sku_id = "123"

    def handler(request: httpx.Request) -> httpx.Response:
        if "/search" in str(request.url):
            return httpx.Response(200, text=_html(_search_payload(sku_id, "FALABELLA_PERU")))
        return httpx.Response(500, text="server error")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    offers = FalabellaAdapter(client, BASE_URL).search("pokemon", datetime.now(UTC))

    assert offers[0].availability is Availability.OUT_OF_STOCK
