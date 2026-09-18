# Verification Report: pokemon-tcg-offer-tracker

**Mode**: full artifacts (proposal, specs, design, tasks all present)
**Date**: 2026-09-17 (independently re-verified 2026-09-18 by sdd-verify)
**Verdict**: **PASS WITH WARNINGS**

## Completeness (tasks.md)

All 30 tasks (Phase 0–9) marked `[x]`. Independently confirmed against the filesystem —
every task's named artifact (source file or test file) exists and was exercised by the
test run below. No task is marked done without a corresponding artifact.

- Phase 5.2 (`PlaywrightTransport`) is marked done as "resolved-skip": the spike found
  no bot challenge (all probes 200, no `_abck`/`cf-ray`/JS-challenge markers), so per
  design.md's decision table row 1, `HttpxTransport` alone was built. Verified: no
  `PlaywrightTransport` class exists anywhere in `src/`; a documented, explicitly-skipped
  placeholder test (`tests/adapters/test_ripley_transport.py::test_playwright_transport_not_applicable`)
  records the reasoning. This is correct per design, not a shortcut.

## Command Evidence (independently re-run, not trusted from prior claims)

| Command | Result |
|---|---|
| `.venv/bin/pytest -q` | `136 passed, 1 deselected in 0.95s` — exit 0 |
| `.venv/bin/ruff check .` | `All checks passed!` — exit 0 |
| `.venv/bin/mypy --strict src/tracker` | `Success: no issues found in 28 source files` — exit 0 |
| `.venv/bin/pip show brotli` | brotli 1.2.0 installed; `httpx[brotli]>=0.27` present in `pyproject.toml` deps |

## Requirement-by-Requirement Spec Compliance Matrix

### offer-discovery

| Requirement | Evidence | Status |
|---|---|---|
| Per-Store Search Coverage | `cli/main.py::_build_adapters` wires all 4 slugs; `tests/integration/test_cli.py::test_json_output_shape` asserts `len(results)==4` | PASS |
| English-Variant Language Filtering (3 scenarios incl. "no marker") | `domain/matching.py::detect_language`; `tests/unit/test_matching.py::test_detect_language_from_title` incl. `Language.UNKNOWN` no-marker case; structured-attribute-wins tests | PASS |
| TCG Product-Type Filtering | `matching.py::detect_product_type`/`is_non_tcg_noise`; `test_matching.py::test_is_target_offer_false_for_non_tcg_noise`, `test_is_target_offer_false_for_sticker_collection_despite_collection_box_type` | PASS |
| Normalized Offer Output | Single `domain/model.Offer` dataclass used by every adapter; `test_vtex_parse.py`, `test_ilahui_parse.py`, `test_ripley_parse.py` all produce the same shape | PASS |
| Per-Store Failure Isolation (2 scenarios) | `application/track_offers.py` per-adapter try/except; `test_track_offers_use_case.py::test_failure_isolation_one_store_raises_others_still_complete`, `test_diff_isolation_across_stores_one_failure_does_not_skew_another` | PASS |
| Ripley Adapter Contract | `adapters/stores/ripley.py::RipleyAdapter` implements identical `fetch/parse/search`; `test_ripley_parse.py` | PASS |

### offer-change-detection (RECONCILED requirements — extra scrutiny applied)

| Requirement | Evidence | Status |
|---|---|---|
| Last-Known-State Diff Baseline (newest, not strictly-previous run) | `sqlite_offer_repository.py::last_known` (newest-observation SQL); `test_sqlite_repository.py::test_last_known_picks_newest_across_multiple_runs` (3 runs) | PASS |
| — Scenario: "store outage does not fabricate false NEW on recovery" (N ok, N+1 fail, N+2 ok) | **No automated test exercises this exact 3-run sequence.** Manually reproduced via a throwaway runtime script (real `TrackOffersUseCase` + real SQLite): run 1 ok/baseline, run 2 fails entirely, run 3 ok with unchanged product → `events == []`, confirmed no false NEW/RESTOCKED. Passed at runtime, but is not a checked-in regression test. | **WARNING** — verified-correct by manual execution, but a future regression in this exact path would not be caught by CI |
| New Offer Event (per-store scoped, not global-DB-empty) | `ChangeDetector.detect(..., store_has_history=True)` + `has_history` is `WHERE store_slug = ? AND status='ok'` (per-store, not global); `test_change_detection.py::test_new_offer_when_store_has_history`; `test_sqlite_repository.py::test_has_history_is_scoped_per_store` | PASS |
| First-Run Baseline Labeling (per-store, not global; Ripley baseline alongside Plaza Vea NEW) | `ChangeDetector.detect` branches on `store_has_history` per call, one call per adapter loop iteration in `track_offers.py`; `test_change_detection.py::test_per_store_baseline_alongside_established_store_new_event`; `test_track_offers_use_case.py::test_per_store_baseline_labeling_ripley_baseline_alongside_plaza_vea_new` (full use-case + real SQLite, asserts Ripley=BASELINE/store_has_history=False and Plaza Vea=NEW/store_has_history=True in the *same run*) | PASS — this is the most directly on-point test in the suite for the reconciled semantics |
| Restock Event | `test_change_detection.py::test_restocked_event`, `test_restocked_and_price_drop_can_both_fire_for_one_product` | PASS |
| Price Drop Event (incl. one-cent) | `test_change_detection.py::test_price_drop_event`, `test_price_drop_one_cent_still_qualifies` | PASS |
| Non-Reportable Transitions Recorded but Not Alerted (price rise, out-of-stock silent) | `test_change_detection.py::test_price_rise_is_silent`, `test_going_out_of_stock_is_silent`; persistence confirmed separately (observations always recorded regardless of event) via `sqlite_offer_repository.record_observations` called unconditionally in `track_offers.py` | PASS |
| Diff Isolation Across Stores | `test_track_offers_use_case.py::test_diff_isolation_across_stores_one_failure_does_not_skew_another` | PASS |

