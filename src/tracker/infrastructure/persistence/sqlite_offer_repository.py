"""`OfferRepository` implemented over stdlib `sqlite3`.

Prices are stored as `INTEGER` cents (design decision #7) — never `float` —
so a price-drop comparison never suffers binary-float drift. Product identity
is `(store_slug, external_id)` per the schema's `UNIQUE` constraint: a
changed `external_id` (including a URL-derived one, for adapters that fall
back to the URL as identity) naturally creates a fresh product row with empty
history, which is the offer-history-store "SKU/URL Change Yields a New
Product Identity" behavior.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from tracker.application.dto import CurrentListingEntry, ObservationSnapshot
from tracker.domain.model import Availability, Money, Offer

_CENTS_PER_UNIT = Decimal(100)


def _money_to_cents(money: Money | None) -> tuple[int | None, str]:
    if money is None:
        return None, "PEN"
    cents = int((money.amount * _CENTS_PER_UNIT).quantize(Decimal(1)))
    return cents, money.currency


def _cents_to_money(price_cents: int | None, currency: str) -> Money | None:
    if price_cents is None:
        return None
    return Money(amount=Decimal(price_cents) / _CENTS_PER_UNIT, currency=currency)


def _dt_to_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _iso_to_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


_LAST_KNOWN_SQL = """
SELECT p.external_id, o.price_cents, o.currency, o.availability, o.observed_at
FROM product p
JOIN observation o ON o.id = (
  SELECT o2.id FROM observation o2 WHERE o2.product_id = p.id
  ORDER BY o2.observed_at DESC, o2.id DESC LIMIT 1)
WHERE p.store_slug = ?
"""

_CURRENT_LISTING_SQL = """
SELECT p.store_slug, p.external_id, p.title, p.url,
       o.price_cents, o.currency, o.availability, o.observed_at
FROM product p
JOIN observation o ON o.id = (
  SELECT o2.id FROM observation o2 WHERE o2.product_id = p.id
  ORDER BY o2.observed_at DESC, o2.id DESC LIMIT 1)
"""


class SqliteOfferRepository:
    """Implements `application.ports.OfferRepository` (structural — no base class)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def start_run(self, started_at: datetime) -> int:
        cursor = self._conn.execute(
            "INSERT INTO run (started_at, status) VALUES (?, 'running')",
            (_dt_to_iso(started_at),),
        )
        self._conn.commit()
        return int(cursor.lastrowid)  # type: ignore[arg-type]

    def finish_run(self, run_id: int, status: str) -> None:
        self._conn.execute(
            "UPDATE run SET status = ?, finished_at = ? WHERE id = ?",
            (status, _dt_to_iso(datetime.now(UTC)), run_id),
        )
        self._conn.commit()

    def record_store_run(
        self, run_id: int, store: str, status: str, offer_count: int, error: str | None
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO store_run (run_id, store_slug, status, offer_count, error)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (run_id, store_slug) DO UPDATE SET
              status = excluded.status,
              offer_count = excluded.offer_count,
              error = excluded.error
            """,
            (run_id, store, status, offer_count, error),
        )
        self._conn.commit()

    def has_history(self, store: str) -> bool:
        row = self._conn.execute(
            "SELECT EXISTS(SELECT 1 FROM store_run WHERE store_slug = ? AND status = 'ok') AS has_history",
            (store,),
        ).fetchone()
        return bool(row["has_history"])

    def last_known(self, store: str) -> dict[str, ObservationSnapshot]:
        rows = self._conn.execute(_LAST_KNOWN_SQL, (store,)).fetchall()
        return {
            row["external_id"]: ObservationSnapshot(
                external_id=row["external_id"],
                price=_cents_to_money(row["price_cents"], row["currency"]),
                availability=Availability(row["availability"]),
                observed_at=_iso_to_dt(row["observed_at"]),
            )
            for row in rows
        }

    def record_observations(self, run_id: int, offers: Sequence[Offer]) -> None:
        """Own transaction: one store's write can never be partially applied
        alongside another store's failure (design decision #9)."""
        try:
            for offer in offers:
                self._conn.execute(
                    """
                    INSERT INTO product
                        (store_slug, external_id, title, url, language, product_type, first_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (store_slug, external_id) DO UPDATE SET
                        title = excluded.title,
                        url = excluded.url,
                        language = excluded.language,
                        product_type = excluded.product_type
                    """,
                    (
                        offer.store,
                        offer.external_id,
                        offer.title,
                        offer.url,
                        offer.language.value,
                        offer.product_type.value,
                        _dt_to_iso(offer.observed_at),
                    ),
                )
                product_row = self._conn.execute(
                    "SELECT id FROM product WHERE store_slug = ? AND external_id = ?",
                    (offer.store, offer.external_id),
                ).fetchone()
                price_cents, currency = _money_to_cents(offer.price)
                self._conn.execute(
                    """
                    INSERT INTO observation
                        (product_id, run_id, price_cents, currency, availability, observed_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        product_row["id"],
                        run_id,
                        price_cents,
                        currency,
                        offer.availability.value,
                        _dt_to_iso(offer.observed_at),
                    ),
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def price_history(
        self, store: str, external_id: str, limit: int = 50
    ) -> list[ObservationSnapshot]:
        rows = self._conn.execute(
            """
            SELECT o.price_cents, o.currency, o.availability, o.observed_at
            FROM observation o
            JOIN product p ON p.id = o.product_id
            WHERE p.store_slug = ? AND p.external_id = ?
            ORDER BY o.observed_at DESC, o.id DESC
            LIMIT ?
            """,
            (store, external_id, limit),
        ).fetchall()
        observations = [
            ObservationSnapshot(
                external_id=external_id,
                price=_cents_to_money(row["price_cents"], row["currency"]),
                availability=Availability(row["availability"]),
                observed_at=_iso_to_dt(row["observed_at"]),
            )
            for row in rows
        ]
        observations.reverse()  # most-recent-N, returned oldest-first (chronological)
        return observations

    def current_listing(self, store: str | None = None) -> list[CurrentListingEntry]:
        query = _CURRENT_LISTING_SQL
        params: tuple[str, ...] = ()
        if store is not None:
            query += " WHERE p.store_slug = ?"
            params = (store,)
        query += " ORDER BY p.store_slug, p.title"
        rows = self._conn.execute(query, params).fetchall()
        return [
            CurrentListingEntry(
                store=row["store_slug"],
                external_id=row["external_id"],
                title=row["title"],
                url=row["url"],
                price=_cents_to_money(row["price_cents"], row["currency"]),
                availability=Availability(row["availability"]),
                observed_at=_iso_to_dt(row["observed_at"]),
            )
            for row in rows
        ]
