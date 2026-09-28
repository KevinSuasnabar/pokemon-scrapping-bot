"""`httpx.Client` factory: browser-like headers, timeout, and a connection-level
retry policy. Shared by every non-Playwright adapter (design's "HTTP client")."""

from __future__ import annotations

import time

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


def get_with_retry(
    client: httpx.Client, url: str, *, retries: int = 1, delay: float = 2.0
) -> httpx.Response:
    """`client.get(url)` + `raise_for_status()`, retried once on a non-2xx
    response.

    `httpx.HTTPTransport(retries=...)` (see `build_http_client` above) only
    retries a *connection-level* failure (DNS/connect/timeout) — it never
    retries a "successful" HTTP exchange that just came back with an error
    status. Real usage of this project (4 days of unattended `--interval`
    runs, 2026-09-18 to 2026-09-22) recorded 3 transient Ripley 404s that
    succeeded again seconds later on the identical URL, so this app-level
    retry covers a gap the transport-level one structurally cannot.
    """
    last_exc: httpx.HTTPError | None = None
    for attempt in range(retries + 1):
        try:
            response = client.get(url)
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(delay)
    assert last_exc is not None  # loop always runs at least once (retries >= 0)
    raise last_exc
