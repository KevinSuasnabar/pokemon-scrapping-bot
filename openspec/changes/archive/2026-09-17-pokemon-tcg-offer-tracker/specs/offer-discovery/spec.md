# Offer Discovery Specification

## Purpose

Query each of the four supported storefronts for Pokémon TCG 30th Anniversary products, filter
results to the English-language variant, and normalize matches into a common `Offer` model for
downstream diffing and persistence.

## Requirements

### Requirement: Per-Store Search Coverage

The system MUST search Plaza Vea (plazavea.com.pe), Oechsle (oechsle.pe), Ripley
(ripley.com.pe/simple.ripley.com.pe), and Ilahui (ilahuiperu.com) for Pokémon TCG 30th Anniversary
products on every run.

#### Scenario: All four stores are queried in a single run

- GIVEN a scheduled or manual run is started
- WHEN the discovery step executes
- THEN each of the four stores is queried independently
- AND results from each store are tagged with that store's identity before matching

### Requirement: English-Variant Language Filtering

The system MUST include only listings identified as the English-language variant and MUST exclude
listings identified as Spanish, based on title text matching (e.g. "inglés"/"english") and, where a
store exposes a structured language attribute, that attribute.

#### Scenario: Title marks the English variant

- GIVEN a store returns a product titled "Pokemon TCG 30 Aniversario ETB En Inglés"
- WHEN language filtering runs
- THEN the product is included as an English-variant match

#### Scenario: Title marks the Spanish variant

- GIVEN a store returns a product titled "Pokemon TCG 30 Aniversario ETB Español"
- WHEN language filtering runs
- THEN the product is excluded from results

#### Scenario: No language marker present

- GIVEN a store returns a 30th Anniversary product with no language marker in its title or
  attributes
- WHEN language filtering runs
- THEN the product MUST NOT be assumed English and MUST be excluded, unless a store-specific
  structured attribute confirms English

### Requirement: TCG Product-Type Filtering

The system MUST filter matches to TCG product types (e.g. Elite Trainer Box, Booster, Box, Sobres)
and MUST exclude non-TCG 30th Anniversary merchandise (e.g. plush, figures) that would otherwise
match the same search terms.

#### Scenario: Non-TCG merch is excluded

- GIVEN a store's 30th Anniversary search results include a plush toy and a Booster Box, both in
  English
- WHEN product-type filtering runs
- THEN only the Booster Box is retained as a match

### Requirement: Normalized Offer Output

The system MUST normalize every matched listing into a common `Offer` representation carrying
store identity, product identity (external SKU/URL), title, language, product type, price,
currency, and availability, regardless of source store's native data shape.

#### Scenario: Offers from different stores share one shape

- GIVEN Plaza Vea and Ilahui each return one matching English TCG listing
- WHEN discovery completes
- THEN both listings are represented as `Offer` records with the same field set

### Requirement: Per-Store Failure Isolation

The system MUST isolate a failure (HTTP error, parse error, timeout) in one store's adapter so that
it does not abort the run or prevent other stores from being searched, diffed, and reported. A
failed store MUST be reported explicitly as failed, and MUST NOT be silently treated as "no
changes" or "zero matches."

#### Scenario: One store fails, others complete

- GIVEN Ripley's adapter raises an HTTP error during a run
- WHEN discovery executes
- THEN Plaza Vea, Oechsle, and Ilahui still complete their search and diff
- AND the run's output explicitly marks Ripley as failed for that run

#### Scenario: A failed store's prior state is not corrupted

- GIVEN Ripley fails on this run
- WHEN the run completes
- THEN no observation is recorded for Ripley on this run
- AND Ripley's last-known state from a prior successful run remains the diff baseline for its next
  successful run

### Requirement: Ripley Adapter Contract

Ripley MUST remain in v1 scope. The Ripley adapter MUST implement the same
`search(query) -> list[Offer]` contract as every other store adapter; the fetch mechanism required
to reach Ripley's data (plain HTTP or browser automation) is an adapter-internal detail and MUST
NOT alter the discovery contract or output shape.

#### Scenario: Ripley results are indistinguishable in shape from other stores

- GIVEN Ripley's adapter successfully retrieves matching listings
- WHEN discovery completes
- THEN Ripley's `Offer` records have the same shape as records from any other store