### offer-history-store

| Requirement | Evidence | Status |
|---|---|---|
| Store/Product/Observation/Run Schema | `schema.sql` — all 4+ tables present with FKs; `test_cli.py::test_db_file_is_created` | PASS |
| — Scenario: "one run row + one observation per matched offer" exactly | No single end-to-end test asserts exact row *counts* for a multi-store, multi-offer single run. Structurally guaranteed (one `INSERT INTO run` in `start_run`, one `INSERT INTO observation` per loop iteration in `record_observations`) and indirectly confirmed by `test_record_observations_is_append_only` (2 calls x 1 offer = 2 rows). Low risk given code triviality. | SUGGESTION (minor coverage gap, not a defect) |
| Append-Only Observation History | No `UPDATE`/`DELETE` on `observation` anywhere in `sqlite_offer_repository.py`; `test_sqlite_repository.py::test_record_observations_is_append_only` | PASS |
| Last-Known State Derivation | `_LAST_KNOWN_SQL` (`ORDER BY observed_at DESC, id DESC LIMIT 1` per product, correlated subquery); `test_last_known_picks_newest_across_multiple_runs` | PASS |
| Price History Query | `price_history()`; `test_price_history_returns_all_observations_in_chronological_order` | PASS |
| SKU/URL Change Yields New Product Identity | `UNIQUE(store_slug, external_id)` constraint; `test_sku_url_change_yields_new_product_identity` | PASS |

### offer-console-report

