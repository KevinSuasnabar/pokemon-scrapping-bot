# Exploration: pokemon-tcg-offer-tracker

Date: 2026-09-17
Status: partial — ready for proposal pending 3 open product questions

## Goal

Python CLI that checks for Pokémon TCG 30th Anniversary product listings, English-language
version specifically, across four Peruvian retail sites: Plaza Vea, Oechsle, Ripley, Ilahui.
Console-only output (no dashboard, bot, or notifications).

## Site-by-site technical verification (fresh WebFetch checks, independent of the prior
"pokemon-scrapping" project)

### Plaza Vea (plazavea.com.pe) — VTEX
- `robots.txt`: only disallows `/checkout` and brand sitemap XMLs.
- Public unauthenticated catalog API `GET /api/catalog_system/pub/products/search?ft={term}`
  returns real JSON over plain HTTP. Confirmed working today, no headless browser needed.
- Pagination (`_from`/`_to`, `Resources-Count-Range` header) not verified at raw-header level —
  WebFetch summarized rather than returning raw headers. Verify with a real HTTP client during
  implementation.

### Oechsle (oechsle.pe) — VTEX (same corporate group as Plaza Vea, Intercorp)
- Same `/api/catalog_system/pub/products/search?ft=...` pattern, confirmed working over plain HTTP.
- Test query only returned Spanish-titled results at fetch time — stock/listing is time-variable,
  doesn't confirm the English SKU is never stocked there.

### Ripley (simple.ripley.com.pe / ripley.com.pe)
- HTTP 403 on three independent probes today (robots.txt, search page, homepage) and on the
  alternate host's robots.txt too.
- Root cause unverified (WAF/Akamai/Cloudflare bot detection vs. UA allowlist vs. geo/IP block) —
  WebFetch only surfaces the status code, not headers or challenge markup.
- Converges with the prior unrelated project's finding on the same domain, but was re-verified
  independently here per the user's explicit "don't assume" instruction.
- Verdict: plain HTTP very likely blocked; headless browser (Playwright) is the probable path,
  but not certain — one more direct test with a real HTTP client + realistic headers recommended
  before committing to a full browser-automation adapter.

### Ilahui — domain inferred as ilahuiperu.com (Shopify), **not confirmed by the user**
- `/search?q=pokemon` HTML returns real product data over plain HTTP, no JS rendering needed.
- Found SKUs: "Pokemon TCG 30 Aniversario ETB En Inglés" (S/289.90, out of stock), "Pokemon TCG 30
  Aniversario Box en Inglés" (S/129.90, out of stock).
- A `/search.json` or `/collections/{handle}/products.json` endpoint likely exists (Shopify
  convention) but wasn't directly verified.

**Aggregate signal:** 3 of 4 stores (Plaza Vea, Oechsle, Ilahui) are plain-HTTP-friendly today;
only Ripley shows anti-bot resistance.

## Language/variant disambiguation

All 3 verifiable sites suffix product titles with literal "Inglés"/"Español" — keyword matching on
title is a viable first-pass filter. Risks: inconsistent free-text phrasing ("Ingles" no accent,
"(ENG)", no marker at all), false positives from non-TCG 30th-anniversary merch (plush, figures)
matching the same search terms, need for an explicit TCG-product-type filter (ETB/Booster/Box/
Sobres) alongside the language filter. Ripley's title conventions are completely unknown (no data
access yet). VTEX may carry a structured "Idioma"/language attribute in the full JSON payload,
more robust than title matching — not confirmed via WebFetch's summarized output.

## Architecture direction (not final)

**Recommended:** hexagonal port/adapter per store — a common `StoreAdapter` port
(`search(query) -> list[Offer]`), a normalized `Offer`/`Product` model, an orchestrator that runs
all adapters with per-adapter failure isolation (one store down doesn't kill the run). Separate
`fetch()` from `parse()` per adapter so parsing/matching logic is unit-testable without network
mocking. Mixed fetch strategy: plain HTTP (httpx/requests) for Plaza Vea/Oechsle/Ilahui, Playwright
reserved for Ripley pending one more verification.

**Alternatives considered:**
- Flat scripts, no abstraction — fastest, but contradicts the explicit "well-structured project"
  requirement that is the whole reason for running SDD here.
- Dynamic plugin/adapter registry — over-engineered for exactly 4 known, fixed stores.

## Testing direction

No test runner exists yet (empty project), Strict TDD currently disabled. Recommended: pytest +
recorded fixtures (httpx.MockTransport or vcrpy/pytest-recording for HTTP adapters; Playwright's
`page.route` for Ripley if needed) to avoid slow/flaky tests against live network and anti-bot
layers.

## Open product questions (must be resolved before/during proposal)

1. **State/persistence**: print current matching listings every run (stateless), or diff against a
   previous run's snapshot to highlight NEW listings or price drops (implies local persistence —
   JSON snapshot or SQLite)? "Tracker" in the change name leans toward diffing, but "console only"
   describes the output channel, not whether internal state exists.
2. **Ripley scope contingency**: if Playwright is confirmed necessary, stays in v1 scope or becomes
   best-effort/optional given the added dependency weight (browser binaries)?
3. **Ilahui domain confirmation**: is `ilahuiperu.com` the intended store?

## Risks

- Ripley access is the single biggest technical risk to "all 4 stores" scope — root cause of the
  403 unverified, could resist even Playwright if geo/IP-based.
- Title-based language matching is a heuristic verified on a small sample, not exhaustive; unknown
  for Ripley entirely.
- Ilahui domain inferred, not user-confirmed.
- Listing volatility observed even during this exploration (stock changed between fetches) —
  supports that the diffing/persistence question matters.
- VTEX API pagination and full JSON schema only summarized by WebFetch, need raw-client
  verification before design/implementation.

## Next recommended

`sdd-propose`, after resolving the 3 open product questions above with the user.
