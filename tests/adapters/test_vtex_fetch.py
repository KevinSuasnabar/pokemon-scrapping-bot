"""`VtexStoreAdapter.fetch()` — `httpx.MockTransport`: URL/params, headers,
pagination loop over the total-count header, and error mapping."""

from __future__ import annotations

import httpx
import pytest

from tracker.adapters.stores.vtex import MAX_PAGES, VtexStoreAdapter
from tracker.domain.errors import StoreFetchError

BASE_URL = "https://www.plazavea.com.pe"


def _client_with_handler(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": "test-agent"})


def test_fetch_single_page_when_no_pagination_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[], headers={})

    adapter = VtexStoreAdapter("plaza_vea", BASE_URL, _client_with_handler(handler))
    payloads = adapter.fetch("pokemon 30 aniversario")
    assert len(payloads) == 1
    assert payloads[0].store == "plaza_vea"


def test_fetch_url_and_query_params() -> None:
    seen_urls: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(request.url)
        return httpx.Response(200, json=[], headers={})

    adapter = VtexStoreAdapter("plaza_vea", BASE_URL, _client_with_handler(handler))
    adapter.fetch("pokemon 30 aniversario")

    assert len(seen_urls) == 1
    url = seen_urls[0]
    assert url.path == "/api/catalog_system/pub/products/search"
    assert url.params["_from"] == "0"
    assert url.params["_to"] == "49"
    assert "pokemon" in url.params["ft"]


def test_fetch_sends_client_headers() -> None:
    seen_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers)
        return httpx.Response(200, json=[], headers={})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": "browser-like-agent"},
    )
    adapter = VtexStoreAdapter("plaza_vea", BASE_URL, client)
    adapter.fetch("pokemon")

    assert seen_headers[0]["user-agent"] == "browser-like-agent"


def test_fetch_paginates_using_resources_header() -> None:
    requests_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["_from"])
        requests_seen.append(request.url.params["_from"])
        return httpx.Response(200, json=[], headers={"resources": f"{offset}-{offset + 49}/60"})

    adapter = VtexStoreAdapter("plaza_vea", BASE_URL, _client_with_handler(handler))
    payloads = adapter.fetch("pokemon")

    assert requests_seen == ["0", "50"]
    assert len(payloads) == 2


def test_fetch_paginates_using_content_range_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["_from"])
        return httpx.Response(
            200, json=[], headers={"content-range": f"products {offset}-{offset + 49}/1"}
        )

    adapter = VtexStoreAdapter("plaza_vea", BASE_URL, _client_with_handler(handler))
    payloads = adapter.fetch("pokemon")
    assert len(payloads) == 1


def test_fetch_stops_at_max_pages_even_if_total_is_huge() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        offset = int(request.url.params["_from"])
        return httpx.Response(200, json=[], headers={"resources": f"{offset}-{offset + 49}/999999"})

    adapter = VtexStoreAdapter("plaza_vea", BASE_URL, _client_with_handler(handler))
    payloads = adapter.fetch("pokemon")

    assert call_count == MAX_PAGES
    assert len(payloads) == MAX_PAGES


@pytest.mark.parametrize("status_code", [403, 500])
def test_fetch_http_error_status_raises_store_fetch_error(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="blocked")

    adapter = VtexStoreAdapter("plaza_vea", BASE_URL, _client_with_handler(handler))
    with pytest.raises(StoreFetchError):
        adapter.fetch("pokemon")


def test_fetch_timeout_raises_store_fetch_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    adapter = VtexStoreAdapter("plaza_vea", BASE_URL, _client_with_handler(handler))
    with pytest.raises(StoreFetchError):
        adapter.fetch("pokemon")
