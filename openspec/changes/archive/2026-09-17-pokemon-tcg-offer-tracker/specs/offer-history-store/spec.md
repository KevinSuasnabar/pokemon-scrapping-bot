# Offer History Store Specification

## Purpose

Persist per-run observations and last-known state per (store, product) in SQLite (stdlib
`sqlite3`), providing the diff baseline for change detection and a queryable price/availability
history.

## Requirements

### Requirement: Store, Product, Observation, Run Schema

The system MUST persist data using at least four entities: `store` (the four retailers), `product`
(identity scoped to a store, keyed by external SKU/URL, carrying title, language, and product
type), `observation` (append-only per run per product: price, currency, availability, timestamp),
and `run` (one row per execution).

#### Scenario: A run creates a run row and one observation per matched offer

- GIVEN a run discovers three matching offers across two stores
- WHEN the run completes
- THEN exactly one `run` row is persisted for the execution
- AND exactly one `observation` row is persisted per matched offer, linked to that run

### Requirement: Append-Only Observation History

The system MUST NOT update or delete existing `observation` rows. Each run's findings MUST be
inserted as new rows, preserving full price and availability history per product.

#### Scenario: Repeated runs accumulate history

- GIVEN a product has one prior observation
- WHEN two more runs each observe that product
- THEN the product has three observation rows total, ordered by run/timestamp

### Requirement: Last-Known State Derivation

The system MUST derive last-known state for a (store, product) as its newest observation by run
order/timestamp, and MUST expose this derivation to the change-detection capability as the diff
baseline.

#### Scenario: Newest observation wins as baseline

- GIVEN a product has observations from runs 1, 2, and 3
- WHEN the next run needs a diff baseline for that product
- THEN the observation from run 3 is used, not run 1 or run 2

### Requirement: Price History Query

The system MUST allow querying a product's full observation history (price, currency, availability,
timestamp) from the SQLite store.

#### Scenario: Query returns full history for a product

- GIVEN a product has five accumulated observations
- WHEN its history is queried
- THEN all five observations are returned in chronological order

### Requirement: SKU/URL Change Yields a New Product Identity (Known Limitation)

The system MUST treat a change in a store's external SKU or product URL as a new `product` identity
distinct from any prior product row, even if the underlying real-world item is unchanged. This is a
stated v1 limitation: history under the old identity is not linked to the new one, and this history
discontinuity is an accepted tradeoff, not a defect to silently work around.

#### Scenario: A store changes a product's URL between runs

- GIVEN a product was previously tracked at URL A with three observations
- WHEN a later run finds the same real-world item at URL B
- THEN a new `product` row is created for URL B
- AND the new product's history starts empty, independent of URL A's history
- AND the first observation under URL B is treated per the first-run/NEW-event rules for that
  product identity, not as a continuation of URL A
