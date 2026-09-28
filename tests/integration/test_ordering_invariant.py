"""**Load-bearing regression test** (design.md "Data Flow" / tasks.md 7.2).

Asserts `repo.last_known()` / `repo.has_history()` are read for a store
BEFORE `repo.save_current_state()` writes that store's current-run data.
A spy repository fails the test if a read happens after the write for the
same store — without this ordering, every product would diff against
itself and NEW/RESTOCKED/PRICE_DROP would never fire.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tracker.application.dto import RawPayload, RunOutcome
from tracker.application.track_offers import TrackOffersUseCase
from tracker.domain.model import Availability, Language, Money, Offer, ProductType

NOW = datetime(2026, 1, 15, tzinfo=UTC)


class OrderingViolation(AssertionError):
    pass


@dataclass
class OrderTrackingRepository:
    """Fails the moment a store's read (`last_known`/`has_history`) happens
    after that same store's `save_current_state` write."""

    written_stores: set[str] = field(default_factory=set)
    read_log: list[str] = field(default_factory=list)
    write_log: list[str] = field(default_factory=list)
    _run_counter: int = 0

    def start_run(self, started_at: datetime) -> int:
        self._run_counter += 1
        return self._run_counter

    def finish_run(self, run_id: int, status: str) -> None:
        pass

    def record_store_run(self, run_id, store, status, offer_count, error) -> None:
        pass

    def has_history(self, store: str) -> bool:
        self.read_log.append(store)
        if store in self.written_stores:
            raise OrderingViolation(
                f"has_history({store!r}) called AFTER save_current_state({store!r}) — "
                "every product would diff against itself."
            )
        return False

    def last_known(self, store: str):
        self.read_log.append(store)
        if store in self.written_stores:
            raise OrderingViolation(
                f"last_known({store!r}) called AFTER save_current_state({store!r}) — "
                "every product would diff against itself."
            )
        return {}

    def save_current_state(self, run_id: int, offers) -> None:
        for offer in offers:
            self.written_stores.add(offer.store)
            self.write_log.append(offer.store)

    def current_listing(self, store: str | None = None):
        return []


class _NullReporter:
    def report(self, outcome: RunOutcome) -> None:
        pass

    def report_listing(self, entries) -> None:
        pass


class _FrozenClock:
    def now(self) -> datetime:
        return NOW


class _FakeAdapter:
    def __init__(self, store_slug: str) -> None:
        self.store_slug = store_slug

    def fetch(self, query: str) -> Sequence[RawPayload]:
        return [
            RawPayload(
                store=self.store_slug,
                source_url="fake://",
                content_type="application/json",
                body="[]",
                fetched_at=NOW,
            )
        ]

    def parse(self, payloads, observed_at: datetime) -> list[Offer]:
        return [
            Offer(
                store=self.store_slug,
                external_id="p1",
                title="Pokemon TCG 30 Aniversario ETB En Ingles",
                url="https://example.test/p1",
                price=Money(amount=Decimal("99.90")),
                availability=Availability.IN_STOCK,
                language=Language.ENGLISH,
                product_type=ProductType.ETB,
                observed_at=observed_at,
            )
        ]

    def search(self, query: str, observed_at: datetime) -> list[Offer]:
        return self.parse(self.fetch(query), observed_at)


def test_last_known_and_has_history_read_before_save_current_state_write() -> None:
    repo = OrderTrackingRepository()
    use_case = TrackOffersUseCase(
        adapters=[_FakeAdapter("plaza_vea")],
        repository=repo,
        reporter=_NullReporter(),
        clock=_FrozenClock(),
    )

    use_case.execute(["pokemon 30 aniversario"])  # must not raise OrderingViolation

    assert repo.read_log == ["plaza_vea", "plaza_vea"]  # has_history + last_known (order-agnostic between the two)
    assert repo.write_log == ["plaza_vea"]


def test_ordering_violation_is_actually_detected_by_the_spy() -> None:
    """Proves the spy is load-bearing: a deliberately-wrong repository that
    writes before reading DOES fail the check."""
    repo = OrderTrackingRepository()
    repo.written_stores.add("plaza_vea")  # simulate "already written"

    with pytest.raises(OrderingViolation):
        repo.has_history("plaza_vea")

    with pytest.raises(OrderingViolation):
        repo.last_known("plaza_vea")


def test_ordering_holds_independently_per_store_across_multiple_stores() -> None:
    repo = OrderTrackingRepository()
    use_case = TrackOffersUseCase(
        adapters=[_FakeAdapter("plaza_vea"), _FakeAdapter("ripley")],
        repository=repo,
        reporter=_NullReporter(),
        clock=_FrozenClock(),
    )

    use_case.execute(["pokemon 30 aniversario"])  # must not raise for either store

    assert repo.written_stores == {"plaza_vea", "ripley"}
