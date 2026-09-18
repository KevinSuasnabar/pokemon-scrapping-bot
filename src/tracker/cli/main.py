"""`tracker` CLI — argparse composition root.

Wires `httpx.Client`, the four store adapters, the SQLite connection/repository,
the console reporter, and a real-time `Clock`, then runs `TrackOffersUseCase`.
Exit codes: `0` all stores ok, `1` partial failure, `2` all failed (or no
stores were selected at all).
"""

from __future__ import annotations

import argparse
import json as json_module
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

import httpx

from tracker.adapters.reporting.console import ConsoleReporter
from tracker.adapters.stores.ilahui import IlahuiAdapter
from tracker.adapters.stores.oechsle import OechsleAdapter
from tracker.adapters.stores.plaza_vea import PlazaVeaAdapter
from tracker.adapters.stores.ripley import HttpxTransport, RipleyAdapter
from tracker.application.dto import CurrentListingEntry, ObservationSnapshot, RunOutcome
from tracker.application.ports import StoreAdapter
from tracker.application.track_offers import TrackOffersUseCase
from tracker.infrastructure.http.client import build_http_client
from tracker.infrastructure.persistence.connection import apply_schema, connect, seed_stores
from tracker.infrastructure.persistence.sqlite_offer_repository import SqliteOfferRepository

DEFAULT_QUERIES: tuple[str, ...] = ("pokemon 30 aniversario", "pokemon 30th anniversary")
DEFAULT_DB_PATH = "./tracker.db"
ALL_STORE_SLUGS: tuple[str, ...] = ("plaza_vea", "oechsle", "ripley", "ilahui")


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class _NullReporter:
    """Used under `--json`: suppresses console text so stdout carries only JSON."""

    def report(self, outcome: RunOutcome) -> None:
        pass

    def report_listing(self, entries: list[CurrentListingEntry]) -> None:
        pass


def _build_adapters(client: httpx.Client, store_filter: Sequence[str] | None) -> list[StoreAdapter]:
    factories: dict[str, Callable[[], StoreAdapter]] = {
        "plaza_vea": lambda: PlazaVeaAdapter(client),
        "oechsle": lambda: OechsleAdapter(client),
        "ilahui": lambda: IlahuiAdapter(client),
        "ripley": lambda: RipleyAdapter(transport=HttpxTransport(client)),
    }
    slugs = store_filter or ALL_STORE_SLUGS
    return [factories[slug]() for slug in slugs if slug in factories]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tracker", description="Pokemon TCG 30th Anniversary offer tracker"
    )
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite database path")
    parser.add_argument(
        "--store",
        action="append",
        dest="stores",
        choices=list(ALL_STORE_SLUGS),
        help="Limit to one store (repeatable)",
    )
    parser.add_argument("--query", action="append", dest="queries", help="Search query (repeatable)")
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON instead of console text"
    )
    parser.add_argument(
        "--history",
        metavar="STORE:EXTERNAL_ID",
        help="Print price history for one product and exit (no network calls)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_current",
        help="Print the current matching listing and exit (no network calls)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        metavar="SECONDS",
        help=(
            "Run continuously, waiting this many seconds between runs, "
            "until interrupted with Ctrl+C. Omit to run once and exit."
        ),
    )
    return parser


def _exit_code(statuses: Sequence[str]) -> int:
    if not statuses:
        return 2
    if all(status == "ok" for status in statuses):
        return 0
    if all(status == "failed" for status in statuses):
        return 2
    return 1


def _event_to_json(event: object) -> dict[str, object]:
    from tracker.domain.events import ChangeEvent  # local import: avoids a cycle at module load

    assert isinstance(event, ChangeEvent)
    return {
        "kind": event.kind.value,
        "store": event.store,
        "external_id": event.external_id,
        "title": event.title,
        "url": event.url,
        "previous_price": str(event.previous_price.amount) if event.previous_price else None,
        "current_price": str(event.current_price.amount) if event.current_price else None,
        "previous_availability": (
            event.previous_availability.value if event.previous_availability else None
        ),
        "current_availability": (
            event.current_availability.value if event.current_availability else None
        ),
    }


