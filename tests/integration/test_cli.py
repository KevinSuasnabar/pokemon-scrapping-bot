"""CLI integration: exit-code matrix, `--json` output shape, `--list` flag.

Network is faked at the `httpx.Client` transport level (via monkeypatched
adapter factories), matching the project's "no live network in tests" rule.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tracker.cli import main as cli_main
from tracker.domain.model import Availability, Language, Money, Offer, ProductType

NOW = datetime(2026, 1, 15, tzinfo=UTC)


class _FakeAdapter:
    def __init__(self, store_slug: str, *, fail: bool = False) -> None:
        self.store_slug = store_slug
        self._fail = fail

    def fetch(self, query: str):
        if self._fail:
            raise RuntimeError(f"{self.store_slug}: simulated failure")
        return [object()]

    def parse(self, payloads, observed_at):
        return [
            Offer(
                store=self.store_slug,
                external_id="p1",
                title="Pokemon TCG 30 Aniversario ETB En Ingles",
                url="https://example.test/p1",
                price=Money(amount=Decimal("99.90")),
                availability=Availability.IN_STOCK,
                language=Language.ENGLISH,
                product_type=ProductType.ETB,
                observed_at=observed_at,
            )
        ]

    def search(self, query, observed_at):
        return self.parse(self.fetch(query), observed_at)


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "cli_test.db")


def _patch_all_ok(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_main,
        "_build_adapters",
        lambda client, store_filter: [
            _FakeAdapter("plaza_vea"),
            _FakeAdapter("oechsle"),
            _FakeAdapter("ripley"),
            _FakeAdapter("ilahui"),
        ],
    )


def _patch_partial_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_main,
        "_build_adapters",
        lambda client, store_filter: [
            _FakeAdapter("plaza_vea"),
            _FakeAdapter("ripley", fail=True),
        ],
    )


def _patch_all_failed(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_main,
        "_build_adapters",
        lambda client, store_filter: [
            _FakeAdapter("plaza_vea", fail=True),
            _FakeAdapter("ripley", fail=True),
        ],
    )


def test_exit_code_zero_when_all_stores_ok(monkeypatch, db_path: str, capsys) -> None:
    _patch_all_ok(monkeypatch)
    exit_code = cli_main.main(["--db", db_path])
    assert exit_code == 0


def test_exit_code_one_on_partial_failure(monkeypatch, db_path: str, capsys) -> None:
    _patch_partial_failure(monkeypatch)
    exit_code = cli_main.main(["--db", db_path])
    assert exit_code == 1


def test_exit_code_two_when_all_stores_failed(monkeypatch, db_path: str, capsys) -> None:
    _patch_all_failed(monkeypatch)
    exit_code = cli_main.main(["--db", db_path])
    assert exit_code == 2


def test_json_output_shape(monkeypatch, db_path: str, capsys) -> None:
    _patch_all_ok(monkeypatch)
    cli_main.main(["--db", db_path, "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "run_id" in payload
    assert "started_at" in payload
    assert "results" in payload
    assert len(payload["results"]) == 4
    for result in payload["results"]:
        assert {"store", "status", "offer_count", "error", "store_has_history", "events"} <= set(
            result.keys()
        )


def test_json_output_is_the_only_stdout_content(monkeypatch, db_path: str, capsys) -> None:
    _patch_all_ok(monkeypatch)
    cli_main.main(["--db", db_path, "--json"])
    out = capsys.readouterr().out.strip()
    json.loads(out)  # must parse as a single JSON document, no console text mixed in


def test_list_flag_reads_current_listing_without_network(monkeypatch, db_path: str, capsys) -> None:
    _patch_all_ok(monkeypatch)
    cli_main.main(["--db", db_path])  # populate
    capsys.readouterr()

    exit_code = cli_main.main(["--db", db_path, "--list", "--json"])
    out = capsys.readouterr().out
    assert exit_code == 0
    entries = json.loads(out)
    assert len(entries) == 4  # one per store


def test_interval_rejects_non_positive_value(db_path: str, capsys) -> None:
    exit_code = cli_main.main(["--db", db_path, "--interval", "0"])
    assert exit_code == 2
    assert "positivo" in capsys.readouterr().err


def test_interval_cannot_combine_with_list(db_path: str, capsys) -> None:
    exit_code = cli_main.main(["--db", db_path, "--interval", "5", "--list"])
    assert exit_code == 2
    assert "--interval" in capsys.readouterr().err


def test_interval_loop_runs_repeatedly_until_keyboard_interrupt(
    monkeypatch, db_path: str, capsys
) -> None:
    """Regression: the loop must not auto-stop on its own once it finds
    something (user's explicit choice) — only a manual interrupt (Ctrl+C,
    surfaced here as `time.sleep` raising `KeyboardInterrupt`) stops it."""
    _patch_all_ok(monkeypatch)
    sleep_calls: list[int] = []

    def _fake_sleep(seconds: int) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli_main.time, "sleep", _fake_sleep)

    exit_code = cli_main.main(["--db", db_path, "--interval", "7"])

    assert exit_code == 0
    assert sleep_calls == [7, 7, 7]  # ran 3 times before being interrupted
    out = capsys.readouterr()
    assert out.out.count("Corrida #") == 3  # each iteration printed its own run report
    assert "Detenido por el usuario" in out.err


def test_interval_loop_survives_an_unexpected_exception_mid_run(
    monkeypatch, db_path: str, capsys
) -> None:
    """Regression: an error outside the per-store isolation boundary (e.g. a
    transient DB error) must not kill an unattended multi-hour loop — it
    should log and continue to the next interval, not crash silently."""
    _patch_all_ok(monkeypatch)
    call_count = 0
    original_run_once = cli_main._run_once

    def _flaky_run_once(repository, client, args):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("simulated unexpected failure")
        return original_run_once(repository, client, args)

    monkeypatch.setattr(cli_main, "_run_once", _flaky_run_once)

    sleep_calls: list[int] = []

    def _fake_sleep(seconds: int) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli_main.time, "sleep", _fake_sleep)

    exit_code = cli_main.main(["--db", db_path, "--interval", "5"])

    assert exit_code == 0  # the loop itself still exits cleanly via Ctrl+C
    assert call_count == 3  # run 2's crash didn't stop run 3 from happening
    err = capsys.readouterr().err
    assert "Error inesperado en esta corrida (continuando): simulated unexpected failure" in err
    assert "Detenido por el usuario" in err


def test_dotenv_file_is_loaded_for_telegram_credentials(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    """Regression: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID should be readable
    from a .env file, not just real shell-exported env vars."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=dotenv-token\nTELEGRAM_CHAT_ID=dotenv-chat\n")
    monkeypatch.chdir(tmp_path)

    sent_outcomes = []

    class _FakeTelegramReporter:
        def __init__(self, client, bot_token, chat_id) -> None:
            assert bot_token == "dotenv-token"
            assert chat_id == "dotenv-chat"

        def report(self, outcome) -> None:
            sent_outcomes.append(outcome)

        def report_listing(self, entries) -> None:
            pass

    monkeypatch.setattr(cli_main, "TelegramReporter", _FakeTelegramReporter)
    monkeypatch.setattr(
        cli_main,
        "_build_adapters",
        lambda client, store_filter: [_FakeAdapter("plaza_vea")],
    )

    exit_code = cli_main.main(["--db", "cli_test.db", "--telegram"])

    assert exit_code == 0
    assert len(sent_outcomes) == 1


