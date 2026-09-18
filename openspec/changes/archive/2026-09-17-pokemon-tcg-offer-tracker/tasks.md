# Tasks: Pokémon TCG 30th Anniversary Offer Tracker

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~3000-3200 (source ~1475 + tests ~1600, greenfield) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR1 -> PR2 -> PR3 -> PR4 -> PR5 (feature-branch-chain suggested; user decides) |
| Delivery strategy | single-pr |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

Rationale: greenfield package, no existing code to trim against. Source estimate: domain ~200,
application ~175, VTEX+Ilahui adapters ~350, Ripley adapter+transports ~200, persistence ~175,
CLI ~100, reporting ~80, http client ~50, pyproject/config ~65, spike script ~80. Tests are
written alongside each unit (TDD-flavored) and roughly match/exceed source volume (~1600 lines).
Total is ~7.5x the 400-line budget. `delivery_strategy=single-pr` means the orchestrator MUST
require explicit `size:exception` acceptance before `sdd-apply`, OR the user switches to a chained
strategy using the work units below (feature-branch-chain recommended for rollback control on a
greenfield package; stacked-to-main is viable if speed is preferred over isolation).

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Ripley spike + pure domain layer | PR1 (base: tracker branch) | `pytest tests/unit -q` | `python scripts/spike_ripley.py` (manual, live, not CI) | Revert `src/tracker/domain/`, `scripts/spike_ripley.py`, `pyproject.toml`; nothing else depends on it yet |
| 2 | Application ports/DTOs + SQLite persistence | PR2 (base: PR1 branch) | `pytest tests/integration/test_sqlite_repository.py -q` | N/A — DB-only integration test is the real scenario, no live call needed | Revert `src/tracker/application/`, `src/tracker/infrastructure/persistence/`; ports are structural Protocols, no adapter breaks |
| 3 | VTEX adapters (Plaza Vea, Oechsle) + console reporter | PR3 (base: PR2 branch) | `pytest tests/adapters/test_vtex_parse.py tests/adapters/test_vtex_fetch.py tests/adapters/test_console_reporter.py -q` | N/A — `httpx.MockTransport` covers `fetch()`; no live call needed for review | Revert `vtex.py`, `plaza_vea.py`, `oechsle.py`, `reporting/console.py` |
| 4 | Ilahui adapter + Ripley adapter (per spike outcome) | PR4 (base: PR3 branch) | `pytest tests/adapters/test_ilahui_parse.py tests/adapters/test_ripley_parse.py tests/adapters/test_ripley_transport.py -q` | `pytest -m playwright tests/adapters/test_ripley_transport.py` only if spike selected `PlaywrightTransport`; else N/A | Revert `ilahui.py`, `ripley.py`; other two adapters keep working independently |
| 5 | Use-case wiring + ordering-invariant regression + CLI | PR5 (base: PR4 branch, or tracker branch to merge) | `pytest tests/integration -q` | `python -m tracker.cli.main --db ./tracker.db --store plaza_vea` (manual smoke, optional) | Revert `track_offers.py`, `cli/main.py`; persistence/adapters remain independently valid |

## Phase 0: Ripley Spike (run first — blocks Ripley adapter only)

- [x] 0.1 Create `scripts/spike_ripley.py`: 5-probe ladder (`robots.txt`, `/`, `/search/pokemon`, warmed-cookie repeat, VTEX-style `products/search?ft=pokemon`) against `simple.ripley.com.pe` with the design's full browser header set.
- [x] 0.2 Log per probe: status, `server`, `set-cookie` names, `cf-ray`/`x-akamai-*`/`x-amzn-waf-*`, first 2KB of body. Apply the decision rule (200 -> Httpx; 403+challenge markers -> Playwright; 403 everywhere, no markers -> escalate as geo/IP open question).
- [x] 0.3 Save the best response body to `tests/fixtures/ripley/` regardless of outcome; record the chosen transport decision to gate Phase 5.

## Phase 1: Project Foundation

- [x] 1.1 Create `pyproject.toml`: metadata, `httpx`/`selectolax` deps, `[project.optional-dependencies] ripley` (`playwright`, `pytest-playwright`), pytest markers (`live`, `playwright`), `addopts = -m "not live and not playwright"`, ruff/mypy strict config.
- [x] 1.2 Create `tests/conftest.py`: `fixture_body(store, name)`, `FrozenClock`, `tmp_db` fixture.

## Phase 2: Domain Layer (pure, no I/O — parallel to Phase 0/4/5)