def _outcome_to_json(outcome: RunOutcome) -> dict[str, object]:
    return {
        "run_id": outcome.run_id,
        "started_at": outcome.started_at.isoformat(),
        "results": [
            {
                "store": result.store,
                "status": result.status,
                "offer_count": result.offer_count,
                "error": result.error,
                "store_has_history": result.store_has_history,
                "events": [_event_to_json(event) for event in result.events],
            }
            for result in outcome.results
        ],
    }


def _observation_to_json(observation: ObservationSnapshot) -> dict[str, object]:
    return {
        "observed_at": observation.observed_at.isoformat(),
        "price": str(observation.price.amount) if observation.price else None,
        "availability": observation.availability.value,
    }


def _listing_entry_to_json(entry: CurrentListingEntry) -> dict[str, object]:
    return {
        "store": entry.store,
        "external_id": entry.external_id,
        "title": entry.title,
        "url": entry.url,
        "price": str(entry.price.amount) if entry.price else None,
        "availability": entry.availability.value,
        "observed_at": entry.observed_at.isoformat(),
    }


def _run_once(
    repository: SqliteOfferRepository, client: httpx.Client, args: argparse.Namespace
) -> int:
    adapters = _build_adapters(client, args.stores)
    queries = args.queries or list(DEFAULT_QUERIES)
    reporter = _NullReporter() if args.json else ConsoleReporter()
    use_case = TrackOffersUseCase(
        adapters=adapters, repository=repository, reporter=reporter, clock=SystemClock()
    )
    outcome = use_case.execute(queries)

    if args.json:
        print(json_module.dumps(_outcome_to_json(outcome), indent=2))

    return _exit_code([result.status for result in outcome.results])


def _run_loop(
    repository: SqliteOfferRepository, client: httpx.Client, args: argparse.Namespace
) -> int:
    """Runs `_run_once` every `args.interval` seconds until Ctrl+C. Each
    run's own exit code (ok/partial/failed) never stops the loop — only a
    manual interrupt does, per the user's explicit choice to control this
    themselves rather than have it auto-stop when something is found.

    Per-store failures are already isolated inside `TrackOffersUseCase`
    (design decision #9) and never reach here. This outer `except Exception`
    is a second, coarser safety net for anything else unexpected (e.g. a
    transient SQLite error) — meant to run unattended for hours, so one bad
    iteration must log and retry next cycle, not silently kill the whole
    watch until a human notices."""
    print(
        f"Watching every {args.interval}s — press Ctrl+C to stop.",
        file=sys.stderr,
    )
    try:
        while True:
            try:
                _run_once(repository, client, args)
            except Exception as exc:  # noqa: BLE001 — keep an unattended watch alive
                print(f"Unexpected error this run (continuing): {exc}", file=sys.stderr)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped by user.", file=sys.stderr)
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.interval is not None:
        if args.interval <= 0:
            print("--interval must be a positive number of seconds", file=sys.stderr)
            return 2
        if args.history or args.list_current:
            print("--interval cannot be combined with --history or --list", file=sys.stderr)
            return 2

    conn = connect(args.db)
    apply_schema(conn)
    seed_stores(conn)
    repository = SqliteOfferRepository(conn)

    try:
        if args.history:
            store_slug, separator, external_id = args.history.partition(":")
            if not separator or not store_slug or not external_id:
                print("Invalid --history value; expected STORE:EXTERNAL_ID", file=sys.stderr)
                return 2
            history = repository.price_history(store_slug, external_id)
            if args.json:
                print(json_module.dumps([_observation_to_json(h) for h in history], indent=2))
            else:
                for observation in history:
                    price_text = (
                        f"S/ {observation.price.amount:.2f}" if observation.price else "price not published"
                    )
                    print(
                        f"{observation.observed_at.isoformat()} | {price_text} | "
                        f"{observation.availability.value}"
                    )
            return 0

        if args.list_current:
            store_filter = args.stores[0] if args.stores else None
            entries = repository.current_listing(store_filter)
            if args.json:
                print(json_module.dumps([_listing_entry_to_json(e) for e in entries], indent=2))
            else:
                ConsoleReporter().report_listing(entries)
            return 0

        client = build_http_client()
        try:
            if args.interval is not None:
                return _run_loop(repository, client, args)
            return _run_once(repository, client, args)
        finally:
            client.close()
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
