"""`Reporter` that fans out to several other `Reporter`s (e.g. console +
Telegram at once). Each sub-reporter's failure is isolated from the others —
same failure-isolation principle as `TrackOffersUseCase`'s per-store
try/except (design decision #9): one broken notification channel must never
suppress the console output or crash an unattended `--interval` loop.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from tracker.application.dto import CurrentListingEntry, RunOutcome
from tracker.application.ports import Reporter


class CompositeReporter:
    """Implements `application.ports.Reporter`."""

    def __init__(self, reporters: Sequence[Reporter]) -> None:
        self._reporters = reporters

    def report(self, outcome: RunOutcome) -> None:
        for reporter in self._reporters:
            try:
                reporter.report(outcome)
            except Exception as exc:  # noqa: BLE001 — one channel's failure must not affect others
                print(f"{type(reporter).__name__} failed (continuing): {exc}", file=sys.stderr)

    def report_listing(self, entries: list[CurrentListingEntry]) -> None:
        for reporter in self._reporters:
            try:
                reporter.report_listing(entries)
            except Exception as exc:  # noqa: BLE001
                print(f"{type(reporter).__name__} failed (continuing): {exc}", file=sys.stderr)
