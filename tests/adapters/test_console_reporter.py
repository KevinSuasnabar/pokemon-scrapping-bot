"""`ConsoleReporter` via `capsys` — covers every offer-console-report scenario."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from tracker.adapters.reporting.console import _HIGHLIGHT, _RESET, ConsoleReporter
from tracker.application.dto import CurrentListingEntry, RunOutcome, StoreResult
from tracker.domain.events import ChangeEvent, ChangeKind
from tracker.domain.model import Availability, Money

NOW = datetime(2026, 1, 15, tzinfo=UTC)


def test_mixed_events_are_all_shown(capsys) -> None:
    events = [
        ChangeEvent(
            kind=ChangeKind.NEW,
            store="plaza_vea",
            external_id="1",
            title="New ETB",
            url="https://x/1",
            current_price=Money(amount=Decimal("99.90")),
            current_availability=Availability.IN_STOCK,
        ),
        ChangeEvent(
            kind=ChangeKind.RESTOCKED,
            store="oechsle",
            external_id="2",
            title="Restocked Box",
            url="https://x/2",
            previous_availability=Availability.OUT_OF_STOCK,
            current_availability=Availability.IN_STOCK,
        ),
        ChangeEvent(
            kind=ChangeKind.PRICE_DROP,
            store="ilahui",
            external_id="3",
            title="Cheaper Pack",
            url="https://x/3",
            previous_price=Money(amount=Decimal("100.00")),
            current_price=Money(amount=Decimal("90.00")),
        ),
    ]
    outcome = RunOutcome(
        run_id=1,
        started_at=NOW,
        results=[StoreResult(store="mixed", status="ok", offer_count=3, events=events)],
    )
    ConsoleReporter().report(outcome)
    out = capsys.readouterr().out
    assert "NEW: New ETB" in out
    assert "RESTOCKED: Restocked Box" in out
    assert "PRICE_DROP: Cheaper Pack" in out


def test_no_events_explicitly_states_no_changes(capsys) -> None:
    outcome = RunOutcome(
        run_id=2,
        started_at=NOW,
        results=[
            StoreResult(store="plaza_vea", status="ok", offer_count=0, events=[]),
            StoreResult(store="oechsle", status="ok", offer_count=0, events=[]),
        ],
    )
    ConsoleReporter().report(outcome)
    out = capsys.readouterr().out
    assert "No changes detected this run." in out


def test_events_and_baseline_lines_include_the_product_url(capsys) -> None:
    new_event = ChangeEvent(
        kind=ChangeKind.NEW,
        store="plaza_vea",
        external_id="1",
        title="New ETB",
        url="https://www.plazavea.com.pe/new-etb/p",
        current_price=Money(amount=Decimal("99.90")),
        current_availability=Availability.IN_STOCK,
    )
    baseline_event = ChangeEvent(
        kind=ChangeKind.BASELINE,
        store="ilahui",
        external_id="2",
        title="Baseline Box",
        url="https://ilahuiperu.com/products/baseline-box",
        current_price=Money(amount=Decimal("129.90")),
        current_availability=Availability.IN_STOCK,
    )
    outcome = RunOutcome(
        run_id=8,
        started_at=NOW,
        results=[
            StoreResult(store="plaza_vea", status="ok", offer_count=1, events=[new_event]),
            StoreResult(
                store="ilahui", status="ok", offer_count=1, events=[baseline_event], store_has_history=False
            ),
        ],
    )
    ConsoleReporter().report(outcome)
    out = capsys.readouterr().out
    assert "https://www.plazavea.com.pe/new-etb/p" in out
    assert "https://ilahuiperu.com/products/baseline-box" in out


def test_first_run_baseline_is_labeled_not_as_new(capsys) -> None:
    events = [
        ChangeEvent(
            kind=ChangeKind.BASELINE,
            store="plaza_vea",
            external_id="1",
            title="Baseline offer",
            url="https://x/1",
            current_price=Money(amount=Decimal("50.00")),
            current_availability=Availability.IN_STOCK,
        )
    ]
    outcome = RunOutcome(
        run_id=3,
        started_at=NOW,
        results=[StoreResult(store="plaza_vea", status="ok", offer_count=1, events=events, store_has_history=False)],
    )
    ConsoleReporter().report(outcome)
    out = capsys.readouterr().out
    assert "initial baseline" in out
    assert "NEW:" not in out


def test_one_store_baseline_alongside_another_store_new_not_conflated(capsys) -> None:
    ripley_baseline = ChangeEvent(
        kind=ChangeKind.BASELINE,
        store="ripley",
        external_id="r1",
        title="Ripley offer",
        url="https://x/r1",
        current_price=Money(amount=Decimal("50.00")),
        current_availability=Availability.IN_STOCK,
    )
    plaza_vea_new = ChangeEvent(
        kind=ChangeKind.NEW,
        store="plaza_vea",
        external_id="pv1",
        title="Plaza Vea offer",
        url="https://x/pv1",
        current_price=Money(amount=Decimal("60.00")),
        current_availability=Availability.IN_STOCK,
    )
    outcome = RunOutcome(
        run_id=4,
        started_at=NOW,
        results=[
            StoreResult(store="ripley", status="ok", offer_count=1, events=[ripley_baseline], store_has_history=False),
            StoreResult(store="plaza_vea", status="ok", offer_count=1, events=[plaza_vea_new], store_has_history=True),
        ],
    )
    ConsoleReporter().report(outcome)
    out = capsys.readouterr().out
    assert "[ripley] initial baseline" in out
    assert "Ripley offer" in out
    assert "[plaza_vea] NEW: Plaza Vea offer" in out


def test_failed_store_is_called_out_distinctly_from_zero_matches(capsys) -> None:
    outcome = RunOutcome(
        run_id=5,
        started_at=NOW,
        results=[
            StoreResult(store="ripley", status="failed", offer_count=0, events=[], error="HTTP 403"),
            StoreResult(store="oechsle", status="ok", offer_count=0, events=[]),
        ],
    )
    ConsoleReporter().report(outcome)
    out = capsys.readouterr().out
    assert "[ripley] FAILED: HTTP 403" in out
    assert "[oechsle] ok, 0 matches" in out


def test_all_stores_failed_does_not_read_as_no_changes(capsys) -> None:
    """Regression (sdd-verify WARNING 2): an all-failed run must not end with
    the same trailer as a healthy zero-events run, or an operator skimming
    only the last line could misread total failure as "all clear"."""
    outcome = RunOutcome(
        run_id=6,
        started_at=NOW,
        results=[
            StoreResult(store="plaza_vea", status="failed", offer_count=0, events=[], error="timeout"),
            StoreResult(store="oechsle", status="failed", offer_count=0, events=[], error="HTTP 500"),
            StoreResult(store="ripley", status="failed", offer_count=0, events=[], error="HTTP 403"),
            StoreResult(store="ilahui", status="failed", offer_count=0, events=[], error="HTTP 500"),
        ],
    )
    ConsoleReporter().report(outcome)
    out = capsys.readouterr().out
    assert "No changes detected this run." not in out
    assert "All 4 stores failed this run" in out
    for store in ("plaza_vea", "oechsle", "ripley", "ilahui"):
        assert f"[{store}] FAILED:" in out


def test_partial_failure_with_no_other_changes_still_says_no_changes(capsys) -> None:
    """One store failing alongside healthy zero-match stores is not total
    failure — the existing "no changes" wording is still appropriate since
    the failed store's own FAILED line remains visible above it."""
    outcome = RunOutcome(
        run_id=7,
        started_at=NOW,
        results=[
            StoreResult(store="ripley", status="failed", offer_count=0, events=[], error="HTTP 403"),
            StoreResult(store="plaza_vea", status="ok", offer_count=0, events=[]),
        ],
    )
    ConsoleReporter().report(outcome)
    out = capsys.readouterr().out
    assert "No changes detected this run." in out
    assert "[ripley] FAILED: HTTP 403" in out


