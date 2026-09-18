# Design: Pokémon TCG 30th Anniversary Offer Tracker

## Technical Approach

Hexagonal Python package. The domain (`Offer`, `Money`, matching rules, change detection) has zero
I/O imports. One `StoreAdapter` port per the proposal, split into `fetch()` (I/O, returns an opaque
`RawPayload`) and `parse()` (pure, `RawPayload -> list[Offer]`), so every parsing and classification
rule is testable from a saved fixture with no network and no mock HTTP stack. Store-specific
transport variation (httpx vs. Playwright) lives *inside* the Ripley adapter as an injected
transport, so the Ripley spike's outcome never changes the port contract. Filtering
(English + TCG + 30th Anniversary) lives in a single pure module consumed by the use case, not
duplicated per adapter.

## Architecture Decisions

| # | Decision | Choice | Alternatives rejected | Rationale |
|---|---|---|---|---|
| 1 | Port style | `typing.Protocol`, structural | ABC inheritance; concrete classes | Adapters stay importable without domain coupling; fakes in tests need no base class |
| 2 | Adapter shape | `fetch()` → `RawPayload`, `parse()` pure, `search()` = composition | Single `search()` doing both | Parse tests replay a byte fixture: no `MockTransport`, no browser, no flakiness |
| 3 | Matching rules | One pure `domain/matching.py` | Per-adapter title heuristics | Four stores share one heuristic; one fixture-driven test table covers all of them |
| 4 | Filtering location | Use case applies `is_target_offer` | Adapter filters before returning | Adapter tests assert field mapping only; classification tests stay store-agnostic |
| 5 | Ripley transport | `RipleyAdapter(transport=...)` with `HttpxTransport` \| `PlaywrightTransport` | Two separate adapters; Playwright in the port | Spike outcome swaps one constructor argument; Playwright never reaches domain or CLI signature |
| 6 | Last-known state | Derived from newest `observation` row | Mutable `last_state` column | Single source of truth, append-only history, no update-anomaly between state and history |
| 7 | Money | `Decimal` in domain, `INTEGER` cents in SQLite | `float` / SQLite `REAL` | No binary-float drift on price-drop comparison |
| 8 | Model library | Frozen `dataclass` + `StrEnum` | pydantic | Only one untrusted boundary (`parse()`), which validates explicitly; keeps domain dependency-free |
| 9 | Failure isolation | Per-store `try/except` + per-store transaction in the use case | Global try, `asyncio.gather` | One store's exception cannot abort the run nor roll back another store's writes |
| 10 | HTTP client | `httpx` | `requests`, `aiohttp` | `httpx.MockTransport` gives offline transport tests; HTTP/2 + timeouts built in |

## Module Layout

```
pyproject.toml
src/tracker/
  domain/
    model.py              # Money, Offer, Language, ProductType, Availability, OfferKey
    events.py             # ChangeKind, ChangeEvent
    matching.py           # pure classification + target filter
    change_detection.py   # ChangeDetector
    errors.py             # StoreFetchError, StoreParseError
  application/
    ports.py              # StoreAdapter, OfferRepository, Reporter, Clock
    dto.py                # RawPayload, ObservationSnapshot, StoreResult, RunOutcome
    track_offers.py       # TrackOffersUseCase
  adapters/
    stores/
      vtex.py             # VtexStoreAdapter base (fetch + parse + pagination)
      plaza_vea.py        # config only
      oechsle.py          # config only
      ilahui.py           # Shopify JSON primary + HTML fallback
      ripley.py           # RipleyAdapter + HttpxTransport | PlaywrightTransport
    reporting/console.py  # ConsoleReporter (plain stdout)
  infrastructure/
    http/client.py        # httpx.Client factory, browser-like headers, retry/timeout policy
    persistence/
      schema.sql
      connection.py       # connect(), apply_schema(), seed_stores()
      sqlite_offer_repository.py
  cli/main.py             # composition root (argparse)
scripts/spike_ripley.py   # pre-implementation probe, not shipped code path
tests/
  conftest.py             # fixture_body(), frozen_clock, tmp db
  unit/ adapters/ integration/
  fixtures/{plaza_vea,oechsle,ilahui,ripley}/*.json|*.html
```

## Interfaces / Contracts

