"""`TelegramReporter` — sends only for in-stock events, never crashes on a
failed send, `report_listing` is a no-op."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from tracker.adapters.reporting.telegram import TelegramReporter
from tracker.application.dto import RunOutcome, StoreResult
from tracker.domain.events import ChangeEvent, ChangeKind
from tracker.domain.model import Availability, Money

NOW = datetime(2026, 1, 15, tzinfo=UTC)
BOT_TOKEN = "123456:fake-token"  # noqa: S105 — test fixture, not a real credential
CHAT_ID = "987654321"


def _client_with_handler(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _in_stock_new_event(store: str = "plaza_vea") -> ChangeEvent:
    return ChangeEvent(
        kind=ChangeKind.NEW,
        store=store,
        external_id="1",
        title="Elite Trainer Box",
        url="https://example.test/etb",
        current_price=Money(amount=Decimal("289.90")),
        current_availability=Availability.IN_STOCK,
    )


def _out_of_stock_new_event(store: str = "plaza_vea") -> ChangeEvent:
    return ChangeEvent(
        kind=ChangeKind.NEW,
        store=store,
        external_id="2",
        title="Sold Out Box",
        url="https://example.test/sold-out",
        current_price=Money(amount=Decimal("99.90")),
        current_availability=Availability.OUT_OF_STOCK,
    )


def test_sends_nothing_when_no_events() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    outcome = RunOutcome(
        run_id=1,
        started_at=NOW,
        results=[StoreResult(store="plaza_vea", status="ok", offer_count=0, events=[])],
    )
    TelegramReporter(_client_with_handler(handler), BOT_TOKEN, CHAT_ID).report(outcome)
    assert calls == []


def test_sends_nothing_when_all_events_are_out_of_stock() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    outcome = RunOutcome(
        run_id=2,
        started_at=NOW,
        results=[
            StoreResult(store="plaza_vea", status="ok", offer_count=1, events=[_out_of_stock_new_event()])
        ],
    )
    TelegramReporter(_client_with_handler(handler), BOT_TOKEN, CHAT_ID).report(outcome)
    assert calls == []


def test_sends_message_for_in_stock_event() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    outcome = RunOutcome(
        run_id=3,
        started_at=NOW,
        results=[
            StoreResult(store="plaza_vea", status="ok", offer_count=1, events=[_in_stock_new_event()])
        ],
    )
    TelegramReporter(_client_with_handler(handler), BOT_TOKEN, CHAT_ID).report(outcome)

    assert len(calls) == 1
    request = calls[0]
    assert request.url.path == f"/bot{BOT_TOKEN}/sendMessage"
    body = json.loads(request.content)
    assert body["chat_id"] == CHAT_ID
    assert "Elite Trainer Box" in body["text"]
    assert "https://example.test/etb" in body["text"]
    assert "NUEVO" in body["text"]
    assert body["parse_mode"] == "HTML"


def test_sends_one_message_covering_multiple_in_stock_events_across_stores() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    outcome = RunOutcome(
        run_id=4,
        started_at=NOW,
        results=[
            StoreResult(store="plaza_vea", status="ok", offer_count=1, events=[_in_stock_new_event("plaza_vea")]),
            StoreResult(store="ilahui", status="ok", offer_count=1, events=[_in_stock_new_event("ilahui")]),
        ],
    )
    TelegramReporter(_client_with_handler(handler), BOT_TOKEN, CHAT_ID).report(outcome)

    assert len(calls) == 1  # one message, not one per event
    text = json.loads(calls[0].content)["text"]
    assert "plaza_vea" in text
    assert "ilahui" in text


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(500, text="server error"),
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("boom", request=request)),
    ],
)
def test_failed_send_does_not_raise(handler) -> None:
    outcome = RunOutcome(
        run_id=5,
        started_at=NOW,
        results=[
            StoreResult(store="plaza_vea", status="ok", offer_count=1, events=[_in_stock_new_event()])
        ],
    )
    # Must not raise — an unattended --interval loop depends on this.
    TelegramReporter(_client_with_handler(handler), BOT_TOKEN, CHAT_ID).report(outcome)


def test_title_with_html_special_characters_is_escaped() -> None:
    """Regression: `parse_mode: HTML` means an unescaped `<`/`&` in a scraped
    title would corrupt the message's own markup or be interpreted as a tag —
    every external string must go through `html.escape()` first."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    event = ChangeEvent(
        kind=ChangeKind.NEW,
        store="plaza_vea",
        external_id="1",
        title="ETB <Special> & Rare",
        url="https://example.test/etb",
        current_price=Money(amount=Decimal("289.90")),
        current_availability=Availability.IN_STOCK,
    )
    outcome = RunOutcome(
        run_id=6,
        started_at=NOW,
        results=[StoreResult(store="plaza_vea", status="ok", offer_count=1, events=[event])],
    )
    TelegramReporter(_client_with_handler(handler), BOT_TOKEN, CHAT_ID).report(outcome)

    text = json.loads(calls[0].content)["text"]
    assert "<Special>" not in text
    assert "ETB &lt;Special&gt; &amp; Rare" in text


def test_report_listing_is_a_noop() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    TelegramReporter(_client_with_handler(handler), BOT_TOKEN, CHAT_ID).report_listing([])
    assert calls == []
