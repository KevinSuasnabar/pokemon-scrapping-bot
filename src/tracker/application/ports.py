"""Hexagonal ports. `typing.Protocol` (structural) per design decision #1:
adapters and fakes need no base class, and stay importable without domain
coupling."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from tracker.application.dto import (
    CurrentListingEntry,
    ObservationSnapshot,
    RawPayload,
    RunOutcome,
)
from tracker.domain.model import Offer


class StoreAdapter(Protocol):
    store_slug: str

    def fetch(self, query: str) -> Sequence[RawPayload]:
        """Raises `StoreFetchError` on HTTP error, timeout, or transport failure."""
        ...

    def parse(self, payloads: Sequence[RawPayload], observed_at: datetime) -> list[Offer]:
        """Pure. Raises `StoreParseError` on malformed/unexpected payload shape."""
        ...

    def search(self, query: str, observed_at: datetime) -> list[Offer]:
        """`= parse(fetch(query), observed_at)`."""
        ...


@runtime_checkable
class SupportsKnownProducts(Protocol):
    """Optional capability, not part of `StoreAdapter` itself (design
    decision, 2026-09-29): only VTEX-backed stores, Ripley, and Tai Loy
    implement it so far — adding it to `StoreAdapter` would force every
    other adapter (Ilahui, Pharmax, Falabella) to grow a method it doesn't
    need. Callers check `isinstance(adapter, SupportsKnownProducts)`.

    Covers "ghost products": a product that is real, live, and purchasable
    by direct URL/SKU but isn't surfaced by the store's own search — confirmed
    live 2026-09-29 on Tai Loy (Magento search-index lag) and Ripley (a
    product absent from every tried search query). `fetch_known` results
    bypass `is_target_offer()`'s text heuristics entirely: a manually-curated
    identifier is already confirmed correct by the person who added it, so
    re-running the same word-matching that already missed it once is
    redundant at best.
    """

    def fetch_known(self, identifiers: Sequence[str], observed_at: datetime) -> list[Offer]:
        """One `Offer` per identifier that could be fetched and parsed; a
        single bad identifier is skipped, not fatal to the others."""
        ...


class OfferRepository(Protocol):
    def start_run(self, started_at: datetime) -> int: ...

    def finish_run(self, run_id: int, status: str) -> None: ...

    def record_store_run(
        self, run_id: int, store: str, status: str, offer_count: int, error: str | None
    ) -> None: ...

    def has_history(self, store: str) -> bool:
        """True iff `store` has at least one prior run with `status='ok'`."""
        ...

    def last_known(self, store: str) -> dict[str, ObservationSnapshot]:
        """Current known state per `external_id`, keyed by `external_id` —
        exactly one entry per product, never a history."""
        ...

    def save_current_state(self, run_id: int, offers: Sequence[Offer]) -> None:
        """Replaces (not appends) each offer's stored state — own
        transaction, see design's failure-isolation decision (#9). User
        decision, 2026-09-28: this project tracks "is it available now", not
        price history, so nothing is retained beyond the latest state per
        product."""
        ...

    def current_listing(self, store: str | None = None) -> list[CurrentListingEntry]:
        """Current state per (store, product), across all stores or one
        `store` if given — backs the offer-console-report "Current Matching
        Listing View" requirement, independent of any particular run's events.
        """
        ...


class Reporter(Protocol):
    def report(self, outcome: RunOutcome) -> None: ...

    def report_listing(self, entries: list[CurrentListingEntry]) -> None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