```python
# domain/model.py
class Language(StrEnum):     ENGLISH = "en"; SPANISH = "es"; UNKNOWN = "unknown"
class Availability(StrEnum): IN_STOCK = "in_stock"; OUT_OF_STOCK = "out_of_stock"; UNKNOWN = "unknown"
class ProductType(StrEnum):
    ETB = "etb"; BOOSTER_BOX = "booster_box"; BOOSTER_PACK = "booster_pack"
    BLISTER = "blister"; COLLECTION_BOX = "collection_box"; OTHER = "other"

@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str = "PEN"          # invariant: comparisons require equal currency

@dataclass(frozen=True, slots=True)
class Offer:
    store: str                      # slug, e.g. "plaza_vea"
    external_id: str                # store SKU/product id; fallback = URL path
    title: str
    url: str
    price: Money | None             # None = price not published
    availability: Availability
    language: Language
    product_type: ProductType
    observed_at: datetime           # tz-aware UTC
    @property
    def key(self) -> tuple[str, str]: return (self.store, self.external_id)
```

```python
# application/dto.py
@dataclass(frozen=True, slots=True)
class RawPayload:
    store: str; source_url: str; content_type: str; body: str; fetched_at: datetime

@dataclass(frozen=True, slots=True)
class ObservationSnapshot:
    external_id: str; price: Money | None; availability: Availability; observed_at: datetime
```

```python
# application/ports.py
class StoreAdapter(Protocol):
    store_slug: str
    def fetch(self, query: str) -> Sequence[RawPayload]: ...            # raises StoreFetchError
    def parse(self, payloads: Sequence[RawPayload], observed_at: datetime) -> list[Offer]: ...
    def search(self, query: str, observed_at: datetime) -> list[Offer]: ...  # = parse(fetch())

class OfferRepository(Protocol):
    def start_run(self, started_at: datetime) -> int: ...
    def finish_run(self, run_id: int, status: str) -> None: ...
    def record_store_run(self, run_id: int, store: str, status: str,
                         offer_count: int, error: str | None) -> None: ...
    def has_history(self, store: str) -> bool: ...
    def last_known(self, store: str) -> dict[str, ObservationSnapshot]: ...   # keyed by external_id
    def record_observations(self, run_id: int, offers: Sequence[Offer]) -> None: ...
    def price_history(self, store: str, external_id: str, limit: int = 50) -> list[ObservationSnapshot]: ...

class Reporter(Protocol):
    def report(self, outcome: RunOutcome) -> None: ...

class Clock(Protocol):
    def now(self) -> datetime: ...
```

`fetch()` returns a *sequence* of payloads so VTEX pagination and Ilahui's JSON/HTML fallback stay
inside the adapter without leaking page state to the use case.

## Matching Module (shared, pure)

`normalize(text)` → NFKD accent-strip + casefold + whitespace collapse, so `"Inglés" == "ingles"`.

| Function | Signature | Rule |
|---|---|---|
| `detect_language` | `(title: str, attributes: Mapping[str, Sequence[str]] \| None) -> Language` | Structured attribute (`Idioma`, `Language`, `Lenguaje`) wins; else title markers `ingles/english/(eng)` vs `espanol/spanish/(esp)/latino`; else `UNKNOWN` |
| `detect_product_type` | `(title: str) -> ProductType` | Ordered longest-match table: `elite trainer box`/`etb` → ETB; `booster box`/`caja de sobres` → BOOSTER_BOX; `sobre`/`booster`/`pack` → BOOSTER_PACK; `blister` → BLISTER; `box`/`collection` → COLLECTION_BOX; else OTHER |
| `is_anniversary` | `(title: str) -> bool` | `30 aniversario` / `30th anniversary` / `30 aniv` / `aniversario 30` |
| `is_non_tcg_noise` | `(title: str) -> bool` | `peluche`, `plush`, `figura`, `figure`, `llavero`, `taza`, `polo`, `mochila`, `stickers` |
| `is_target_offer` | `(offer: Offer) -> bool` | `language is ENGLISH and is_anniversary(title) and product_type is not OTHER and not is_non_tcg_noise(title)` |

Adapters call `detect_language`/`detect_product_type` while building `Offer`s (classification is part
of normalization); the use case alone applies `is_target_offer`.

## Adapters

