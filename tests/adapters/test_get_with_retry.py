"""`get_with_retry` (infrastructure/http/client.py) — retries once on a
non-2xx HTTP response before giving up; connection-level retries are a
separate, already-existing concern (`httpx.HTTPTransport(retries=...)`).
"""

from __future__ import annotations

import httpx
import pytest

from tracker.infrastructure.http.client import get_with_retry

URL = "https://example.test/thing"


@pytest.fixture(autouse=True)
def _no_real_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tracker.infrastructure.http.client.time.sleep", lambda seconds: None)


def _client_with_handler(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_succeeds_on_first_attempt_without_retrying() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text="ok")

    response = get_with_retry(_client_with_handler(handler), URL)

    assert response.status_code == 200
    assert calls == 1


def test_retries_once_and_succeeds_on_second_attempt() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, text="try again")
        return httpx.Response(200, text="ok")

    response = get_with_retry(_client_with_handler(handler), URL)

    assert response.status_code == 200
    assert calls == 2


def test_raises_the_last_error_after_exhausting_retries() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, text="not found")

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        get_with_retry(_client_with_handler(handler), URL)

    assert calls == 2  # one original attempt + one retry, default retries=1
    assert exc_info.value.response.status_code == 404


def test_sleeps_between_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "tracker.infrastructure.http.client.time.sleep", lambda seconds: sleep_calls.append(seconds)
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="error")

    with pytest.raises(httpx.HTTPStatusError):
        get_with_retry(_client_with_handler(handler), URL, retries=2, delay=3.5)

    assert sleep_calls == [3.5, 3.5]  # between attempt 1->2 and 2->3, not after the last


def test_zero_retries_means_no_second_attempt() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, text="error")

    with pytest.raises(httpx.HTTPStatusError):
        get_with_retry(_client_with_handler(handler), URL, retries=0)

    assert calls == 1
