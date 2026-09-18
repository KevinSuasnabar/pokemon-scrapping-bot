"""Ripley transport tests.

`HttpxTransport` is exercised via `httpx.MockTransport`. There is no
`PlaywrightTransport` test here because there is no `PlaywrightTransport`
class: the Task 0 spike (`scripts/spike_ripley.py`,
`tests/fixtures/ripley/spike_summary.json`) was unambiguous — every probe
returned 200 with zero bot-challenge markers — so design.md's decision rule
row 1 applies ("Any probe returns 200 -> HttpxTransport"), not the
"403 everywhere, no markers" ambiguous/geo-IP row that would have justified
building a browser-backed transport. See `adapters/stores/ripley.py`'s module
docstring for the full reasoning.
"""

from __future__ import annotations

import httpx
import pytest

from tracker.adapters.stores.ripley import STORE_SLUG, HttpxTransport
from tracker.domain.errors import StoreFetchError

URL = "https://simple.ripley.com.pe/search/pokemon-30-aniversario"


def test_httpx_transport_returns_raw_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>ok</html>", headers={"content-type": "text/html"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = HttpxTransport(client)

    payload = transport.fetch_html(URL)

    assert payload.store == STORE_SLUG
    assert payload.source_url == URL
    assert payload.body == "<html>ok</html>"
    assert payload.content_type == "text/html"


@pytest.mark.parametrize("status_code", [403, 500])
def test_httpx_transport_error_status_raises_store_fetch_error(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="blocked")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = HttpxTransport(client)

    with pytest.raises(StoreFetchError):
        transport.fetch_html(URL)


def test_httpx_transport_timeout_raises_store_fetch_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = HttpxTransport(client)

    with pytest.raises(StoreFetchError):
        transport.fetch_html(URL)


@pytest.mark.playwright
@pytest.mark.skip(
    reason=(
        "No PlaywrightTransport was built: the live Task 0 spike was unambiguous "
        "(200 on every probe, no challenge markers), so design.md's decision rule "
        "never reaches the branch that would require a browser-backed transport."
    )
)
def test_playwright_transport_not_applicable() -> None:
    pytest.skip("PlaywrightTransport intentionally not implemented — see module docstring.")