| Store | Endpoint | Field mapping |
|---|---|---|
| Plaza Vea, Oechsle (`vtex.py`) | `GET {base}/api/catalog_system/pub/products/search?ft={quote(q)}&_from={n}&_to={n+49}`, `Accept: application/json` | JSON array. `productId`→`external_id`; `productName`→`title`; `{base}/{linkText}/p`→`url`; `items[0].sellers[0].commertialOffer.Price`→`Money`; `AvailableQuantity > 0 or IsAvailable`→availability; product-level specification dict (keys from `LANGUAGE_SPEC_KEYS`) → `attributes` for `detect_language` |
| Ilahui (`ilahui.py`) | Primary `GET /search/suggest.json?q={q}&resources[type]=product&resources[limit]=10`; fallback `GET /search?q={q}` (HTML) | JSON: `resources.results.products[]` → `id`, `title`, `url` (prefix host), `price`, `available`. HTML: product-card selectors via `selectolax`, price text → `Decimal` after stripping `S/` and thousands separators. `RawPayload.content_type` selects the parse branch |
| Ripley (`ripley.py`) | Transport-injected: `HttpxTransport` (browser headers) or `PlaywrightTransport` (`page.goto` + `page.content()`); both return `RawPayload` with identical `source_url` | Parse branch chosen by `content_type`: VTEX JSON reuses `vtex.parse_products`; HTML uses a Ripley card parser. Adapter class and port are identical either way |

Pagination (VTEX): read `content-range`/`resources-count-range` (`products 0-49/137`), loop pages of
50 up to `MAX_PAGES = 5`. Missing header → single page, no crash. Non-2xx, timeout, or malformed JSON
→ `StoreFetchError`/`StoreParseError` (never a bare httpx exception crossing the port).

## Ripley Spike (Task 0 — run before any adapter work)

`scripts/spike_ripley.py`, httpx only, no Playwright dependency yet. Probe ladder against
`https://simple.ripley.com.pe`, each with a realistic header set (Chrome UA, `Accept: text/html,...`,
`Accept-Language: es-PE,es;q=0.9,en;q=0.8`, `Accept-Encoding: gzip, deflate, br`,
`Sec-Fetch-{Dest,Mode,Site,User}`, `Sec-CH-UA*`, `Upgrade-Insecure-Requests: 1`, `Referer`):

1. `GET /robots.txt` · 2. `GET /` · 3. `GET /search/pokemon` · 4. repeat 2–3 with the cookie jar
warmed by step 2 · 5. `GET /api/catalog_system/pub/products/search?ft=pokemon` (Ripley may be VTEX).

Record per probe: status, `server`, `set-cookie` names, `cf-ray`/`x-akamai-*`/`x-amzn-waf-*`, and the
first 2 KB of body. Decision rule:

| Observation | Outcome |
|---|---|
| Any probe returns 200 | `HttpxTransport`; if probe 5 answers, reuse the VTEX parser — no Playwright dependency at all |
| 403 with challenge markers (`_abck`, `cf_chl`, `cf-ray`, JS-challenge body) | `PlaywrightTransport`, browser extra installed |
| 403 on every probe including a warmed session, no challenge markers | Likely geo/IP; escalate as an open question, do not build Playwright blindly |

Either way, save the best response body into `tests/fixtures/ripley/` so parse tests exist regardless
of transport. The port contract is unchanged in all three branches.

## SQLite Schema (`infrastructure/persistence/schema.sql`)

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS store (
  slug TEXT PRIMARY KEY, name TEXT NOT NULL, base_url TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at  TEXT NOT NULL,                        -- ISO-8601 UTC
  finished_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('running','completed','failed'))
);

CREATE TABLE IF NOT EXISTS store_run (
  run_id INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
  store_slug TEXT NOT NULL REFERENCES store(slug),
  status TEXT NOT NULL CHECK (status IN ('ok','failed')),
  offer_count INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  PRIMARY KEY (run_id, store_slug)
);

CREATE TABLE IF NOT EXISTS product (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  store_slug TEXT NOT NULL REFERENCES store(slug),
  external_id TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  language TEXT NOT NULL,
  product_type TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  UNIQUE (store_slug, external_id)
);

