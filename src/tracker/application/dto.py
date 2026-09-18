"""Data transfer objects crossing the ports (adapters <-> use case <-> repository)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from tracker.domain.events import ChangeEvent
from tracker.domain.model import Availability, Money


@dataclass(frozen=True, slots=True)
class RawPayload:
    """One fetched-but-unparsed response. `fetch()` returns a *sequence* of these
    so VTEX pagination and Ilahui's JSON/HTML fallback stay inside the adapter
    without leaking page state to the use case."""

    store: str
    source_url: str
    content_type: str
    body: str
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class ObservationSnapshot:
    """A single (store, product)'s observation, as returned by
    `OfferRepository.last_known()` / `.price_history()`."""

    external_id: str
    price: Money | None
    availability: Availability
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class StoreResult:
    """Outcome of running one store's adapter for one run."""

    store: str
    status: str  # "ok" | "failed"
    offer_count: int
    events: list[ChangeEvent] = field(default_factory=list)
    error: str | None = None
    store_has_history: bool = True


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """Everything the reporter needs to render one run."""

    run_id: int
    started_at: datetime
    results: list[StoreResult]


@dataclass(frozen=True, slots=True)
class CurrentListingEntry:
    """One row of the "current matching listing" view (offer-console-report's
    Current Matching Listing View requirement) — latest observation per
    (store, product), independent of any particular run's event list."""

    store: str
    external_id: str
    title: str
    url: str
    price: Money | None
    availability: Availability
    observed_at: datetime
