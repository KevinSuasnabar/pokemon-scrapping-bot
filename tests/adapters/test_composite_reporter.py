"""`CompositeReporter` — fans out to every sub-reporter, one's failure never
suppresses or blocks the others."""

from __future__ import annotations

from datetime import UTC, datetime

from tracker.adapters.reporting.composite import CompositeReporter
from tracker.application.dto import RunOutcome

NOW = datetime(2026, 1, 15, tzinfo=UTC)
OUTCOME = RunOutcome(run_id=1, started_at=NOW, results=[])


class _RecordingReporter:
    def __init__(self) -> None:
        self.reported: list[RunOutcome] = []
        self.listed: list[list] = []

    def report(self, outcome: RunOutcome) -> None:
        self.reported.append(outcome)

    def report_listing(self, entries: list) -> None:
        self.listed.append(entries)


class _BrokenReporter:
    def report(self, outcome: RunOutcome) -> None:
        raise RuntimeError("simulated reporter failure")

    def report_listing(self, entries: list) -> None:
        raise RuntimeError("simulated reporter failure")


def test_report_fans_out_to_every_sub_reporter() -> None:
    a, b = _RecordingReporter(), _RecordingReporter()
    CompositeReporter([a, b]).report(OUTCOME)
    assert a.reported == [OUTCOME]
    assert b.reported == [OUTCOME]


def test_report_listing_fans_out_to_every_sub_reporter() -> None:
    a, b = _RecordingReporter(), _RecordingReporter()
    CompositeReporter([a, b]).report_listing([])
    assert a.listed == [[]]
    assert b.listed == [[]]


def test_one_reporter_failing_does_not_stop_the_others(capsys) -> None:
    broken = _BrokenReporter()
    healthy = _RecordingReporter()
    # Order matters for this test: the broken one must run first to prove a
    # failure doesn't short-circuit the loop.
    CompositeReporter([broken, healthy]).report(OUTCOME)

    assert healthy.reported == [OUTCOME]
    assert "simulated reporter failure" in capsys.readouterr().err


def test_one_reporter_failing_on_listing_does_not_stop_the_others(capsys) -> None:
    broken = _BrokenReporter()
    healthy = _RecordingReporter()
    CompositeReporter([broken, healthy]).report_listing([])

    assert healthy.listed == [[]]
    assert "simulated reporter failure" in capsys.readouterr().err
