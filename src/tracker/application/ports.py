"""Hexagonal ports. `typing.Protocol` (structural) per design decision #1:
adapters and fakes need no base class, and stay importable without domain
coupling."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

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
        """Newest observation per `external_id`, keyed by `external_id`."""
        ...

    def record_observations(self, run_id: int, offers: Sequence[Offer]) -> None:
        """Append-only insert, own transaction — see design's failure-isolation
        decision (#9)."""
        ...

    def price_history(
        self, store: str, external_id: str, limit: int = 50
    ) -> list[ObservationSnapshot]: ...

    def current_listing(self, store: str | None = None) -> list[CurrentListingEntry]:
        """Latest observation per (store, product), across all stores or one
        `store` if given — backs the offer-console-report "Current Matching
        Listing View" requirement, independent of any particular run's events.
        """
        ...


class Reporter(Protocol):
    def report(self, outcome: RunOutcome) -> None: ...

    def report_listing(self, entries: list[CurrentListingEntry]) -> None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