- [x] 2.1 `src/tracker/domain/model.py`: `Money`, `Offer`, `Language`, `Availability`, `ProductType`, `OfferKey`. Test `tests/unit/test_model.py`: Decimal arithmetic, currency-mismatch comparison raises.
- [x] 2.2 `src/tracker/domain/matching.py`: `normalize`, `detect_language`, `detect_product_type`, `is_anniversary`, `is_non_tcg_noise`, `is_target_offer`. Test `tests/unit/test_matching.py`: parametrized real titles + edge cases (no accent, `(ENG)`, no marker, plush/figure noise, Spanish variant) — covers offer-discovery language/product-type scenarios.
- [x] 2.3 `src/tracker/domain/events.py`: `ChangeKind`, `ChangeEvent`.
- [x] 2.4 `src/tracker/domain/change_detection.py`: `ChangeDetector.detect(previous, current, store_has_history)`. Test `tests/unit/test_change_detection.py`: baseline, new, restocked, price_drop (incl. one-cent), price rise/equal/`None` (no event), disappeared product, failed-store isolation — covers every offer-change-detection scenario, including per-store baseline/NEW semantics.
- [x] 2.5 `src/tracker/domain/errors.py`: `StoreFetchError`, `StoreParseError`.

## Phase 3: Application Ports & DTOs (depends on 2.1, 2.3)

- [x] 3.1 `src/tracker/application/dto.py`: `RawPayload`, `ObservationSnapshot`, `StoreResult`, `RunOutcome`.
- [x] 3.2 `src/tracker/application/ports.py`: `StoreAdapter`, `OfferRepository`, `Reporter`, `Clock` Protocols with exact signatures from design.

## Phase 4: Non-Ripley Adapters (parallel to Phase 0/5; depends on Phase 2/3)

- [x] 4.1 Capture fixtures `tests/fixtures/plaza_vea/*.json`, `tests/fixtures/oechsle/*.json` (sample VTEX search payloads).
- [x] 4.2 `src/tracker/infrastructure/http/client.py`: `httpx.Client` factory, browser-like headers, retry/timeout policy.
- [x] 4.3 `src/tracker/adapters/stores/vtex.py`: `VtexStoreAdapter` base — `fetch()` with `content-range` pagination (`MAX_PAGES=5`, missing header = single page), pure `parse_products()`. Test `tests/adapters/test_vtex_parse.py` (fixture-driven field mapping) + `test_vtex_fetch.py` (`httpx.MockTransport`: URL/params/headers, pagination loop, 403/500/timeout -> `StoreFetchError`).
- [x] 4.4 `src/tracker/adapters/stores/plaza_vea.py`, `oechsle.py`: store-specific config over `VtexStoreAdapter`.
- [x] 4.5 Capture fixtures `tests/fixtures/ilahui/*.json,*.html` (suggest.json + HTML search fallback samples).
- [x] 4.6 `src/tracker/adapters/stores/ilahui.py`: primary `suggest.json` parse, `selectolax` HTML fallback, branch by `RawPayload.content_type`. Test `tests/adapters/test_ilahui_parse.py` (both branches).
- [x] 4.7 `src/tracker/adapters/reporting/console.py`: `ConsoleReporter` — event report, explicit no-changes statement, per-store baseline vs NEW labeling, failed-store visibility, current-listing view. Test `tests/adapters/test_console_reporter.py` via `capsys` — covers every offer-console-report scenario.

## Phase 5: Ripley Adapter (gated on Phase 0 outcome; depends on 3, 4.3)

- [x] 5.1 `src/tracker/adapters/stores/ripley.py`: `RipleyAdapter(transport=...)` + `HttpxTransport`; parse branch selects `vtex.parse_products` (if probe 5 answered) or a Ripley HTML card parser, chosen by `content_type`.
- [x] 5.2 If (and only if) spike found a JS challenge: add `PlaywrightTransport` (`page.goto` + `page.content()`) behind the `[ripley]` extra. Otherwise record the geo/IP open question as resolved-skip.
- [x] 5.3 Test `tests/adapters/test_ripley_parse.py` reading Phase 0's saved fixtures. Test `tests/adapters/test_ripley_transport.py`: `HttpxTransport` via `MockTransport`; `PlaywrightTransport` via `page.route`, marked `@pytest.mark.playwright`, skipped when browsers absent.

## Phase 6: Persistence (depends on Phase 3 only; parallel to Phase 4/5)

- [x] 6.1 `src/tracker/infrastructure/persistence/schema.sql`: `store`, `run`, `store_run`, `product`, `observation` tables + indexes exactly per design DDL.
- [x] 6.2 `src/tracker/infrastructure/persistence/connection.py`: `connect()`, `apply_schema()` (idempotent `CREATE TABLE IF NOT EXISTS`), `seed_stores()`.
- [x] 6.3 `src/tracker/infrastructure/persistence/sqlite_offer_repository.py`: implement `OfferRepository` — `start_run`/`finish_run`/`record_store_run`/`has_history`/`last_known`/`record_observations`/`price_history`. `has_history` scoped strictly per `store_slug` with `status='ok'`.
- [x] 6.4 Test `tests/integration/test_sqlite_repository.py`: real `sqlite3` on `tmp_path` — `last_known` picks newest across multiple runs, `has_history` per-store semantics, `price_history` ordering, FK cascade, per-store transaction isolation, append-only inserts, SKU/URL-change new-identity behavior.

