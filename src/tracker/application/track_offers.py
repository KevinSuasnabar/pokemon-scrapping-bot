"""Composition-root-facing use case: run every adapter, diff, persist, report.

Per-store `try/except` isolation with each store's write in its own
repository transaction (design decision #9): one store's exception can
neither abort the run nor roll back another store's already-written data.

Fetching (network-bound: each store's `adapter.search()`, including Ripley/
Falabella's extra per-product confirmation requests) runs concurrently across
stores in a thread pool — `httpx.Client` is documented safe to share across
threads for concurrent requests. Persistence (`last_known`/`has_history`/
`save_current_state`/`record_store_run`, all against one shared
`sqlite3.Connection`) stays strictly sequential in the main thread: SQLite
connections are not safe to use concurrently from multiple threads, and the
per-store read-before-write ordering invariant only needs to hold within
each store's own sequential turn, not across stores — so a global sequential
persistence phase after a concurrent fetch phase satisfies both constraints
without changing behavior, only how long it takes to get there.
"""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from tracker.application.dto import RunOutcome, StoreResult
from tracker.application.ports import Clock, OfferRepository, Reporter, StoreAdapter
from tracker.domain.change_detection import ChangeDetector
from tracker.domain.matching import is_target_offer
from tracker.domain.model import Offer, OfferKey


def _dedupe_by_key(offers: list[Offer]) -> list[Offer]:
    """Later occurrences win — a later query's fresher fetch overwrites an
    earlier one for the same (store, product)."""
    deduped: dict[OfferKey, Offer] = {}
    for offer in offers:
        deduped[offer.key] = offer
    return list(deduped.values())


def _fetch_offers(adapter: StoreAdapter, queries: Sequence[str], now: datetime) -> list[Offer]:
    """Runs in a worker thread. `adapter.search()` is each adapter's own
    `fetch()`+`parse()` composition (StoreAdapter port:
    `search = parse(fetch(...))`) — calling it here rather than fetch+parse
    directly matters now that Ripley/Falabella's `search()` adds a real-stock
    confirmation step after parsing; manually recomposing fetch+parse in the
    use case would silently skip that step. Any exception propagates to the
    caller via the `Future` — the thread pool doesn't swallow it, the main
    thread's per-store try/except (in `execute()`) still does."""
    fetched: list[Offer] = []
    for query in queries:
        fetched.extend(adapter.search(query, now))
    return fetched


class TrackOffersUseCase:
    def __init__(
        self,
        adapters: Sequence[StoreAdapter],
        repository: OfferRepository,
        reporter: Reporter,
        clock: Clock,
    ) -> None:
        self._adapters = adapters
        self._repository = repository
        self._reporter = reporter
        self._clock = clock

    def execute(self, queries: Sequence[str]) -> RunOutcome:
        now = self._clock.now()
        run_id = self._repository.start_run(now)
        results: list[StoreResult] = []

        # --- Concurrent phase: network-bound fetching only, no persistence. ---
        fetch_results: dict[str, list[Offer] | Exception] = {}
        if self._adapters:
            with ThreadPoolExecutor(max_workers=len(self._adapters)) as executor:
                future_to_slug = {
                    executor.submit(_fetch_offers, adapter, queries, now): adapter.store_slug
                    for adapter in self._adapters
                }
                for future in as_completed(future_to_slug):
                    slug = future_to_slug[future]
                    try:
                        fetch_results[slug] = future.result()
                    except Exception as exc:  # noqa: BLE001 — per-store isolation boundary
                        fetch_results[slug] = exc

        # --- Sequential phase: diff + persist, in adapter order, one connection. ---
        for adapter in self._adapters:
            fetch_result = fetch_results[adapter.store_slug]
            try:
                if isinstance(fetch_result, Exception):
                    raise fetch_result

                matched = _dedupe_by_key([offer for offer in fetch_result if is_target_offer(offer)])

                # Ordering invariant (design.md "Data Flow"): last_known/has_history
                # MUST be read before this run's observations are written for this
                # store, or every product would diff against itself.
                previous = self._repository.last_known(adapter.store_slug)
                store_has_history = self._repository.has_history(adapter.store_slug)

                events = ChangeDetector.detect(previous, matched, store_has_history)

                self._repository.save_current_state(run_id, matched)
                self._repository.record_store_run(
                    run_id, adapter.store_slug, "ok", len(matched), None
                )

                results.append(
                    StoreResult(
                        store=adapter.store_slug,
                        status="ok",
                        offer_count=len(matched),
                        events=events,
                        error=None,
                        store_has_history=store_has_history,
                    )
                )
            except Exception as exc:  # noqa: BLE001 — per-store isolation boundary
                self._repository.record_store_run(run_id, adapter.store_slug, "failed", 0, str(exc))
                results.append(
                    StoreResult(
                        store=adapter.store_slug,
                        status="failed",
                        offer_count=0,
                        events=[],
                        error=str(exc),
                        store_has_history=False,
                    )
                )

        overall_status = "failed" if all(r.status == "failed" for r in results) else "completed"
        self._repository.finish_run(run_id, overall_status)

        outcome = RunOutcome(run_id=run_id, started_at=now, results=results)
        self._reporter.report(outcome)
        return outcome
