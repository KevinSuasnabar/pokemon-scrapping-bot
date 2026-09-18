"""Run-over-run diff producing NEW / RESTOCKED / PRICE_DROP / BASELINE events.

Pure function of its three arguments — no repository, no clock, no I/O
(design.md "Change Detection"). `ChangeDetector.detect` only looks at the
current run's matched offers; a product present in `previous` but absent from
`current` is simply not visited, which is exactly the "nothing — absence is
not out-of-stock" rule from the design table.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from tracker.domain.events import ChangeEvent, ChangeKind
from tracker.domain.model import Availability, Money, Offer


class ObservationLike(Protocol):
    """Structural shape of a "last-known observation" needed to diff.

    Deliberately a `Protocol` rather than an import of
    `application.dto.ObservationSnapshot`: the domain layer must not depend on
    the application layer (hexagonal — dependencies point inward only).
    `ObservationSnapshot` satisfies this shape structurally, with no import
    needed on either side.
    """

    # Read-only properties, not plain attributes: `ObservationSnapshot` is a
    # frozen dataclass, so its fields are read-only from mypy's point of view
    # — a plain-attribute Protocol member would require a *settable*
    # attribute and reject it structurally.
    @property
    def external_id(self) -> str: ...

    @property
    def price(self) -> Money | None: ...

    @property
    def availability(self) -> Availability: ...

    @property
    def observed_at(self) -> datetime: ...


class ChangeDetector:
    """Diffs a run's offers against last-known state per (store, product)."""

    @staticmethod
    def detect(
        previous: Mapping[str, ObservationLike],
        current: Sequence[Offer],
        store_has_history: bool,
    ) -> list[ChangeEvent]:
        events: list[ChangeEvent] = []

        for offer in current:
            if not store_has_history:
                # First-ever successful run for this store: everything is baseline,
                # never NEW (offer-change-detection "First-Run Baseline Labeling").
                events.append(
                    ChangeEvent(
                        kind=ChangeKind.BASELINE,
                        store=offer.store,
                        external_id=offer.external_id,
                        title=offer.title,
                        url=offer.url,
                        current_price=offer.price,
                        current_availability=offer.availability,
                    )
                )
                continue

            previous_observation = previous.get(offer.external_id)

            if previous_observation is None:
                events.append(
                    ChangeEvent(
                        kind=ChangeKind.NEW,
                        store=offer.store,
                        external_id=offer.external_id,
                        title=offer.title,
                        url=offer.url,
                        current_price=offer.price,
                        current_availability=offer.availability,
                    )
                )
                continue

            if (
                previous_observation.availability != Availability.IN_STOCK
                and offer.availability == Availability.IN_STOCK
            ):
                events.append(
                    ChangeEvent(
                        kind=ChangeKind.RESTOCKED,
                        store=offer.store,
                        external_id=offer.external_id,
                        title=offer.title,
                        url=offer.url,
                        previous_availability=previous_observation.availability,
                        current_availability=offer.availability,
                    )
                )

            previous_price = previous_observation.price
            current_price = offer.price
            if (
                previous_price is not None
                and current_price is not None
                and previous_price.currency == current_price.currency
                and current_price < previous_price
            ):
                events.append(
                    ChangeEvent(
                        kind=ChangeKind.PRICE_DROP,
                        store=offer.store,
                        external_id=offer.external_id,
                        title=offer.title,
                        url=offer.url,
                        previous_price=previous_price,
                        current_price=current_price,
                        # Carried so the reporter can highlight a price drop that is
                        # ALSO currently in stock — not used by price-drop matching
                        # itself (that only compares previous/current price above).
                        current_availability=offer.availability,
                    )
                )

        return events
