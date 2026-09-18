"""`Reporter` implemented as plain stdout text (design decision: no `rich`,
keeps the port testable with `capsys`). No dashboard, bot, email, or push
channel exists anywhere in this module — console text is the only output.

Lines for an offer that is currently IN_STOCK are wrapped in a bright ANSI
highlight (bold green) so a person watching a `--interval` loop can spot
availability at a glance without reading every line. Plain ANSI escape
codes, not a color library — keeps this module dependency-free and the
highlighted text is still just a `str`, so `capsys`-based tests keep working
unchanged (they assert on substrings, escape codes included).
"""

from __future__ import annotations

import os
import sys

from tracker.application.dto import CurrentListingEntry, RunOutcome, StoreResult
from tracker.domain.events import ChangeEvent, ChangeKind
from tracker.domain.model import Availability, Money

_HIGHLIGHT = "\033[1;92m"  # bold bright green
_RESET = "\033[0m"


def _detect_color_support() -> bool:
    """Honors the NO_COLOR convention (https://no-color.org/) and disables
    color when stdout isn't a real terminal (piped/redirected)."""
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _highlight(text: str, *, use_color: bool, available: bool) -> str:
    if not use_color or not available:
        return text
    return f"{_HIGHLIGHT}{text}{_RESET}"


def _is_available(availability: Availability | None) -> bool:
    return availability is Availability.IN_STOCK


def _format_money(money: Money | None) -> str:
    if money is None:
        return "price not published"
    return f"S/ {money.amount:.2f}"


def _format_event(event: ChangeEvent) -> str:
    if event.kind is ChangeKind.NEW:
        return (
            f"NEW: {event.title} | {_format_money(event.current_price)} | "
            f"{event.current_availability} | {event.url}"
        )
    if event.kind is ChangeKind.RESTOCKED:
        return (
            f"RESTOCKED: {event.title} "
            f"({event.previous_availability} -> {event.current_availability}) | {event.url}"
        )
    if event.kind is ChangeKind.PRICE_DROP:
        delta = event.price_delta
        delta_text = f" ({_format_money(delta)})" if delta is not None else ""
        return (
            f"PRICE_DROP: {event.title} "
            f"{_format_money(event.previous_price)} -> {_format_money(event.current_price)}"
            f"{delta_text} | {event.url}"
        )
    return f"{event.kind.value.upper()}: {event.title} | {event.url}"


def _print_store_result(result: StoreResult, *, use_color: bool) -> bool:
    """Prints one store's section. Returns True if any baseline/change content
    was printed (used to decide the run-level "no changes" message)."""
    if result.status == "failed":
        print(f"  [{result.store}] FAILED: {result.error}")
        return False

    baseline_events = [e for e in result.events if e.kind is ChangeKind.BASELINE]
    other_events = [e for e in result.events if e.kind is not ChangeKind.BASELINE]

    if not baseline_events and not other_events:
        print(f"  [{result.store}] ok, {result.offer_count} matches, no changes")
        return False

    printed_anything = False
    if baseline_events:
        print(f"  [{result.store}] initial baseline ({len(baseline_events)} offers):")
        for event in baseline_events:
            line = (
                f"    - {event.title} | {_format_money(event.current_price)} | "
                f"{event.current_availability} | {event.url}"
            )
            print(
                _highlight(
                    line, use_color=use_color, available=_is_available(event.current_availability)
                )
            )
        printed_anything = True

    for event in other_events:
        line = f"  [{result.store}] {_format_event(event)}"
        print(
            _highlight(line, use_color=use_color, available=_is_available(event.current_availability))
        )
        printed_anything = True

    return printed_anything


class ConsoleReporter:
    """Implements `application.ports.Reporter` (structural — no base class).

    `use_color`: `None` (default) auto-detects via `_detect_color_support()`
    (NO_COLOR env var + `isatty()`); pass `True`/`False` to force it — tests
    pass `True` explicitly since captured stdout is never a tty.
    """

    def __init__(self, use_color: bool | None = None) -> None:
        self._use_color = _detect_color_support() if use_color is None else use_color

    def report(self, outcome: RunOutcome) -> None:
        print(f"Run #{outcome.run_id} started at {outcome.started_at.isoformat()}")
        any_reportable = False
        for result in outcome.results:
            if _print_store_result(result, use_color=self._use_color):
                any_reportable = True
        if not any_reportable:
            if outcome.results and all(r.status == "failed" for r in outcome.results):
                # Every store failed: "no changes" would read as "all clear" to
                # anyone skimming only the last line right after N FAILED rows.
                print(
                    f"All {len(outcome.results)} stores failed this run — "
                    "no data collected. See FAILED lines above for details."
                )
            else:
                print("No changes detected this run.")

    def report_listing(self, entries: list[CurrentListingEntry]) -> None:
        if not entries:
            print("No current matching offers in the database.")
            return
        print(f"Current matching listing ({len(entries)} offers):")
        for entry in entries:
            line = (
                f"  [{entry.store}] {entry.title} | {_format_money(entry.price)} | "
                f"{entry.availability} | {entry.url}"
            )
            print(_highlight(line, use_color=self._use_color, available=_is_available(entry.availability)))