def test_current_listing_view_prints_all_entries(capsys) -> None:
    entries = [
        CurrentListingEntry(
            store="plaza_vea",
            external_id=str(i),
            title=f"Product {i}",
            url=f"https://x/{i}",
            price=Money(amount=Decimal("10.00")),
            availability=Availability.IN_STOCK,
            observed_at=NOW,
        )
        for i in range(6)
    ]
    ConsoleReporter().report_listing(entries)
    out = capsys.readouterr().out
    for i in range(6):
        assert f"Product {i}" in out
        assert f"https://x/{i}" in out


def test_current_listing_view_empty(capsys) -> None:
    ConsoleReporter().report_listing([])
    out = capsys.readouterr().out
    assert "No current matching offers" in out


def test_color_is_disabled_by_default_under_pytest(capsys) -> None:
    """Auto-detection (`use_color=None`) must never emit escape codes when
    stdout isn't a real terminal — which captured pytest output never is."""
    event = ChangeEvent(
        kind=ChangeKind.NEW,
        store="plaza_vea",
        external_id="1",
        title="ETB",
        url="https://x/1",
        current_price=Money(amount=Decimal("99.90")),
        current_availability=Availability.IN_STOCK,
    )
    outcome = RunOutcome(
        run_id=9, started_at=NOW, results=[StoreResult(store="plaza_vea", status="ok", offer_count=1, events=[event])]
    )
    ConsoleReporter().report(outcome)
    out = capsys.readouterr().out
    assert _HIGHLIGHT not in out
    assert "NEW: ETB" in out


