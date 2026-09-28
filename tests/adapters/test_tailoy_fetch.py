"""`TaiLoyAdapter.fetch()` — `httpx.MockTransport`: URL construction and
error mapping. No pagination handling: Magento returned every match on one
page for our query volume (confirmed live, 25 results for "pokemon tcg 30"),
unlike VTEX's confirmed-paginated catalog."""

from __future__ import annotations

import httpx
import pytest

from tracker.adapters.stores.tailoy import TaiLoyAdapter
from tracker.domain.errors import StoreFetchError

BASE_URL = "https://www.tailoy.com.pe"


@pytest.fixture(autouse=True)
def _no_real_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tracker.infrastructure.http.client.time.sleep", lambda seconds: None)


def _client_with_handler(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_builds_the_catalogsearch_url() -> None:
    seen_urls: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(request.url)
        return httpx.Response(200, text="<html></html>")

    adapter = TaiLoyAdapter(_client_with_handler(handler), BASE_URL)
    adapter.fetch("pokemon tcg 30")

    assert len(seen_urls) == 1
    assert seen_urls[0].path == "/catalogsearch/result/"
    assert "pokemon" in seen_urls[0].params["q"]


def test_fetch_returns_one_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>real page</html>")

    adapter = TaiLoyAdapter(_client_with_handler(handler), BASE_URL)
    payloads = adapter.fetch("pokemon")

    assert len(payloads) == 1
    assert payloads[0].store == "tailoy"
    assert payloads[0].body == "<html>real page</html>"


@pytest.mark.parametrize("status_code", [403, 500])
def test_fetch_http_error_status_raises_store_fetch_error(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="blocked")

    adapter = TaiLoyAdapter(_client_with_handler(handler), BASE_URL)
    with pytest.raises(StoreFetchError):
        adapter.fetch("pokemon")


def test_fetch_timeout_raises_store_fetch_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    adapter = TaiLoyAdapter(_client_with_handler(handler), BASE_URL)
    with pytest.raises(StoreFetchError):
        adapter.fetch("pokemon")
