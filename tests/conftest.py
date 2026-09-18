"""Shared pytest fixtures: fixture_body(), frozen_clock, tmp_db."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tracker.infrastructure.persistence.connection import (
    apply_schema,
    connect,
    seed_stores,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_body() -> Callable[[str, str], str]:
    """`fixture_body("plaza_vea", "search_page_1.json")` -> file contents as text."""

    def _load(store: str, name: str) -> str:
        return (FIXTURES_DIR / store / name).read_text(encoding="utf-8")

    return _load


@dataclass(frozen=True, slots=True)
class FrozenClock:
    """A `Clock` (structural) that always returns the same instant."""

    fixed_now: datetime

    def now(self) -> datetime:
        return self.fixed_now


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock(fixed_now=datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC))


@pytest.fixture
def tmp_db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A real SQLite connection, schema applied, stores seeded, on a temp file."""
    db_path = tmp_path / "test_tracker.db"
    conn = connect(db_path)
    apply_schema(conn)
    seed_stores(conn)
    try:
        yield conn
    finally:
        conn.close()