def test_in_stock_offer_is_highlighted_when_color_forced_on(capsys) -> None:
    in_stock_event = ChangeEvent(
        kind=ChangeKind.NEW,
        store="plaza_vea",
        external_id="1",
        title="In Stock ETB",
        url="https://x/1",
        current_price=Money(amount=Decimal("99.90")),
        current_availability=Availability.IN_STOCK,
    )
    out_of_stock_event = ChangeEvent(
        kind=ChangeKind.RESTOCKED,
        store="plaza_vea",
        external_id="2",
        title="Still Out Box",
        url="https://x/2",
        previous_availability=Availability.OUT_OF_STOCK,
        current_availability=Availability.OUT_OF_STOCK,
    )
    outcome = RunOutcome(
        run_id=10,
        started_at=NOW,
        results=[
            StoreResult(store="plaza_vea", status="ok", offer_count=2, events=[in_stock_event, out_of_stock_event])
        ],
    )
    ConsoleReporter(use_color=True).report(outcome)
    out = capsys.readouterr().out

    in_stock_line = next(line for line in out.splitlines() if "In Stock ETB" in line)
    out_of_stock_line = next(line for line in out.splitlines() if "Still Out Box" in line)
    assert in_stock_line.startswith(_HIGHLIGHT)
    assert in_stock_line.endswith(_RESET)
    assert _HIGHLIGHT not in out_of_stock_line


def test_price_drop_that_is_also_in_stock_is_highlighted(capsys) -> None:
    event = ChangeEvent(
        kind=ChangeKind.PRICE_DROP,
        store="plaza_vea",
        external_id="1",
        title="Cheaper And Available ETB",
        url="https://x/1",
        previous_price=Money(amount=Decimal("100.00")),
        current_price=Money(amount=Decimal("90.00")),
        current_availability=Availability.IN_STOCK,
    )
    outcome = RunOutcome(
        run_id=11, started_at=NOW, results=[StoreResult(store="plaza_vea", status="ok", offer_count=1, events=[event])]
    )
    ConsoleReporter(use_color=True).report(outcome)
    out = capsys.readouterr().out
    assert _HIGHLIGHT in out
    assert "Cheaper And Available ETB" in out


def test_baseline_highlights_only_in_stock_entries(capsys) -> None:
    in_stock = ChangeEvent(
        kind=ChangeKind.BASELINE,
        store="ilahui",
        external_id="1",
        title="Baseline In Stock",
        url="https://x/1",
        current_price=Money(amount=Decimal("50.00")),
        current_availability=Availability.IN_STOCK,
    )
    out_of_stock = ChangeEvent(
        kind=ChangeKind.BASELINE,
        store="ilahui",
        external_id="2",
        title="Baseline Out Of Stock",
        url="https://x/2",
        current_price=Money(amount=Decimal("60.00")),
        current_availability=Availability.OUT_OF_STOCK,
    )
    outcome = RunOutcome(
        run_id=12,
        started_at=NOW,
        results=[
            StoreResult(
                store="ilahui",
                status="ok",
                offer_count=2,
                events=[in_stock, out_of_stock],
                store_has_history=False,
            )
        ],
    )
    ConsoleReporter(use_color=True).report(outcome)
    out = capsys.readouterr().out

    in_stock_line = next(line for line in out.splitlines() if "Baseline In Stock" in line)
    out_of_stock_line = next(line for line in out.splitlines() if "Baseline Out Of Stock" in line)
    assert _HIGHLIGHT in in_stock_line
    assert _HIGHLIGHT not in out_of_stock_line


def test_current_listing_highlights_in_stock_entries(capsys) -> None:
    entries = [
        CurrentListingEntry(
            store="plaza_vea",
            external_id="1",
            title="Available Now",
            url="https://x/1",
            price=Money(amount=Decimal("10.00")),
            availability=Availability.IN_STOCK,
            observed_at=NOW,
        ),
        CurrentListingEntry(
            store="plaza_vea",
            external_id="2",
            title="Not Available",
            url="https://x/2",
            price=Money(amount=Decimal("10.00")),
            availability=Availability.OUT_OF_STOCK,
            observed_at=NOW,
        ),
    ]
    ConsoleReporter(use_color=True).report_listing(entries)
    out = capsys.readouterr().out

    available_line = next(line for line in out.splitlines() if "Available Now" in line)
    unavailable_line = next(line for line in out.splitlines() if "Not Available" in line)
    assert _HIGHLIGHT in available_line
    assert _HIGHLIGHT not in unavailable_line
