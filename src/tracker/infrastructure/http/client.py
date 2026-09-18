"""`httpx.Client` factory: browser-like headers, timeout, and a connection-level
retry policy. Shared by every non-Playwright adapter (design's "HTTP client")."""

from __future__ import annotations

import httpx

DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=10.0)

DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
    "Accept-Language": "es-PE,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}


def build_http_client(
    *, timeout: httpx.Timeout | float = DEFAULT_TIMEOUT, retries: int = 2
) -> httpx.Client:
    """A pre-configured `httpx.Client`. Callers own its lifecycle (composition
    root closes it); adapters only ever receive an already-built client."""
    transport = httpx.HTTPTransport(retries=retries)
    return httpx.Client(
        headers=DEFAULT_HEADERS,
        timeout=timeout,
        transport=transport,
        follow_redirects=True,
    )
