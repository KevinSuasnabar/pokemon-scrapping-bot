"""Core domain model: Offer and its value objects.

Zero I/O. Zero third-party imports. This module is the one place price
comparisons and offer identity are defined, so change-detection and every
adapter agree on the same rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

#: (store slug, external_id) — the stable identity of a tracked product.
OfferKey = tuple[str, str]


class Language(StrEnum):
    ENGLISH = "en"
    SPANISH = "es"
    UNKNOWN = "unknown"


class Availability(StrEnum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    UNKNOWN = "unknown"


class ProductType(StrEnum):
    ETB = "etb"
    BOOSTER_BOX = "booster_box"
    BOOSTER_PACK = "booster_pack"
    BLISTER = "blister"
    COLLECTION_BOX = "collection_box"
    OTHER = "other"


class CurrencyMismatchError(ValueError):
    """Raised when comparing or diffing two `Money` values in different currencies."""


@dataclass(frozen=True, slots=True)
class Money:
    """A monetary amount. Always `Decimal` — never `float` — to avoid binary
    floating-point drift on price-drop comparisons (design decision #7)."""

    amount: Decimal
    currency: str = "PEN"

    def _check_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"cannot compare Money in different currencies: {self.currency!r} vs {other.currency!r}"
            )

    def __lt__(self, other: Money) -> bool:
        self._check_currency(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._check_currency(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._check_currency(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._check_currency(other)
        return self.amount >= other.amount

    def delta(self, other: Money) -> Decimal:
        """`self - other`, raising on currency mismatch."""
        self._check_currency(other)
        return self.amount - other.amount


@dataclass(frozen=True, slots=True)
class Offer:
    """A single store's listing for a product, as observed at `observed_at`."""

    store: str
    external_id: str
    title: str
    url: str
    price: Money | None
    availability: Availability
    language: Language
    product_type: ProductType
    observed_at: datetime

    @property
    def key(self) -> OfferKey:
        return (self.store, self.external_id)
