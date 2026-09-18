# Proposal: Pokémon TCG 30th Anniversary Offer Tracker

## Intent

Pokémon TCG 30th Anniversary English-language stock in Peru is scarce and volatile — exploration saw
stock change between fetches in one session. Manually checking four storefronts misses the only
moments that matter (restock, price drop), and with no stored history a buyer cannot tell what is
new versus already seen.

## Scope

### In Scope
- CLI run that queries Plaza Vea, Oechsle, Ripley, Ilahui (ilahuiperu.com) for 30th Anniversary TCG
  products and filters to the English variant.
- Change detection across runs on **both** price and availability: (a) new matching offer,
  (b) out-of-stock → in-stock, (c) price drop on a previously seen offer.
- SQLite persistence (stdlib `sqlite3`) holding last-known state per (store, product) plus an
  append-only observation history for later price queries.
- Console-only report of the run's changes.
- Ripley via Playwright if plain HTTP stays blocked — committed v1 scope, not best-effort.
- pytest suite over recorded fixtures; no live network in tests.

### Out of Scope
- Web dashboard, bot, email, or push notifications.
- Multi-currency or FX conversion (PEN only).
- Purchase automation, cart, checkout, alerts scheduling/cron packaging.
- Stores beyond the four named. Plugin-style extensibility is a nice-to-have if the port design
  yields it for free; it is not a requirement.
- Spanish-variant tracking, non-TCG 30th Anniversary merchandise.

## Capabilities

### New Capabilities
- `offer-discovery`: per-store search, English/TCG matching, normalization to a common `Offer`.
- `offer-change-detection`: run-over-run diff producing new / restocked / price-drop events.
- `offer-history-store`: SQLite state + observation history.
- `offer-console-report`: console rendering of detected changes and current matches.

### Modified Capabilities
- None (greenfield project).

## Approach

Hexagonal. `StoreAdapter` port (`search(query) -> list[Offer]`), one adapter per store, each
splitting `fetch()` (I/O) from `parse()` (pure) so matching stays unit-testable without network
mocking. `ChangeDetector` domain service diffs the run against last-known state. `OfferRepository`
port implemented by SQLite; `Reporter` port implemented by console. A use case runs adapters with
per-adapter failure isolation — one store failing must not abort the run or corrupt other diffs.

Mixed fetch strategy: httpx for VTEX (Plaza Vea, Oechsle) and Shopify (Ilahui); Playwright confined
to the Ripley adapter so the browser dependency never reaches the domain.

### Persistence shape (high level; DDL belongs to design)
- **store** — the four retailers.
- **product** — identity per (store, external SKU/URL) plus title, language, product type.
- **observation** — append-only per run per product: price, currency, availability, timestamp.
- **run** — one row per execution, for provenance and previous-run resolution.

Last-known state = newest observation per product; the diff compares this run's observations to it.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/` domain + ports | New | `Offer`, `StoreAdapter`, `OfferRepository`, `Reporter`, `ChangeDetector` |
| `src/` adapters | New | 3 HTTP adapters + 1 Playwright adapter |
| `src/` persistence | New | SQLite repository + schema bootstrap/migration |
| `tests/` | New | pytest + recorded fixtures per adapter |
| project root | New | packaging, dependency manifest, CLI entry point |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Ripley 403 root cause unverified; may resist Playwright if geo/IP-based | High | Spike one raw HTTP client with realistic headers before building the adapter; failure isolation keeps other stores green |
| Title-based English matching is a heuristic ("Ingles", "(ENG)", no marker); Ripley conventions unknown | Med | Prefer structured VTEX language attribute where present; keep matching rules in one pure, fixture-tested module |
| VTEX pagination and full JSON schema only summarized by WebFetch | Med | Verify with a raw client during design; do not lock the adapter contract before |
| Site markup/API drift silently empties results | Med | Report zero-result stores explicitly instead of as "no changes" |
| False positives from non-TCG 30th Anniversary merch | Med | Explicit product-type filter (ETB/Booster/Box/Sobres) alongside language filter |

## Rollback Plan

Greenfield, no production consumers. Revert the change branch; delete the local SQLite file to drop
accumulated history. No external state or migrations to unwind.

## Dependencies

- Python 3.11+, httpx (or requests), pytest.
- Playwright plus browser binaries — required only if the Ripley HTTP spike confirms the block.
- Network access to four third-party storefronts whose availability we do not control.

## Success Criteria

- [ ] One command queries all four stores and prints matching English 30th Anniversary offers.
- [ ] A second run after a simulated price/stock change reports new, restocked, and price-drop events.
- [ ] A failing store is reported and does not abort the run or corrupt other stores' diffs.
- [ ] Price history for a product is queryable from the SQLite store.
- [ ] pytest suite passes offline against recorded fixtures.