## Phase 7: Use Case & Ordering Invariant (depends on Phase 2, 3, 6)

- [x] 7.1 `src/tracker/application/track_offers.py`: `TrackOffersUseCase.execute()` — per-store `try/except` isolation, own transaction per store, dedupe by key, apply `is_target_offer`.
- [x] 7.2 **Load-bearing regression test** `tests/integration/test_ordering_invariant.py`: assert `repo.last_known()`/`repo.has_history()` are read for a store BEFORE `repo.record_observations()` writes that store's current-run data. Use a spy/fake repository that fails the test if read happens after write — without this, every product would diff against itself.
- [x] 7.3 Test `tests/integration/test_track_offers_use_case.py`: fake in-memory adapters (one raising) — failure isolation, two-run diff producing exactly the expected events, per-store baseline labeling (Ripley baseline alongside Plaza Vea NEW in the same run), diff isolation across stores.

## Phase 8: CLI Composition Root (depends on all above)

- [x] 8.1 `src/tracker/cli/main.py`: argparse — `--db PATH`, `--store SLUG` (repeatable), `--query TEXT` (repeatable, default queries), `--json`, `--history STORE:EXTERNAL_ID`; wires `httpx.Client`, adapters, SQLite conn, repo, reporter, clock; exit codes `0`/`1`/`2`.
- [x] 8.2 Test `tests/integration/test_cli.py`: exit-code matrix (all ok / partial failure / all failed), `--json` output shape, `--history` flag.

## Phase 9: Live/E2E Guard

- [x] 9.1 Verify `pyproject.toml` `addopts` deselects `not live and not playwright` by default; confirm `scripts/spike_ripley.py` and any `@pytest.mark.live` test never run in default CI selection.

## Implementation Resolution Notes (apply phase, 2026-09-17)

All 30 tasks complete, 30/30. `pytest tests/unit tests/adapters tests/integration -q`
-> 131 passed, 1 deselected. `ruff check` clean. `mypy --strict src/tracker` clean.

- **Ripley spike outcome**: unambiguous. Every probe in the live ladder returned HTTP 200
  with zero bot-challenge markers (`tests/fixtures/ripley/spike_summary.json`). Per the
  design's decision rule row 1, this resolves to `HttpxTransport` only —
  **`PlaywrightTransport` was not built**, not even as a stub, because there was nothing
  ambiguous to gate: the "403 everywhere, no markers" geo/IP row never applied. The
  `[ripley]` extra and `Transport` protocol remain in place as the seam design decision #5
  describes, so a browser transport could be added later without touching the port.
- Probe 5 (`/api/catalog_system/pub/products/search?ft=pokemon`) answered 404: Ripley is
  not VTEX. It is a Next.js app; `ripley.py` parses the embedded `__NEXT_DATA__` JSON
  script rather than scraping visible HTML card markup.
- Real live captures (VTEX pagination header, Ilahui `suggest.json`/HTML fallback, Ripley
  page shape) confirmed the design's assumed field mappings, with one correction: VTEX's
  actual pagination header observed live is `resources: "0-49/N"`, not only
  `content-range`/`resources-count-range` — `vtex.py` checks all three header names.
- Live data drove two matching-rule fixes beyond the original design text (both covered by
  new unit tests against the real titles that exposed them): Spanish descriptive text
  ("Caja de Entrenador Élite", "Colección") needed Spanish product-type markers even for
  English-language cards, and the Spanish ordinal "30.º Aniversario" needed an explicit
  anniversary-marker variant (NFKD decomposes "º" to a plain "o", producing "30.o
  aniversario" after normalization).
- `matching.py`'s `ObservationLike` Protocol (used by `change_detection.py` for hexagonal
  purity — domain must not import `application.dto.ObservationSnapshot`) uses read-only
  `@property` members, not plain attributes: `ObservationSnapshot` is a frozen dataclass,
  and mypy strict treats frozen dataclass fields as read-only, which a plain-attribute
  Protocol member rejects structurally.
- Added a `current_listing()` repository method and CLI `--list` flag beyond the design's
  literal port table, to satisfy the offer-console-report "Current Matching Listing View"
  MUST requirement, which design.md did not concretize a port signature for.
- `ruff` `line-length` raised from 100 to 120 (still enforced, not disabled) to
  accommodate real accented Spanish product titles in test fixtures/parametrize tables
  without awkward mid-string wraps.
