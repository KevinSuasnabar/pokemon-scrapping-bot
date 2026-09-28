"""`IlahuiAdapter.fetch()` — mandatory HTML fallback behavior.

Found via live smoke-testing: `suggest.json` can answer 200 with a non-JSON
body (themed error page, redirect, etc.), which is not an `httpx.HTTPError`
and must still trigger the HTML fallback per design.md ("the HTML fallback
path is mandatory, not optional").
"""

from __future__ import annotations

import httpx
import pytest

from tracker.adapters.stores.ilahui import BASE_URL, IlahuiAdapter
from tracker.domain.errors import StoreFetchError


@pytest.fixture(autouse=True)
def _no_real_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """`get_with_retry` (infrastructure/http/client.py) sleeps between
    attempts in real usage — tests exercising an error path must not
    actually wait for it."""
    monkeypatch.setattr("tracker.infrastructure.http.client.time.sleep", lambda seconds: None)


def _adapter_with_handler(handler) -> IlahuiAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return IlahuiAdapter(client, BASE_URL)


def test_fetch_uses_suggest_json_when_it_returns_valid_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "suggest.json" in str(request.url)
        return httpx.Response(200, json={"resources": {"results": {"products": []}}})

    payloads = _adapter_with_handler(handler).fetch("pokemon")
    assert len(payloads) == 1
    assert payloads[0].content_type == "application/json"


def test_fetch_falls_back_to_html_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "suggest.json" in str(request.url):
            return httpx.Response(404, text="not found")
        return httpx.Response(200, text="<html></html>")

    payloads = _adapter_with_handler(handler).fetch("pokemon")
    assert len(payloads) == 1
    assert payloads[0].content_type == "text/html"


def test_fetch_falls_back_to_html_when_suggest_json_returns_200_with_non_json_body() -> None:
    """Regression: a 200 status is not proof of a real JSON endpoint."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "suggest.json" in str(request.url):
            return httpx.Response(200, text="<!doctype html><html>not json</html>")
        return httpx.Response(200, text="<html>real search results</html>")

    payloads = _adapter_with_handler(handler).fetch("pokemon")
    assert len(payloads) == 1
    assert payloads[0].content_type == "text/html"
    assert "real search results" in payloads[0].body


def test_fetch_raises_when_both_suggest_json_and_html_fail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    with pytest.raises(StoreFetchError):
        _adapter_with_handler(handler).fetch("pokemon")


def test_fetch_raises_when_suggest_json_invalid_and_html_also_invalid_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "suggest.json" in str(request.url):
            return httpx.Response(200, text="not json")
        return httpx.Response(503, text="unavailable")

    with pytest.raises(StoreFetchError):
        _adapter_with_handler(handler).fetch("pokemon")
