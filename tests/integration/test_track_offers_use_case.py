"""`TrackOffersUseCase` with fake in-memory adapters (one raising) — failure
isolation, two-run diff producing exactly the expected events, per-store
baseline labeling, diff isolation across stores."""

from __future__ import annotations

import sqlite3
import threading
import time
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


class _FakeAdapterWithKnownProducts(_FakeAdapter):
    """Adds `fetch_known` (`SupportsKnownProducts`) on top of `_FakeAdapter`
    — a plain `isinstance` check against the structural, `@runtime_checkable`
    protocol is enough for `TrackOffersUseCase` to pick it up, no formal
    subclassing needed in production code, but the fake needs the method to
    exist for the test to exercise the real branch."""

    def __init__(self, store_slug: str, offers: list[Offer], known_offers: list[Offer]) -> None:
        super().__init__(store_slug, offers)
        self._known_offers = known_offers

    def fetch_known(self, identifiers: Sequence[str], observed_at: datetime) -> list[Offer]:
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
            for o in self._known_offers
        ]


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


def test_execute_fetches_every_store_concurrently_not_sequentially(
    tmp_db: sqlite3.Connection,
) -> None:
    """Proves genuine concurrency, not just "still happens to work": each
    fake adapter blocks on a shared barrier until every other adapter has
    ALSO reached it. If execute() fetched stores one at a time (the old
    behavior), the first adapter's search() would hang forever waiting for
    adapters that never get a turn to run — this test would time out and
    fail rather than silently pass."""
    # Real seeded store slugs (tmp_db fixture calls seed_stores()) — a made-up
    # slug would fail record_store_run's FOREIGN KEY constraint.
    store_slugs = ["plaza_vea", "oechsle", "ripley", "ilahui"]
    barrier = threading.Barrier(len(store_slugs), timeout=2)

    class _BarrierAdapter:
        def __init__(self, store_slug: str) -> None:
            self.store_slug = store_slug

        def fetch(self, query: str):  # pragma: no cover - unused, search() is called directly
            return []

        def parse(self, payloads, observed_at):  # pragma: no cover
            return []

        def search(self, query: str, observed_at: datetime) -> list:
            barrier.wait()  # would deadlock/time out under sequential execution
            return []

    repo = SqliteOfferRepository(tmp_db)
    adapters = [_BarrierAdapter(slug) for slug in store_slugs]
    use_case = TrackOffersUseCase(adapters, repo, _NullReporter(), _StepClock([RUN1]))

    outcome = use_case.execute(["pokemon 30 aniversario"])  # must not hang or raise

    assert len(outcome.results) == len(store_slugs)
    assert all(r.status == "ok" for r in outcome.results)


def test_execute_results_preserve_adapter_order_regardless_of_fetch_completion_order(
    tmp_db: sqlite3.Connection,
) -> None:
    """The concurrent fetch phase completes in whatever order threads
    finish, but `outcome.results` must still be reported in the same order
    the adapters were configured — console/JSON output ordering must stay
    predictable regardless of which store's network call happened to
    return first."""
    class _SlowFirstAdapter:
        """The FIRST configured adapter is deliberately the SLOWEST to
        finish, so completion order is the reverse of configuration order —
        a real ordering bug would show up as results in completion order."""

        def __init__(self, store_slug: str, delay: float) -> None:
            self.store_slug = store_slug
            self._delay = delay

        def fetch(self, query: str):  # pragma: no cover
            return []

        def parse(self, payloads, observed_at):  # pragma: no cover
            return []

        def search(self, query: str, observed_at: datetime) -> list:
            time.sleep(self._delay)
            return []

    repo = SqliteOfferRepository(tmp_db)
    adapters = [
        _SlowFirstAdapter("plaza_vea", delay=0.05),
        _SlowFirstAdapter("oechsle", delay=0.0),
        _SlowFirstAdapter("ripley", delay=0.0),
    ]
    use_case = TrackOffersUseCase(adapters, repo, _NullReporter(), _StepClock([RUN1]))

    outcome = use_case.execute(["pokemon 30 aniversario"])

    assert [r.store for r in outcome.results] == ["plaza_vea", "oechsle", "ripley"]


