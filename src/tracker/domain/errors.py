"""Domain-level errors raised across the `StoreAdapter` port boundary.

Adapters MUST translate transport/parse failures into these so a bare
`httpx.HTTPError` (or any other library exception) never crosses the port —
see design.md's adapter table and the use case's per-store isolation.
"""

from __future__ import annotations


class StoreFetchError(Exception):
    """Raised by `StoreAdapter.fetch()` on HTTP error, timeout, or transport failure."""


class StoreParseError(Exception):
    """Raised by `StoreAdapter.parse()` on malformed/unexpected payload shape."""
