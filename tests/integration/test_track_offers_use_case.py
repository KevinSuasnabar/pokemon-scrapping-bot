"""`TrackOffersUseCase` with fake in-memory adapters (one raising) — failure
isolation, two-run diff producing exactly the expected events, per-store
baseline labeling, diff isolation across stores."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tracker.application.dto import RawPayload, RunOutcome
from tracker.application.track_offers import TrackOffersUseCase
from tracker.domain.events import ChangeKind
from tracker.domain.model import Availability, Language, Money, Offer, ProductType
from tracker.infrastructure.persistence.sqlite_offer_repository import SqliteOfferRepository

RUN1 = datetime(2026, 1, 15, tzinfo=UTC)
RUN2 = RUN1 + timedelta(days=1)
RUN3 = RUN1 + timedelta(days=2)


class _NullReporter:
    def report(self, outcome: RunOutcome) -> None:
        pass

    def report_listing(self, entries) -> None:
        pass


class _StepClock:
    def __init__(self, instants: list[datetime]) -> None:
        self._instants = list(instants)

    def now(self) -> datetime:
        return self._instants.pop(0)


class _FakeAdapter:
    """Returns whatever offers list it's constructed with for every query,
    or raises `StoreFetchError`-equivalent if configured to fail."""

    def __init__(self, store_slug: str, offers: list[Offer] | None = None, *, fail: bool = False) -> None:
        self.store_slug = store_slug
        self._offers = offers or []
        self._fail = fail

    def fetch(self, query: str) -> Sequence[RawPayload]:
        if self._fail:
            raise RuntimeError(f"{self.store_slug}: simulated fetch failure")
        return [
            RawPayload(
                store=self.store_slug,
                source_url="fake://",
                content_type="application/json",
                body="[]",
                fetched_at=RUN1,
            )
        ]

    def parse(self, payloads: Sequence[RawPayload], observed_at: datetime) -> list[Offer]:
        return [
            Offer(
                store=o.store,
                external_id=o.external_id,
                title=o.title,
                url=o.url,
                price=o.price,
                availability=o.availability,
                language=o.language,
                product_type=o.product_type,
                observed_at=observed_at,
            )
            for o in self._offers
        ]

    def search(self, query: str, observed_at: datetime) -> list[Offer]:
        return self.parse(self.fetch(query), observed_at)


def _target_offer(
    store: str, external_id: str, *, price: str = "99.90", availability: Availability = Availability.IN_STOCK
) -> Offer:
    return Offer(
        store=store,
        external_id=external_id,
        title="Pokemon TCG 30 Aniversario ETB En Ingles",
        url=f"https://example.test/{store}/{external_id}",
        price=Money(amount=Decimal(price)),
        availability=availability,
        language=Language.ENGLISH,
        product_type=ProductType.ETB,
        observed_at=RUN1,
    )


def test_failure_isolation_one_store_raises_others_still_complete(tmp_db: sqlite3.Connection) -> None:
    repo = SqliteOfferRepository(tmp_db)
    adapters = [
        _FakeAdapter("plaza_vea", [_target_offer("plaza_vea", "p1")]),
        _FakeAdapter("ripley", fail=True),
    ]
    use_case = TrackOffersUseCase(adapters, repo, _NullReporter(), _StepClock([RUN1]))

    outcome = use_case.execute(["pokemon 30 aniversario"])

    statuses = {r.store: r.status for r in outcome.results}
    assert statuses == {"plaza_vea": "ok", "ripley": "failed"}
    ripley_result = next(r for r in outcome.results if r.store == "ripley")
    assert ripley_result.error is not None
    assert "simulated fetch failure" in ripley_result.error

    # Plaza Vea's data must actually be persisted despite Ripley's failure.
    assert repo.last_known("plaza_vea") != {}
    assert repo.last_known("ripley") == {}


def test_two_run_diff_produces_expected_events(tmp_db: sqlite3.Connection) -> None:
    repo = SqliteOfferRepository(tmp_db)

    # Run 1: baseline (store has no prior history).
    adapters_run1 = [_FakeAdapter("plaza_vea", [_target_offer("plaza_vea", "p1", price="100.00")])]
    use_case1 = TrackOffersUseCase(adapters_run1, repo, _NullReporter(), _StepClock([RUN1]))
    outcome1 = use_case1.execute(["pokemon 30 aniversario"])
    assert outcome1.results[0].events[0].kind is ChangeKind.BASELINE

    # Run 2: price drop on p1, plus a brand-new p2.
    adapters_run2 = [
        _FakeAdapter(
            "plaza_vea",
            [
                _target_offer("plaza_vea", "p1", price="90.00"),
                _target_offer("plaza_vea", "p2", price="50.00"),
            ],
        )
    ]
    use_case2 = TrackOffersUseCase(adapters_run2, repo, _NullReporter(), _StepClock([RUN2]))
    outcome2 = use_case2.execute(["pokemon 30 aniversario"])

    events = outcome2.results[0].events
    kinds = {(e.kind, e.external_id) for e in events}
    assert kinds == {(ChangeKind.PRICE_DROP, "p1"), (ChangeKind.NEW, "p2")}


def test_per_store_baseline_labeling_ripley_baseline_alongside_plaza_vea_new(
    tmp_db: sqlite3.Connection,
) -> None:
    repo = SqliteOfferRepository(tmp_db)

    # Plaza Vea already has established history from a prior run.
    seed_use_case = TrackOffersUseCase(
        [_FakeAdapter("plaza_vea", [_target_offer("plaza_vea", "existing")])],
        repo,
        _NullReporter(),
        _StepClock([RUN1]),
    )
    seed_use_case.execute(["pokemon 30 aniversario"])

    # Next run: Plaza Vea finds a genuinely new product; Ripley's first-ever
    # successful run finds one offer (its own baseline).
    adapters = [
        _FakeAdapter("plaza_vea", [_target_offer("plaza_vea", "pv-new")]),
        _FakeAdapter("ripley", [_target_offer("ripley", "r1")]),
    ]
    use_case = TrackOffersUseCase(adapters, repo, _NullReporter(), _StepClock([RUN2]))
    outcome = use_case.execute(["pokemon 30 aniversario"])

    plaza_vea_result = next(r for r in outcome.results if r.store == "plaza_vea")
    ripley_result = next(r for r in outcome.results if r.store == "ripley")

    assert plaza_vea_result.events[0].kind is ChangeKind.NEW
    assert ripley_result.events[0].kind is ChangeKind.BASELINE
    assert ripley_result.store_has_history is False
    assert plaza_vea_result.store_has_history is True


def test_diff_isolation_across_stores_one_failure_does_not_skew_another(
    tmp_db: sqlite3.Connection,
) -> None:
    repo = SqliteOfferRepository(tmp_db)

    # Seed both stores with history.
    seed = TrackOffersUseCase(
        [
            _FakeAdapter("plaza_vea", [_target_offer("plaza_vea", "p1", price="100.00")]),
            _FakeAdapter("ripley", [_target_offer("ripley", "r1", price="50.00")]),
        ],
        repo,
        _NullReporter(),
        _StepClock([RUN1]),
    )
    seed.execute(["pokemon 30 aniversario"])

    # Ripley fails this run; Plaza Vea has a genuine price drop.
    adapters = [
        _FakeAdapter("plaza_vea", [_target_offer("plaza_vea", "p1", price="90.00")]),
        _FakeAdapter("ripley", fail=True),
    ]
    use_case = TrackOffersUseCase(adapters, repo, _NullReporter(), _StepClock([RUN2]))
    outcome = use_case.execute(["pokemon 30 aniversario"])

    plaza_vea_result = next(r for r in outcome.results if r.store == "plaza_vea")
    ripley_result = next(r for r in outcome.results if r.store == "ripley")

    assert plaza_vea_result.events[0].kind is ChangeKind.PRICE_DROP
    assert ripley_result.status == "failed"
    assert ripley_result.events == []
    # Ripley's last-known state from the successful seed run is untouched.
    assert repo.last_known("ripley")["r1"].price.amount == Decimal("50.00")


def test_store_outage_recovery_does_not_fabricate_false_new_or_restocked(
    tmp_db: sqlite3.Connection,
) -> None:
    """Regression (sdd-verify WARNING 1): offer-change-detection's "Last-Known-
    State Diff Baseline" scenario — a store outage on run N+1 must not make
    run N+2's diff baseline anything other than run N's last successful
    observation, so recovery reports no false NEW/RESTOCKED for an unchanged
    product. Previously only verified manually at runtime, not as a checked-in
    test."""
    repo = SqliteOfferRepository(tmp_db)
    unchanged = _target_offer("ripley", "r1", price="50.00", availability=Availability.IN_STOCK)

    # Run 1 (RUN1): Ripley succeeds, product observed in stock at 50.00.
    run1 = TrackOffersUseCase(
        [_FakeAdapter("ripley", [unchanged])], repo, _NullReporter(), _StepClock([RUN1])
    )
    outcome1 = run1.execute(["pokemon 30 aniversario"])
    assert outcome1.results[0].events[0].kind is ChangeKind.BASELINE

    # Run 2 (RUN2): Ripley fails entirely — no observation recorded.
    run2 = TrackOffersUseCase(
        [_FakeAdapter("ripley", fail=True)], repo, _NullReporter(), _StepClock([RUN2])
    )
    outcome2 = run2.execute(["pokemon 30 aniversario"])
    assert outcome2.results[0].status == "failed"
    assert repo.last_known("ripley")["r1"].price.amount == Decimal("50.00")  # untouched

    # Run 3 (RUN3): Ripley recovers, same product, unchanged price/stock.
    run3 = TrackOffersUseCase(
        [_FakeAdapter("ripley", [unchanged])], repo, _NullReporter(), _StepClock([RUN3])
    )
    outcome3 = run3.execute(["pokemon 30 aniversario"])

    ripley_result3 = outcome3.results[0]
    assert ripley_result3.status == "ok"
    # The diff baseline is run 1's observation (last-known), not "no baseline"
    # — so an unchanged product must produce NO events at all on recovery.
    assert ripley_result3.events == []
    assert ripley_result3.store_has_history is True


def test_dedupe_by_key_across_multiple_queries(tmp_db: sqlite3.Connection) -> None:
    repo = SqliteOfferRepository(tmp_db)
    # Same adapter instance's offers list simulates the same product turning
    # up from two different query strings.
    adapter = _FakeAdapter("plaza_vea", [_target_offer("plaza_vea", "p1")])
    use_case = TrackOffersUseCase([adapter], repo, _NullReporter(), _StepClock([RUN1]))

    outcome = use_case.execute(["pokemon 30 aniversario", "pokemon 30th anniversary"])

    assert outcome.results[0].offer_count == 1  # not double-counted
