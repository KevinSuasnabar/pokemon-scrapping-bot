"""Change-detection output types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from tracker.domain.model import Availability, Money


class ChangeKind(StrEnum):
    BASELINE = "baseline"
    NEW = "new"
    RESTOCKED = "restocked"
    PRICE_DROP = "price_drop"


@dataclass(frozen=True, slots=True)
class ChangeEvent:
    """One reportable change for a (store, product).

    Field population depends on `kind`:
      - BASELINE / NEW: only `current_price` / `current_availability` are meaningful.
      - RESTOCKED: `previous_availability` and `current_availability` are both set.
      - PRICE_DROP: `previous_price` and `current_price` are both set (same currency);
        use `price_delta` for the signed decrease. `current_availability` is also
        carried (not used by price-drop matching itself) so a reporter can flag a
        price drop that is ALSO currently in stock.
    """

    kind: ChangeKind
    store: str
    external_id: str
    title: str
    url: str
    previous_price: Money | None = None
    current_price: Money | None = None
    previous_availability: Availability | None = None
    current_availability: Availability | None = None

    @property
    def price_delta(self) -> Money | None:
        """`current_price - previous_price` (negative = a drop). None if not applicable."""
        if self.previous_price is None or self.current_price is None:
            return None
        return Money(
            amount=self.current_price.delta(self.previous_price),
            currency=self.current_price.currency,
        )
