"""Money / Offer domain model tests: Decimal arithmetic, currency-mismatch guard."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tracker.domain.model import (
    Availability,
    CurrencyMismatchError,
    Language,
    Money,
    Offer,
    ProductType,
    is_in_stock,
)


def test_money_less_than_same_currency() -> None:
    cheaper = Money(amount=Decimal("129.89"), currency="PEN")
    pricier = Money(amount=Decimal("129.90"), currency="PEN")
    assert cheaper < pricier
    assert pricier > cheaper


def test_money_one_cent_precision_is_exact() -> None:
    a = Money(amount=Decimal("129.90"))
    b = Money(amount=Decimal("129.89"))
    assert b < a
    assert a.delta(b) == Decimal("0.01")


def test_money_currency_mismatch_raises_on_comparison() -> None:
    pen = Money(amount=Decimal("100"), currency="PEN")
    usd = Money(amount=Decimal("100"), currency="USD")
    with pytest.raises(CurrencyMismatchError):
        _ = pen < usd


def test_money_currency_mismatch_raises_on_delta() -> None:
    pen = Money(amount=Decimal("100"), currency="PEN")
    usd = Money(amount=Decimal("100"), currency="USD")
    with pytest.raises(CurrencyMismatchError):
        pen.delta(usd)


def test_money_decimal_avoids_float_drift() -> None:
    # 0.1 + 0.2 != 0.3 in binary float; Decimal must not suffer that here.
    total = Money(amount=Decimal("0.1")).amount + Money(amount=Decimal("0.2")).amount
    assert total == Decimal("0.3")


def test_offer_key_is_store_and_external_id() -> None:
    offer = Offer(
        store="plaza_vea",
        external_id="12345",
        title="Pokemon TCG 30 Aniversario ETB En Ingles",
        url="https://example.test/p",
        price=Money(amount=Decimal("289.90")),
        availability=Availability.IN_STOCK,
        language=Language.ENGLISH,
        product_type=ProductType.ETB,
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert offer.key == ("plaza_vea", "12345")


@pytest.mark.parametrize(
    ("availability", "expected"),
    [
        (Availability.IN_STOCK, True),
        (Availability.OUT_OF_STOCK, False),
        (Availability.UNKNOWN, False),
        (None, False),
    ],
)
def test_is_in_stock(availability: Availability | None, expected: bool) -> None:
    assert is_in_stock(availability) is expected
