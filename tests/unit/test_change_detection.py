"""Pure in-memory diff tests: baseline, new, restocked, price drop, price rise
(no event), unchanged, disappeared product, per-store baseline semantics.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from tracker.application.dto import ObservationSnapshot
from tracker.domain.change_detection import ChangeDetector
from tracker.domain.events import ChangeKind
from tracker.domain.model import Availability, Language, Money, Offer, ProductType

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def _offer(
    external_id: str,
    *,
    store: str = "plaza_vea",
    price: str | None = "289.90",
    availability: Availability = Availability.IN_STOCK,
) -> Offer:
    return Offer(
        store=store,
        external_id=external_id,
        title=f"Pokemon TCG 30 Aniversario {external_id} En Ingles",
        url=f"https://example.test/{external_id}",
        price=Money(amount=Decimal(price)) if price is not None else None,
        availability=availability,
        language=Language.ENGLISH,
        product_type=ProductType.ETB,
        observed_at=NOW,
    )


def _snapshot(
    external_id: str,
    *,
    price: str | None = "289.90",
    availability: Availability = Availability.IN_STOCK,
) -> ObservationSnapshot:
    return ObservationSnapshot(
        external_id=external_id,
        price=Money(amount=Decimal(price)) if price is not None else None,
        availability=availability,
        observed_at=NOW,
    )


def test_first_ever_run_is_baseline_not_new() -> None:
    current = [_offer("p1"), _offer("p2")]
    events = ChangeDetector.detect(previous={}, current=current, store_has_history=False)
    assert len(events) == 2
    assert all(e.kind is ChangeKind.BASELINE for e in events)


def test_new_offer_when_store_has_history() -> None:
    current = [_offer("p1")]
    events = ChangeDetector.detect(previous={}, current=current, store_has_history=True)
    assert len(events) == 1
    assert events[0].kind is ChangeKind.NEW


def test_restocked_event() -> None:
    previous = {"p1": _snapshot("p1", availability=Availability.OUT_OF_STOCK)}
    current = [_offer("p1", availability=Availability.IN_STOCK)]
    events = ChangeDetector.detect(previous, current, store_has_history=True)
    assert len(events) == 1
    assert events[0].kind is ChangeKind.RESTOCKED
    assert events[0].previous_availability is Availability.OUT_OF_STOCK
    assert events[0].current_availability is Availability.IN_STOCK


def test_price_drop_event() -> None:
    previous = {"p1": _snapshot("p1", price="289.90")}
    current = [_offer("p1", price="288.00")]
    events = ChangeDetector.detect(previous, current, store_has_history=True)
    assert len(events) == 1
    assert events[0].kind is ChangeKind.PRICE_DROP
    assert events[0].price_delta is not None
    assert events[0].price_delta.amount == Decimal("-1.90")


def test_price_drop_one_cent_still_qualifies() -> None:
    previous = {"p1": _snapshot("p1", price="129.90")}
    current = [_offer("p1", price="129.89")]
    events = ChangeDetector.detect(previous, current, store_has_history=True)
    assert len(events) == 1
    assert events[0].kind is ChangeKind.PRICE_DROP


def test_price_rise_is_silent() -> None:
    previous = {"p1": _snapshot("p1", price="129.90")}
    current = [_offer("p1", price="139.90")]
    events = ChangeDetector.detect(previous, current, store_has_history=True)
    assert events == []


def test_price_equal_is_silent() -> None:
    previous = {"p1": _snapshot("p1", price="129.90")}
    current = [_offer("p1", price="129.90")]
    events = ChangeDetector.detect(previous, current, store_has_history=True)
    assert events == []


def test_price_none_on_either_side_is_silent() -> None:
    previous = {"p1": _snapshot("p1", price=None)}
    current = [_offer("p1", price=None)]
    events = ChangeDetector.detect(previous, current, store_has_history=True)
    assert events == []


def test_going_out_of_stock_is_silent() -> None:
    previous = {"p1": _snapshot("p1", availability=Availability.IN_STOCK)}
    current = [_offer("p1", availability=Availability.OUT_OF_STOCK)]
    events = ChangeDetector.detect(previous, current, store_has_history=True)
    assert events == []


def test_unchanged_product_produces_no_event() -> None:
    previous = {"p1": _snapshot("p1", price="129.90", availability=Availability.IN_STOCK)}
    current = [_offer("p1", price="129.90", availability=Availability.IN_STOCK)]
    events = ChangeDetector.detect(previous, current, store_has_history=True)
    assert events == []


def test_disappeared_product_is_not_visited() -> None:
    """A product in `previous` but absent from `current` produces nothing —
    absence is not out-of-stock, and last-known state is simply not touched
    (change_detection only iterates `current`)."""
    previous = {"p1": _snapshot("p1"), "p2": _snapshot("p2")}
    current = [_offer("p1")]  # p2 is simply missing this run
    events = ChangeDetector.detect(previous, current, store_has_history=True)
    assert events == []


def test_restocked_and_price_drop_can_both_fire_for_one_product() -> None:
    previous = {"p1": _snapshot("p1", price="289.90", availability=Availability.OUT_OF_STOCK)}
    current = [_offer("p1", price="199.90", availability=Availability.IN_STOCK)]
    events = ChangeDetector.detect(previous, current, store_has_history=True)
    kinds = {e.kind for e in events}
    assert kinds == {ChangeKind.RESTOCKED, ChangeKind.PRICE_DROP}


def test_per_store_baseline_alongside_established_store_new_event() -> None:
    """Simulates one run's two stores diffed independently: Ripley's first
    successful run (baseline) alongside Plaza Vea's established history (NEW)."""
    ripley_events = ChangeDetector.detect(
        previous={}, current=[_offer("r1", store="ripley")], store_has_history=False
    )
    plaza_vea_events = ChangeDetector.detect(
        previous={}, current=[_offer("pv1", store="plaza_vea")], store_has_history=True
    )
    assert ripley_events[0].kind is ChangeKind.BASELINE
    assert plaza_vea_events[0].kind is ChangeKind.NEW
