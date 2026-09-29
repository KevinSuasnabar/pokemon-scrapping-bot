"""`tracker` CLI — argparse composition root.

Wires `httpx.Client`, the four store adapters, the SQLite connection/repository,
the console reporter, and a real-time `Clock`, then runs `TrackOffersUseCase`.
Exit codes: `0` all stores ok, `1` partial failure, `2` all failed (or no
stores were selected at all).
"""

from __future__ import annotations

import argparse
import json as json_module
import os
import random
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

import httpx
from dotenv import find_dotenv, load_dotenv

from tracker.adapters.reporting.composite import CompositeReporter
from tracker.adapters.reporting.console import ConsoleReporter
from tracker.adapters.reporting.telegram import TelegramReporter
from tracker.adapters.stores.falabella import FalabellaAdapter
from tracker.adapters.stores.ilahui import IlahuiAdapter
from tracker.adapters.stores.metro import MetroAdapter
from tracker.adapters.stores.oechsle import OechsleAdapter
from tracker.adapters.stores.pharmax import PharmaxAdapter
from tracker.adapters.stores.plaza_vea import PlazaVeaAdapter
from tracker.adapters.stores.ripley import HttpxTransport, RipleyAdapter
from tracker.adapters.stores.tailoy import TaiLoyAdapter
from tracker.adapters.stores.wong import WongAdapter
from tracker.application.dto import CurrentListingEntry, RunOutcome
from tracker.application.ports import Reporter, StoreAdapter
from tracker.application.track_offers import TrackOffersUseCase
from tracker.infrastructure.http.client import build_http_client
from tracker.infrastructure.persistence.connection import apply_schema, connect, seed_stores
from tracker.infrastructure.persistence.sqlite_offer_repository import SqliteOfferRepository

#: Read at the point `--telegram` is used, not at import time, so tests can
#: monkeypatch `os.environ` freely.
TELEGRAM_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID_ENV = "TELEGRAM_CHAT_ID"

DEFAULT_QUERIES: tuple[str, ...] = ("pokemon 30 aniversario", "pokemon 30th anniversary")
DEFAULT_DB_PATH = "./tracker.db"
ALL_STORE_SLUGS: tuple[str, ...] = (
    "plaza_vea",
    "oechsle",
    "ripley",
    "ilahui",
    "pharmax",
    "tailoy",
    "falabella",
    "metro",
    "wong",
)

#: ±25% around the configured `--interval`. Confirmed live, 2026-09-29:
#: Ripley's Cloudflare bot-management blocked the whole domain (403 on every
#: page, not just search) after ~12 minutes of a perfectly regular 20s
#: cadence hitting the same 2 default queries — a real human never re-checks
#: a page at the exact same second every time, so that mechanical regularity
#: is itself a bot signal, independent of request volume.
_INTERVAL_JITTER_FRACTION = 0.25


def _jittered_interval(interval: int) -> float:
    spread = interval * _INTERVAL_JITTER_FRACTION
    return random.uniform(interval - spread, interval + spread)


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
        "pharmax": lambda: PharmaxAdapter(client),
        "tailoy": lambda: TaiLoyAdapter(client),
        "falabella": lambda: FalabellaAdapter(client),
        "metro": lambda: MetroAdapter(client),
        "wong": lambda: WongAdapter(client),
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
            "Run continuously, waiting approximately this many seconds "
            "(±25%%, randomized, to avoid a bot-like fixed cadence) between "
            "runs, until interrupted with Ctrl+C. Omit to run once and exit."
        ),
    )
    parser.add_argument(
        "--telegram",
        action="store_true",
        help=(
            "Also push a Telegram message for any currently-in-stock event "
            f"(requires {TELEGRAM_BOT_TOKEN_ENV} and {TELEGRAM_CHAT_ID_ENV} "
            "environment variables). Sends nothing on a run with no in-stock "
            "events — safe to combine with --interval for a long-running watch."
        ),
    )
    parser.add_argument(
        "--healthcheck",
        action="store_true",
        help=(
            "Send a one-time test message to Telegram and exit immediately "
            f"(requires {TELEGRAM_BOT_TOKEN_ENV} and {TELEGRAM_CHAT_ID_ENV}). "
            "Runs no store queries and touches no database — use it to "
            "confirm the bot/chat credentials and connectivity work on demand, "
            "independent of whether anything is currently in stock."
        ),
    )
    return parser


def _telegram_credentials_from_env() -> tuple[str, str] | None:
    bot_token = os.environ.get(TELEGRAM_BOT_TOKEN_ENV)
    chat_id = os.environ.get(TELEGRAM_CHAT_ID_ENV)
    if not bot_token or not chat_id:
        return None
    return bot_token, chat_id


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


