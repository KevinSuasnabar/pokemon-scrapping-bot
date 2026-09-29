"""`Reporter` that pushes a Telegram message via the Bot API — a second,
independent implementation of the same port `ConsoleReporter` implements
(design decision #1: structural `Protocol`, no shared base class needed).

Only sends a message when there's something worth an alert (same `is_in_stock`
predicate the console highlighter uses — see domain/model.py): a BASELINE,
NEW, RESTOCKED, or PRICE_DROP event whose *current* availability is in stock.
A run with nothing interesting sends nothing — this is meant to run every
few minutes for hours, so silence on "no changes" is the whole point.

Sent with `parse_mode: "HTML"` for bold price and a clickable link. Product
titles/URLs are external, untrusted data, so every user-controlled string is
run through `html.escape()` before being interpolated — the only Telegram
markup in the message is markup this module wrote itself.
"""

from __future__ import annotations

import html
import sys

import httpx

from tracker.adapters.reporting.labels import availability_label, kind_emoji, kind_label
from tracker.application.dto import CurrentListingEntry, RunOutcome
from tracker.domain.events import ChangeEvent, ChangeKind
from tracker.domain.model import Money, is_in_stock

_API_BASE = "https://api.telegram.org"


def _format_money(money: Money | None) -> str:
    if money is None:
        return "precio no publicado"
    return f"S/ {money.amount:.2f}"


def _format_event_line(event: ChangeEvent) -> str:
    title = html.escape(event.title)
    url = html.escape(event.url)
    if event.kind is ChangeKind.PRICE_DROP:
        delta = event.price_delta
        delta_text = f" ({_format_money(delta)})" if delta is not None else ""
        price_text = (
            f"{_format_money(event.previous_price)} → <b>{_format_money(event.current_price)}</b>{delta_text}"
        )
    else:
        price_text = f"<b>{_format_money(event.current_price)}</b>"
    return (
        f"{kind_emoji(event.kind)} <b>{kind_label(event.kind)}</b>: {title}\n"
        f"{price_text} · {availability_label(event.current_availability)}\n"
        f'<a href="{url}">Ver producto</a>'
    )


def _build_message(outcome: RunOutcome) -> str | None:
    """`None` when nothing in this run is currently in stock — caller must
    not send anything in that case. Events are grouped by store so a run
    that touches several stores reads as sections, not a flat list."""
    by_store: dict[str, list[str]] = {}
    for result in outcome.results:
        for event in result.events:
            if is_in_stock(event.current_availability):
                by_store.setdefault(result.store, []).append(_format_event_line(event))

    if not by_store:
        return None

    sections = [
        f"<b>[{html.escape(store)}]</b>\n" + "\n\n".join(lines) for store, lines in by_store.items()
    ]
    return f"Corrida #{outcome.run_id}\n\n" + "\n\n".join(sections)


class TelegramReporter:
    """Implements `application.ports.Reporter`.

    `report_listing` (the `--list` flag) is intentionally a no-op: it's a
    one-shot manual command the user runs themselves and reads on their own
    screen, not part of an unattended `--interval` loop — there's nothing to
    push a notification about.
    """

    def __init__(self, client: httpx.Client, bot_token: str, chat_id: str) -> None:
        self._client = client
        self._bot_token = bot_token
        self._chat_id = chat_id

    def send_text(self, text: str) -> None:
        """Sends an arbitrary HTML-formatted message — the primitive `report()`
        is built on, also used directly for operational alerts (e.g. the
        `--interval` loop's own overrun notice) that aren't a `RunOutcome`."""
        url = f"{_API_BASE}/bot{self._bot_token}/sendMessage"
        try:
            response = self._client.post(
                url,
                json={
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # A failed notification must never crash an unattended multi-hour
            # loop (same resilience rule as _run_loop's outer safety net) —
            # log it and let the next run try again.
            print(f"Telegram notification failed (continuing): {exc}", file=sys.stderr)

    def report(self, outcome: RunOutcome) -> None:
        message = _build_message(outcome)
        if message is not None:
            self.send_text(message)

    def report_listing(self, entries: list[CurrentListingEntry]) -> None:
        pass
