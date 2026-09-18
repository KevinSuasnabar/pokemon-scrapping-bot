"""One-time diagnostic probe against Ripley (simple.ripley.com.pe).

This is NOT part of the shipped code path and is NOT exercised by the default
pytest selection (see `pyproject.toml`'s `addopts`). It is a live-network
script whose sole purpose is to determine, per `design.md`'s decision rule,
whether the Ripley adapter needs `HttpxTransport` or `PlaywrightTransport`.

Run manually:

    .venv/bin/python scripts/spike_ripley.py

Probe ladder (per design.md "Ripley Spike"):
    1. GET /robots.txt
    2. GET /
    3. GET /search/pokemon
    4. repeat 2-3 with the cookie jar warmed by step 2
    5. GET /api/catalog_system/pub/products/search?ft=pokemon  (Ripley may be VTEX)

For every probe we record: status code, `server` header, `set-cookie` cookie
names (not values), any `cf-ray` / `x-akamai-*` / `x-amzn-waf-*` headers, and
the first 2 KB of the response body. We then apply the design's decision rule
and print the outcome. The best response body is saved to
`tests/fixtures/ripley/` regardless of outcome, so parse tests exist even if
the live probe is later unreachable from a different environment (CI, a
reviewer's machine, geo-blocked IP, etc).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx

BASE_URL = "https://simple.ripley.com.pe"
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "ripley"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "es-PE,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-CH-UA": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.google.com/",
}

CHALLENGE_MARKERS = ("_abck", "cf_chl", "cf-ray", "cf-chl", "__cf_bm", "akamai")


@dataclass
class ProbeResult:
    name: str
    url: str
    status: int | None
    server: str | None
    set_cookie_names: list[str]
    challenge_headers: dict[str, str]
    body_preview: str
    error: str | None = None
    body_full: str = field(default="", repr=False)
    content_type: str = "text/html"


def _extract_challenge_headers(headers: httpx.Headers) -> dict[str, str]:
    found: dict[str, str] = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower.startswith("cf-") or lower.startswith("x-akamai") or lower.startswith("x-amzn-waf"):
            found[key] = value
    return found


def _cookie_names(headers: httpx.Headers) -> list[str]:
    names = []
    for value in headers.get_list("set-cookie"):
        name = value.split("=", 1)[0].strip()
        if name:
            names.append(name)
    return names


def probe(client: httpx.Client, name: str, path: str) -> ProbeResult:
    url = f"{BASE_URL}{path}"
    try:
        resp = client.get(url, headers=BROWSER_HEADERS, timeout=15.0, follow_redirects=True)
    except httpx.HTTPError as exc:
        return ProbeResult(
            name=name,
            url=url,
            status=None,
            server=None,
            set_cookie_names=[],
            challenge_headers={},
            body_preview="",
            error=str(exc),
        )
    body = resp.text
    return ProbeResult(
        name=name,
        url=url,
        status=resp.status_code,
        server=resp.headers.get("server"),
        set_cookie_names=_cookie_names(resp.headers),
        challenge_headers=_extract_challenge_headers(resp.headers),
        body_preview=body[:2048],
        body_full=body,
        content_type=resp.headers.get("content-type", "text/html"),
    )


def has_challenge_markers(result: ProbeResult) -> bool:
    if result.challenge_headers:
        return True
    lowered = result.body_preview.lower()
    return any(marker in lowered for marker in CHALLENGE_MARKERS)


def print_result(result: ProbeResult) -> None:
    print(f"\n--- probe: {result.name} ({result.url}) ---")
    if result.error:
        print(f"  ERROR: {result.error}")
        return
    print(f"  status: {result.status}")
    print(f"  server: {result.server}")
    print(f"  set-cookie names: {result.set_cookie_names}")
    print(f"  challenge headers: {result.challenge_headers}")
    print(f"  body preview (first 300 chars): {result.body_preview[:300]!r}")


def apply_decision_rule(results: list[ProbeResult]) -> str:
    """Return one of: 'httpx', 'playwright', 'ambiguous'."""
    if any(r.status == 200 for r in results):
        return "httpx"
    all_403 = all(r.status == 403 for r in results if r.error is None)
    any_challenge = any(has_challenge_markers(r) for r in results)
    if all_403 and any_challenge:
        return "playwright"
    if all_403 and not any_challenge:
        return "ambiguous"
    return "ambiguous"


def save_best_fixture(results: list[ProbeResult]) -> Path | None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    candidates = [r for r in results if r.error is None and r.body_full]
    if not candidates:
        return None
    best = max(candidates, key=lambda r: (r.status == 200, len(r.body_full)))
    is_json = "json" in best.content_type
    suffix = "json" if is_json else "html"
    out_path = FIXTURES_DIR / f"spike_{best.name}.{suffix}"
    out_path.write_text(best.body_full, encoding="utf-8")
    return out_path


def main() -> int:
    results: list[ProbeResult] = []
    with httpx.Client() as client:
        r1 = probe(client, "robots_txt", "/robots.txt")
        results.append(r1)
        r2 = probe(client, "home", "/")
        results.append(r2)
        r3 = probe(client, "search_pokemon", "/search/pokemon")
        results.append(r3)
        # Step 4: repeat 2-3 with the cookie jar warmed by step 2 (same client, cookies persist)
        r4 = probe(client, "home_warmed", "/")
        results.append(r4)
        r5 = probe(client, "search_pokemon_warmed", "/search/pokemon")
        results.append(r5)
        r6 = probe(client, "vtex_products_search", "/api/catalog_system/pub/products/search?ft=pokemon")
        results.append(r6)

    for result in results:
        print_result(result)

    decision = apply_decision_rule(results)
    saved = save_best_fixture(results)

    print("\n=== DECISION ===")
    print(f"Outcome: {decision}")
    if decision == "httpx":
        print("At least one probe returned 200 -> use HttpxTransport.")
        vtex_probe = results[-1]
        if vtex_probe.status == 200:
            print("Probe 5 (VTEX-style products/search) answered 200 -> reuse the VTEX parser.")
    elif decision == "playwright":
        print("403 with challenge markers on every probe -> use PlaywrightTransport.")
    else:
        print(
            "403 on every probe with NO challenge markers -> likely geo/IP block. "
            "This is an OPEN QUESTION, not silently resolved to Playwright. "
            "See design.md 'Open Questions' and proposal.md Risks."
        )
    if saved:
        print(f"\nBest response body saved to: {saved}")
    else:
        print("\nNo response body could be saved (all probes errored).")

    summary = {
        "decision": decision,
        "probes": [
            {
                "name": r.name,
                "status": r.status,
                "server": r.server,
                "set_cookie_names": r.set_cookie_names,
                "challenge_headers": r.challenge_headers,
                "error": r.error,
            }
            for r in results
        ],
        "saved_fixture": str(saved) if saved else None,
    }
    summary_path = FIXTURES_DIR / "spike_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Summary JSON written to: {summary_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