| Requirement | Evidence | Status |
|---|---|---|
| Console-Only Output Channel | `adapters/reporting/console.py` — plain `print()` only, no dashboard/bot/email code anywhere in the repo (`rg` confirms no such integration) | PASS |
| Event Report (mixed events all shown) | `test_console_reporter.py::test_mixed_events_are_all_shown` | PASS |
| — "No events" explicitly stated | `test_no_events_explicitly_states_no_changes` | PASS |
| First-Run Baseline Report (per-store, not conflated with another store's NEW) | `test_first_run_baseline_is_labeled_not_as_new`, `test_one_store_baseline_alongside_another_store_new_not_conflated` | PASS |
| Failed Store Visibility (distinct from zero-match success) | `test_failed_store_is_called_out_distinctly_from_zero_matches` | PASS |
| Current Matching Listing View | `current_listing()` repo method + CLI `--list` flag (added beyond design's literal port table, per apply-phase notes); `test_console_reporter.py::test_current_listing_view_prints_all_entries`, `test_cli.py::test_list_flag_reads_current_listing_without_network` | PASS |

## "All 4 Stores Fail" — Independently Re-Verified at Runtime (not just read from code)

Ran a real `TrackOffersUseCase` against a real in-memory SQLite DB with all 4 adapters
raising:

```
EXIT CODE: 2
RUN ROWS: [{'id': 1, 'status': 'failed', 'finished_at': '...'}]
STORE_RUN ROWS: 4 rows, all status='failed', with per-store error text
```

- Exit code 2: confirmed both by the automated `test_exit_code_two_when_all_stores_failed`
  (2-adapter case) and by this manual 4-adapter reproduction. PASS.
- Run row persisted even on total failure: confirmed (`run.status='failed'`, not silently
  dropped). PASS.
- Console explicitly reports total failure: **partially confirmed, with a caveat.**
  Every failed store is individually and explicitly printed (`[store] FAILED: <reason>`),
  satisfying the spec's literal "Failed Store Visibility" requirement. However, the reporter
  then still prints the generic `"No changes detected this run."` trailer line — the same
  wording used for a fully healthy zero-events run — because `ConsoleReporter.report`'s
  "any_reportable" flag treats a failed store the same as a store with zero matches (both
  return `False` from `_print_store_result`). Design.md's CLI section does not literally
  mandate distinct wording for a total-failure run, so this is not a spec-line violation,
  but it is a real readability gap: an operator skimming only the last line of output could
  read "No changes detected this run" as "everything is fine" immediately after 4 FAILED
  lines. **WARNING**, not CRITICAL — no spec text is contradicted, but flagged because the
  task brief specifically asked this to be checked against design.md's intent. No automated
  test asserts console *text content* for the all-failed case; only exit code is tested.

## Design Coherence

- Ordering invariant (`last_known`/`has_history` before `record_observations`) — explicitly
  regression-tested with a spy repository (`test_ordering_invariant.py`), including a
  meta-test proving the spy itself would catch a violation. Strong evidence.
- Per-store transaction isolation (design decision #9) — confirmed via
  `test_per_store_transaction_isolation_one_store_failure_does_not_lose_another` and the
  live 4-adapter reproduction above.
- Two deviations beyond the literal design text, both justified and low-risk:
  1. `current_listing()` / `--list` — added to satisfy "Current Matching Listing View", which
     design.md's port table didn't concretize a signature for. Correctly implements the spec
     requirement; no port-contract regression.
  2. `ruff` `line-length` raised 100→120 to fit accented Spanish fixture titles. Still
     enforced (not disabled); cosmetic, no risk.
- VTEX pagination header set was widened live (`resources` in addition to `content-range`/
  `resources-count-range`) — documented, tested (`test_fetch_paginates_using_resources_header`),
  no design contradiction (design's table was a starting assumption, not exhaustive).

## Open Questions (design.md) — Confirmed Nothing Silently Broke

1. **Ripley 403 root cause** — resolved by the spike: no block occurred, `HttpxTransport`
   suffices. Closed, not open.
2. **VTEX structured language attribute (`LANGUAGE_SPEC_KEYS`)** — genuinely still open:
   no real captured fixture (Plaza Vea or Oechsle) contains a specification key matching
   `Idioma`/`Language`/`Lenguaje`. The code path (`_extract_attributes` → `detect_language`)
   exists and is unit-tested with synthetic attribute dicts, but is never exercised by a real
   fixture. This is safe: `detect_language` unconditionally falls back to title-marker
   detection when no matching attribute is present, and all real fixtures classify correctly
   via title markers alone. Confirmed via `test_vtex_parse.py` assertions on real captured
   JSON. No silent breakage — correctly left open for v1 as design.md states.
3. **Ilahui `suggest.json` availability is an unverified Shopify convention** — the mandatory
   HTML fallback exists and is tested, including the newly-fixed 200-with-non-JSON-body edge
   case. Confirmed safe.
4. **`ilahuiperu.com` domain unconfirmed** — outside verification's scope (a business/content
   fact, not a code defect); the orchestrator's live smoke run already exercised this domain
   successfully twice, so it is de facto confirmed working, even though design.md's checkbox
   is unticked.

## Issues

### CRITICAL
None.

### WARNING
1. No automated regression test for offer-change-detection's "store outage does not
   fabricate false NEW events on recovery" scenario (3-run sequence: ok → fail → ok).
   Manually verified correct at runtime; recommend adding an integration test
   (`tests/integration/test_track_offers_use_case.py`) before archive so a future change
   can't silently regress this reconciled behavior.
2. `ConsoleReporter` prints the same `"No changes detected this run."` trailer after an
   all-stores-failed run as it does after a fully healthy zero-events run. Not a literal
   spec violation (per-store FAILED lines are present and distinct), but a real UX/clarity
   gap given the task brief's explicit ask to confirm "console explicitly reports total
   failure." No automated test covers console *text* for the all-failed case (only exit
   code is tested in `test_cli.py`).
3. Engram (`mem_*`) tools were unavailable in this execution context, consistent with every
   prior phase of this change. Verification proceeded on the authoritative filesystem
   artifacts per the task's explicit fallback instruction; this report is being written to
   both the OpenSpec file and (best-effort) Engram. If Engram write fails silently, the
   canonical copy is this file.

### SUGGESTION
1. No single test asserts exact row *counts* (`1 run row + N observation rows`) for a
   multi-store, multi-offer single run end-to-end — the behavior is structurally guaranteed
   by trivial code (one INSERT per `start_run` call, one INSERT per loop iteration) and
   indirectly covered, but a direct assertion would close the gap completely.
2. `pyproject.toml`'s `[ripley]` extra still declares `playwright`/`pytest-playwright` even
   though no code uses them. This is intentional (design decision #5's seam for a future
   browser transport) and not a defect — noted for awareness only.

## Final Verdict

**PASS WITH WARNINGS.** All 30 tasks are genuinely complete with corresponding artifacts.
Both spec/design/tasks correctness and design coherence were verified against the actual
implementation and confirmed independently via test execution, ruff, mypy, and targeted
manual runtime reproductions (all-4-stores-fail scenario, store-outage-recovery scenario).
The two reconciled per-store baseline/NEW requirements — the area flagged for extra
scrutiny — are correctly implemented and are, in fact, the most thoroughly tested part of
the codebase (unit + integration + SQLite-integration coverage of the exact Ripley-baseline-
alongside-Plaza-Vea-NEW scenario). No CRITICAL issues found. The WARNINGs are test-coverage
and UX-clarity gaps, not functional defects — safe to proceed to archive at the orchestrator's
discretion, optionally after adding the one missing regression test (WARNING #1).
