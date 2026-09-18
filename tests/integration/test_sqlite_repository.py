"""Real `sqlite3` on `tmp_path`: last_known/has_history/price_history
semantics, FK cascade, per-store transaction isolation, append-only inserts,
SKU/URL-change new-identity behavior."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tracker.domain.model import Availability, Language, Money, Offer, ProductType
from tracker.infrastructure.persistence.sqlite_offer_repository import SqliteOfferRepository

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def _offer(
    external_id: str,
    *,
    store: str = "plaza_vea",
    price: str | None = "100.00",
    availability: Availability = Availability.IN_STOCK,
    observed_at: datetime = NOW,
    title: str = "Pokemon TCG 30 Aniversario ETB En Ingles",
    url: str = "https://example.test/p",
) -> Offer:
    return Offer(
        store=store,
        external_id=external_id,
        title=title,
        url=url,
        price=Money(amount=Decimal(price)) if price is not None else None,
        availability=availability,
        language=Language.ENGLISH,
        product_type=ProductType.ETB,
        observed_at=observed_at,
    )


def test_has_history_false_before_any_ok_run(tmp_db: sqlite3.Connection) -> None:
    repo = SqliteOfferRepository(tmp_db)
    assert repo.has_history("plaza_vea") is False


def test_has_history_true_after_ok_store_run(tmp_db: sqlite3.Connection) -> None:
    repo = SqliteOfferRepository(tmp_db)
    run_id = repo.start_run(NOW)
    repo.record_store_run(run_id, "plaza_vea", "ok", 1, None)
    assert repo.has_history("plaza_vea") is True


def test_has_history_is_scoped_per_store(tmp_db: sqlite3.Connection) -> None:
    repo = SqliteOfferRepository(tmp_db)
    run_id = repo.start_run(NOW)
    repo.record_store_run(run_id, "plaza_vea", "ok", 1, None)
    repo.record_store_run(run_id, "ripley", "failed", 0, "HTTP 403")
    assert repo.has_history("plaza_vea") is True
    assert repo.has_history("ripley") is False


def test_last_known_picks_newest_across_multiple_runs(tmp_db: sqlite3.Connection) -> None:
    repo = SqliteOfferRepository(tmp_db)

    run1 = repo.start_run(NOW)
    repo.record_observations(run1, [_offer("p1", price="100.00", observed_at=NOW)])

    run2_time = NOW + timedelta(days=1)
    run2 = repo.start_run(run2_time)
    repo.record_observations(run2, [_offer("p1", price="90.00", observed_at=run2_time)])

    run3_time = NOW + timedelta(days=2)
    run3 = repo.start_run(run3_time)
    repo.record_observations(run3, [_offer("p1", price="80.00", observed_at=run3_time)])

    last_known = repo.last_known("plaza_vea")
    assert last_known["p1"].price is not None
    assert last_known["p1"].price.amount == Decimal("80.00")
    assert last_known["p1"].observed_at == run3_time


def test_price_history_returns_all_observations_in_chronological_order(
    tmp_db: sqlite3.Connection,
) -> None:
    repo = SqliteOfferRepository(tmp_db)
    prices = ["100.00", "95.00", "90.00", "85.00", "80.00"]
    for i, price in enumerate(prices):
        t = NOW + timedelta(days=i)
        run_id = repo.start_run(t)
        repo.record_observations(run_id, [_offer("p1", price=price, observed_at=t)])

    history = repo.price_history("plaza_vea", "p1")
    assert len(history) == 5
    assert [h.price.amount for h in history] == [Decimal(p) for p in prices]
    # strictly ascending timestamps -> chronological order
    assert all(history[i].observed_at < history[i + 1].observed_at for i in range(4))


def test_record_observations_is_append_only(tmp_db: sqlite3.Connection) -> None:
    repo = SqliteOfferRepository(tmp_db)
    run1 = repo.start_run(NOW)
    repo.record_observations(run1, [_offer("p1")])
    run2_time = NOW + timedelta(days=1)
    run2 = repo.start_run(run2_time)
    repo.record_observations(run2, [_offer("p1", observed_at=run2_time)])

    count = tmp_db.execute("SELECT COUNT(*) AS c FROM observation").fetchone()["c"]
    assert count == 2


def test_fk_cascade_deletes_observations_when_run_deleted(tmp_db: sqlite3.Connection) -> None:
    repo = SqliteOfferRepository(tmp_db)
    run_id = repo.start_run(NOW)
    repo.record_observations(run_id, [_offer("p1")])

    tmp_db.execute("DELETE FROM run WHERE id = ?", (run_id,))
    tmp_db.commit()

    count = tmp_db.execute("SELECT COUNT(*) AS c FROM observation").fetchone()["c"]
    assert count == 0


def test_per_store_transaction_isolation_one_store_failure_does_not_lose_another(
    tmp_db: sqlite3.Connection,
) -> None:
    repo = SqliteOfferRepository(tmp_db)
    run_id = repo.start_run(NOW)

    # Plaza Vea succeeds and commits.
    repo.record_observations(run_id, [_offer("p1", store="plaza_vea")])
    repo.record_store_run(run_id, "plaza_vea", "ok", 1, None)

    # Ripley "fails" — the use case would never call record_observations for
    # it; simulate that directly and record the failure.
    repo.record_store_run(run_id, "ripley", "failed", 0, "HTTP 403")

    assert repo.last_known("plaza_vea") != {}
    assert repo.last_known("ripley") == {}
    assert repo.has_history("plaza_vea") is True
    assert repo.has_history("ripley") is False


def test_sku_url_change_yields_new_product_identity(tmp_db: sqlite3.Connection) -> None:
    repo = SqliteOfferRepository(tmp_db)

    run1 = repo.start_run(NOW)
    repo.record_observations(
        run1,
        [_offer("old-sku", url="https://example.test/old-url", price="100.00", observed_at=NOW)],
    )

    run2_time = NOW + timedelta(days=1)
    run2 = repo.start_run(run2_time)
    repo.record_observations(
        run2,
        [_offer("new-sku", url="https://example.test/new-url", price="100.00", observed_at=run2_time)],
    )

    old_history = repo.price_history("plaza_vea", "old-sku")
    new_history = repo.price_history("plaza_vea", "new-sku")

    assert len(old_history) == 1  # unaffected, independent identity
    assert len(new_history) == 1  # fresh, empty-start identity


def test_current_listing_returns_latest_per_product_across_stores(
    tmp_db: sqlite3.Connection,
) -> None:
    repo = SqliteOfferRepository(tmp_db)
    run_id = repo.start_run(NOW)
    repo.record_observations(
        run_id,
        [
            _offer("p1", store="plaza_vea", title="Plaza Vea Offer"),
            _offer("p2", store="ripley", title="Ripley Offer"),
        ],
    )
    entries = repo.current_listing()
    assert len(entries) == 2
    titles = {e.title for e in entries}
    assert titles == {"Plaza Vea Offer", "Ripley Offer"}


def test_current_listing_filters_by_store(tmp_db: sqlite3.Connection) -> None:
    repo = SqliteOfferRepository(tmp_db)
    run_id = repo.start_run(NOW)
    repo.record_observations(
        run_id,
        [
            _offer("p1", store="plaza_vea"),
            _offer("p2", store="ripley"),
        ],
    )
    entries = repo.current_listing("ripley")
    assert len(entries) == 1
    assert entries[0].store == "ripley"


def test_finish_run_updates_status(tmp_db: sqlite3.Connection) -> None:
    repo = SqliteOfferRepository(tmp_db)
    run_id = repo.start_run(NOW)
    repo.finish_run(run_id, "completed")
    row = tmp_db.execute("SELECT status, finished_at FROM run WHERE id = ?", (run_id,)).fetchone()
    assert row["status"] == "completed"
    assert row["finished_at"] is not None