def test_telegram_requires_env_vars(monkeypatch, db_path: str, tmp_path: Path, capsys) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    # Isolate from any real .env this developer's own project root may have
    # (load_dotenv() would otherwise silently repopulate the vars just
    # deleted above, from a .env in a parent directory of the real cwd).
    monkeypatch.chdir(tmp_path)
    exit_code = cli_main.main(["--db", db_path, "--telegram"])
    assert exit_code == 2
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err


def test_telegram_cannot_combine_with_list(monkeypatch, db_path: str, capsys) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "fake-chat-id")
    exit_code = cli_main.main(["--db", db_path, "--telegram", "--list"])
    assert exit_code == 2
    assert "--telegram" in capsys.readouterr().err


def test_telegram_sends_via_composite_reporter_when_configured(
    monkeypatch, db_path: str, capsys
) -> None:
    """The CLI wires ConsoleReporter + TelegramReporter through
    CompositeReporter under --telegram: console output must still happen,
    and the (faked) Telegram side must receive the same in-stock event."""
    _patch_all_ok(monkeypatch)  # _FakeAdapter always returns an IN_STOCK offer
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "fake-chat-id")

    sent_outcomes = []

    class _FakeTelegramReporter:
        def __init__(self, client, bot_token, chat_id) -> None:
            assert bot_token == "fake-token"
            assert chat_id == "fake-chat-id"

        def report(self, outcome) -> None:
            sent_outcomes.append(outcome)

        def report_listing(self, entries) -> None:
            pass

    monkeypatch.setattr(cli_main, "TelegramReporter", _FakeTelegramReporter)

    exit_code = cli_main.main(["--db", db_path, "--telegram"])

    assert exit_code == 0
    assert len(sent_outcomes) == 1
    out = capsys.readouterr().out
    assert "Corrida #" in out  # console output still happened alongside Telegram


def test_db_file_is_created(monkeypatch, db_path: str, capsys) -> None:
    _patch_all_ok(monkeypatch)
    cli_main.main(["--db", db_path])
    assert Path(db_path).exists()
    conn = sqlite3.connect(db_path)
    tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    conn.close()
    assert {"store", "run", "store_run", "product", "current_state"} <= tables