def _build_reporter(args: argparse.Namespace, client: httpx.Client) -> Reporter:
    base: Reporter = _NullReporter() if args.json else ConsoleReporter()
    if not args.telegram:
        return base
    # main() already validated these are present before any network/DB work
    # started; re-checking here would just duplicate that error path.
    credentials = _telegram_credentials_from_env()
    assert credentials is not None
    bot_token, chat_id = credentials
    return CompositeReporter([base, TelegramReporter(client, bot_token, chat_id)])


def _run_once(
    repository: SqliteOfferRepository, client: httpx.Client, args: argparse.Namespace
) -> int:
    adapters = _build_adapters(client, args.stores)
    queries = args.queries or list(DEFAULT_QUERIES)
    reporter = _build_reporter(args, client)
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
        f"Vigilando cada {args.interval}s — presioná Ctrl+C para detener.",
        file=sys.stderr,
    )

    # Built once, reused for every overrun notice below — independent of the
    # per-run reporter `_run_once` builds internally, since this alert isn't
    # a `RunOutcome`. Credentials were already validated in `main()`.
    telegram: TelegramReporter | None = None
    if args.telegram:
        credentials = _telegram_credentials_from_env()
        assert credentials is not None
        bot_token, chat_id = credentials
        telegram = TelegramReporter(client, bot_token, chat_id)

    try:
        while True:
            cycle_start = time.monotonic()
            try:
                _run_once(repository, client, args)
            except Exception as exc:  # noqa: BLE001 — keep an unattended watch alive
                print(f"Error inesperado en esta corrida (continuando): {exc}", file=sys.stderr)
            elapsed = time.monotonic() - cycle_start

            # Deliberately NOT shortening the sleep below by `elapsed` (that
            # would be fixed-rate scheduling, a separate change) — this only
            # detects and reports the overrun, it doesn't correct for it.
            if elapsed > args.interval:
                notice = (
                    f"⏱️ La corrida tardó {elapsed:.1f}s, más que el intervalo configurado "
                    f"de {args.interval}s. El chequeo real está siendo más lento que lo "
                    "pedido — puede deberse a timeouts o reintentos en alguna tienda. El "
                    "tracker sigue corriendo."
                )
                print(f"Aviso: {notice}", file=sys.stderr)
                if telegram is not None:
                    telegram.send_text(notice)

            time.sleep(_jittered_interval(args.interval))
    except KeyboardInterrupt:
        print("\nDetenido por el usuario.", file=sys.stderr)
        return 0


def main(argv: list[str] | None = None) -> int:
    # Loads .env from the current/parent directories if present (never
    # overrides an already-exported real env var). Called here, not at
    # module import time, so importing this module for tests has no
    # filesystem side effects. `find_dotenv(usecwd=True)`: search from the
    # directory the command is actually run from — the plain `load_dotenv()`
    # default searches from this installed package's own file location
    # instead, which would never find a project-root .env for an
    # editable/installed package.
    load_dotenv(find_dotenv(usecwd=True))

    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.interval is not None:
        if args.interval <= 0:
            print("--interval debe ser un número positivo de segundos", file=sys.stderr)
            return 2
        if args.list_current:
            print("--interval no se puede combinar con --list", file=sys.stderr)
            return 2

    if args.telegram:
        if args.list_current:
            print("--telegram no se puede combinar con --list", file=sys.stderr)
            return 2
        if _telegram_credentials_from_env() is None:
            print(
                f"--telegram requiere que las variables de entorno {TELEGRAM_BOT_TOKEN_ENV} "
                f"y {TELEGRAM_CHAT_ID_ENV} estén configuradas",
                file=sys.stderr,
            )
            return 2

    if args.healthcheck:
        if args.list_current:
            print("--healthcheck no se puede combinar con --list", file=sys.stderr)
            return 2
        if args.interval is not None:
            print("--healthcheck no se puede combinar con --interval", file=sys.stderr)
            return 2
        credentials = _telegram_credentials_from_env()
        if credentials is None:
            print(
                f"--healthcheck requiere que las variables de entorno {TELEGRAM_BOT_TOKEN_ENV} "
                f"y {TELEGRAM_CHAT_ID_ENV} estén configuradas",
                file=sys.stderr,
            )
            return 2
        bot_token, chat_id = credentials
        # Deliberately skips connect()/apply_schema()/seed_stores() below —
        # a connectivity check has no business touching the tracking database.
        client = build_http_client()
        try:
            TelegramReporter(client, bot_token, chat_id).send_text(
                "✅ Healthcheck del tracker — si ves este mensaje, la conexión "
                "a Telegram funciona correctamente."
            )
        finally:
            client.close()
        print("Mensaje de prueba enviado a Telegram.")
        return 0

    conn = connect(args.db)
    apply_schema(conn)
    seed_stores(conn)
    repository = SqliteOfferRepository(conn)

    try:
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