def test_known_products_bypass_the_text_matching_filter(tmp_db: sqlite3.Connection) -> None:
    """The exact real-world case this exists for (Tai Loy's Sylveon box,
    confirmed live 2026-09-29): a title that would fail `is_target_offer()`
    (no "aniversario"/"anniversary"/"celebration" marker) must still be
    reported when it comes from `fetch_known` — a manually-curated
    identifier is already confirmed correct, re-running the filter that
    already missed it once would just miss it again."""
    repo = SqliteOfferRepository(tmp_db)
    ghost_product = Offer(
        store="tailoy",
        external_id="55969003",
        title="Caja Pokémon Tcg 30Th Sylveon Inglés",  # no anniversary marker
        url="https://www.tailoy.com.pe/x-55969003.html",
        price=Money(amount=Decimal("129.90")),
        availability=Availability.IN_STOCK,
        language=Language.ENGLISH,
        product_type=ProductType.COLLECTION_BOX,
        observed_at=RUN1,
    )
    adapter = _FakeAdapterWithKnownProducts("tailoy", offers=[], known_offers=[ghost_product])
    use_case = TrackOffersUseCase(
        [adapter], repo, _NullReporter(), _StepClock([RUN1]), known_products={"tailoy": ["some-url"]}
    )

    outcome = use_case.execute(["pokemon 30 aniversario"])

    assert outcome.results[0].offer_count == 1
    assert outcome.results[0].events[0].external_id == "55969003"
    assert repo.last_known("tailoy") != {}


def test_known_products_not_fetched_when_no_identifiers_configured(tmp_db: sqlite3.Connection) -> None:
    repo = SqliteOfferRepository(tmp_db)
    ghost_product = _target_offer("tailoy", "ghost")
    adapter = _FakeAdapterWithKnownProducts("tailoy", offers=[], known_offers=[ghost_product])
    # No `known_products` entry for "tailoy" at all — fetch_known must not run.
    use_case = TrackOffersUseCase([adapter], repo, _NullReporter(), _StepClock([RUN1]))

    outcome = use_case.execute(["pokemon 30 aniversario"])

    assert outcome.results[0].offer_count == 0


def test_known_products_ignored_for_adapters_that_do_not_support_them(
    tmp_db: sqlite3.Connection,
) -> None:
    """A plain `_FakeAdapter` (no `fetch_known`) must not error out even if
    `known_products` has an entry for its slug — `isinstance` against the
    optional protocol just skips it."""
    repo = SqliteOfferRepository(tmp_db)
    adapter = _FakeAdapter("plaza_vea", [_target_offer("plaza_vea", "p1")])
    use_case = TrackOffersUseCase(
        [adapter], repo, _NullReporter(), _StepClock([RUN1]), known_products={"plaza_vea": ["999"]}
    )

    outcome = use_case.execute(["pokemon 30 aniversario"])  # must not raise

    assert outcome.results[0].status == "ok"
    assert outcome.results[0].offer_count == 1


def test_known_products_dedupe_against_a_search_result_for_the_same_product(
    tmp_db: sqlite3.Connection,
) -> None:
    repo = SqliteOfferRepository(tmp_db)
    searched = _target_offer("ripley", "shared-id", price="100.00")
    known = _target_offer("ripley", "shared-id", price="90.00")
    adapter = _FakeAdapterWithKnownProducts("ripley", offers=[searched], known_offers=[known])
    use_case = TrackOffersUseCase(
        [adapter], repo, _NullReporter(), _StepClock([RUN1]), known_products={"ripley": ["shared-id"]}
    )

    outcome = use_case.execute(["pokemon 30 aniversario"])

    assert outcome.results[0].offer_count == 1  # not counted twice