CREATE TABLE IF NOT EXISTS observation (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL REFERENCES product(id) ON DELETE CASCADE,
  run_id     INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
  price_cents INTEGER,                              -- NULL = price not published
  currency TEXT NOT NULL DEFAULT 'PEN',
  availability TEXT NOT NULL
    CHECK (availability IN ('in_stock','out_of_stock','unknown')),
  observed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_obs_product_time
  ON observation (product_id, observed_at DESC, id DESC);   -- serves "last observation per product"
CREATE INDEX IF NOT EXISTS idx_obs_run     ON observation (run_id);
CREATE INDEX IF NOT EXISTS idx_product_store ON product (store_slug);
```

`last_known(store)` query (index above makes the correlated subquery an index seek per product):

```sql
SELECT p.external_id, o.price_cents, o.currency, o.availability, o.observed_at
FROM product p
JOIN observation o ON o.id = (
  SELECT o2.id FROM observation o2 WHERE o2.product_id = p.id
  ORDER BY o2.observed_at DESC, o2.id DESC LIMIT 1)
WHERE p.store_slug = ?;
```

`has_history(store)` = `SELECT EXISTS(SELECT 1 FROM store_run WHERE store_slug = ? AND status='ok')`
— a store that has only ever failed has no baseline and must not fabricate NEW events later.

## Change Detection

```python
class ChangeKind(StrEnum):
    BASELINE = "baseline"; NEW = "new"; RESTOCKED = "restocked"; PRICE_DROP = "price_drop"

def detect(previous: Mapping[str, ObservationSnapshot],
           current: Sequence[Offer],
           store_has_history: bool) -> list[ChangeEvent]
```

| Condition | Emits |
|---|---|
| `not store_has_history` | `BASELINE` for every current offer; no NEW/RESTOCKED/PRICE_DROP that run |
| `key not in previous` (store has history) | `NEW` |
| `previous.availability != IN_STOCK` and `current == IN_STOCK` | `RESTOCKED` |
| both prices present, same currency, `current.price < previous.price` | `PRICE_DROP` (no threshold; carries old/new/delta) |
| price rise, equal price, or either price `None` | nothing |
| product in `previous` but absent from `current` | nothing — absence is not out-of-stock, and last-known is not overwritten |
| adapter raised | nothing for that store; `StoreResult(status="failed")` only |

`RESTOCKED` and `PRICE_DROP` may both fire for one product in one run. Detection is a pure function
of its three arguments — no repository, no clock, no I/O.

## Data Flow

    CLI (composition root)
      │ builds httpx.Client, adapters, sqlite conn, repo, reporter, clock
      ▼
    TrackOffersUseCase.execute()
      │ run_id = repo.start_run(now)
      │ for adapter in adapters:            ← isolation boundary
      │    try: payloads = adapter.fetch(q) ──→ network / browser
      │         offers   = adapter.parse(payloads, now)   (pure)
      │         matched  = filter(is_target_offer, offers) ; dedupe by key
      │         prev     = repo.last_known(slug)   ← MUST precede record_observations
      │         hist     = repo.has_history(slug)
      │         events   = ChangeDetector.detect(prev, matched, hist)
      │         repo.record_observations(run_id, matched)   [own transaction]
      │         repo.record_store_run(run_id, slug, "ok", ...)
      │    except Exception as exc:
      │         repo.record_store_run(run_id, slug, "failed", 0, str(exc))
      │         results += StoreResult(failed)   → loop continues
      ▼
    repo.finish_run(run_id, "completed") → Reporter.report(RunOutcome) → stdout

Ordering invariant: `last_known` and `has_history` are read **before** this run's observations are
written for that store, otherwise every product diffs against itself. Each store commits in its own
transaction, so a later store's failure cannot roll back an earlier store's data.

CLI flags: `--db PATH` (default `./tracker.db`), `--store SLUG` (repeatable), `--query TEXT`
(repeatable, default `["pokemon 30 aniversario", "pokemon 30th anniversary"]`), `--json`,
`--history STORE:EXTERNAL_ID`. Exit codes: `0` all stores ok, `1` partial failure, `2` all failed.
Console output separates `ok, 0 matches` from `FAILED: <reason>` so silent API drift is visible.

## File Changes

| File | Action | Description |
|---|---|---|
| `pyproject.toml` | Create | Package metadata, deps, `[project.optional-dependencies] ripley`, pytest markers, ruff/mypy config |
| `src/tracker/domain/*.py` | Create | Model, events, matching, change detection, errors |
| `src/tracker/application/*.py` | Create | Ports, DTOs, use case |
| `src/tracker/adapters/stores/*.py` | Create | VTEX base + 2 configs, Ilahui, Ripley + transports |
| `src/tracker/adapters/reporting/console.py` | Create | Reporter implementation |
| `src/tracker/infrastructure/**` | Create | httpx client factory, schema, connection, SQLite repository |
| `src/tracker/cli/main.py` | Create | argparse composition root |
| `scripts/spike_ripley.py` | Create | Pre-implementation probe, deleted or kept as a dev tool |
| `tests/**` | Create | Unit, adapter, integration suites + fixtures |

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit | `matching` | `tests/unit/test_matching.py`, parametrized table using real titles from exploration (`"Pokemon TCG 30 Aniversario ETB En Inglés"`) plus edge cases: no accent, `(ENG)`, no marker, plush/figure noise, Spanish variant |
| Unit | `change_detection` | `tests/unit/test_change_detection.py`, pure in-memory maps: baseline, new, restocked, price drop, price rise (no event), unchanged, disappeared product, failed-store isolation |
| Unit | `Money` | Decimal arithmetic, currency-mismatch comparison raises |
| Adapter | `parse()` | `tests/adapters/test_*_parse.py` reading `tests/fixtures/{store}/*.json|html` directly into `RawPayload`. No HTTP layer involved at all |
| Adapter | `fetch()` | `httpx.MockTransport` asserting URL/params, header set, pagination loop over `content-range`, and error mapping (403/500/timeout → `StoreFetchError`) |
| Adapter | Ripley transports | `HttpxTransport` via `MockTransport`; `PlaywrightTransport` via `page.route(...)` fulfilling with the saved fixture body, marked `@pytest.mark.playwright` and skipped when browsers are absent |
| Integration | SQLite repository | Real `sqlite3` on `tmp_path`: `last_known` picks newest across multiple runs, `has_history` semantics, price-history ordering, FK cascade, per-store transaction isolation |
| Integration | Use case | Fake in-memory adapters (one raising) verifying failure isolation, read-before-write ordering, two-run diff producing exactly the expected events |
| E2E | Live network | `scripts/spike_ripley.py` and any `@pytest.mark.live` test, deselected by default via `addopts = -m "not live and not playwright"` |

`tests/conftest.py` provides `fixture_body(store, name)`, a `FrozenClock`, and a `tmp_db` fixture.
No test touches the network in the default selection.

## Dependencies

| Package | Scope | Why |
|---|---|---|
| Python ≥ 3.11 | runtime | `StrEnum`, `datetime.UTC`, `tomllib` |
| `httpx` | runtime | HTTP/2, per-request timeouts, and `MockTransport` for offline transport tests — the decisive edge over `requests` |
| `selectolax` | runtime | Fast, lenient HTML parsing for Ilahui fallback and Ripley HTML (lighter than `beautifulsoup4` + `lxml`) |
| `playwright` | extra `[ripley]` | Only if the spike confirms a real bot challenge; isolated behind `PlaywrightTransport` |
| `pytest` | dev | Test runner |
| `pytest-cov` | dev | Coverage on `src/tracker` |
| `pytest-playwright` | extra `[ripley]` dev | `page.route` interception for the Ripley browser path |
| `ruff`, `mypy` | dev | Lint/format + `--strict` typing on `src/` (ports are `Protocol`s; strict typing is what makes them load-bearing) |
| stdlib `sqlite3`, `decimal`, `datetime`, `argparse`, `unicodedata` | runtime | Persistence, money, time, CLI, accent normalization |

**Rejected:** `pydantic` (only one untrusted boundary, `parse()`, which validates explicitly; frozen
dataclasses keep the domain dependency-free), `rich` (plain stdout makes the `Reporter` port testable
with `capsys`), `vcrpy` (saved fixtures + `MockTransport` cover the same ground without cassette
coupling), `SQLAlchemy` (four tables, one adapter, stdlib `sqlite3` is sufficient).

## Threat Matrix

N/A — this change introduces no routing, shell-command construction, VCS/PR automation,
executable-file classification, or process-integration boundary. The only subprocess is a
Playwright-managed browser launched with library-fixed arguments and no user-controlled input;
no matrix row (documentation-like paths, git repository selection, commit state, push state, PR
commands) applies.

## Migration / Rollout

No migration required — greenfield. `apply_schema()` runs idempotent `CREATE TABLE IF NOT EXISTS` on
every start; rollback is deleting the SQLite file. Playwright installs only via the optional
`[ripley]` extra, so the core install stays browser-free until the spike says otherwise.

## Open Questions

- [ ] Ripley 403 root cause — resolved by the Task 0 spike; if it is geo/IP, Ripley coverage needs a
      product decision (proxy, drop, or best-effort) before adapter work.
- [ ] Whether VTEX exposes a structured language specification key, and under which name — confirm
      against a real payload during Task 0; `LANGUAGE_SPEC_KEYS` is a list precisely because of this.
- [ ] Ilahui `/search/suggest.json` availability is a Shopify convention, not verified — the HTML
      fallback path is mandatory, not optional.
- [ ] `ilahuiperu.com` remains user-inferred, not confirmed (carried from exploration).
