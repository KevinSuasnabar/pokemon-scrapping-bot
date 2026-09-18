"""Composition-root-facing use case: run every adapter, diff, persist, report.

Per-store `try/except` isolation with each store's write in its own
repository transaction (design decision #9): one store's exception can
neither abort the run nor roll back another store's already-written data.
"""

from __future__ import annotations

from collections.abc import Sequence

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

        for adapter in self._adapters:
            try:
                fetched: list[Offer] = []
                for query in queries:
                    payloads = adapter.fetch(query)
                    fetched.extend(adapter.parse(payloads, now))

                matched = _dedupe_by_key([offer for offer in fetched if is_target_offer(offer)])

                # Ordering invariant (design.md "Data Flow"): last_known/has_history
                # MUST be read before this run's observations are written for this
                # store, or every product would diff against itself.
                previous = self._repository.last_known(adapter.store_slug)
                store_has_history = self._repository.has_history(adapter.store_slug)

                events = ChangeDetector.detect(previous, matched, store_has_history)

                self._repository.record_observations(run_id, matched)
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
