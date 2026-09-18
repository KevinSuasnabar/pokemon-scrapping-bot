# Offer Change Detection Specification

## Purpose

Compare each run's discovered offers against the last-known persisted state per (store, product)
and produce exactly three reportable event types: new offer, restock, and price drop.

## Requirements

### Requirement: Last-Known-State Diff Baseline

The system MUST diff this run's observations against the newest prior observation per (store,
product) — the last-known state — and MUST NOT require that baseline to come from the strictly
immediately-previous run.

#### Scenario: A store outage does not fabricate false "new" events on recovery

- GIVEN a product was observed in-stock at run N
- AND the same store fails entirely at run N+1 (no observation recorded)
- WHEN the store succeeds again at run N+2 with the same product still in stock, unchanged price
- THEN the diff baseline used is the run N observation
- AND no NEW or RESTOCKED event is reported for that product at run N+2

### Requirement: New Offer Event

The system MUST report a NEW event for any (store, product) matched in this run where that STORE
has at least one prior successful run, and the product has no prior observation. A store's first
ever successful run MUST NOT emit NEW events (see First-Run Baseline Labeling) — this check is
scoped per store, not to whether the database holds any data at all for other stores.

#### Scenario: Previously unseen product appears at an already-established store

- GIVEN Plaza Vea has at least one prior successful run
- AND this product has never been observed at Plaza Vea before
- WHEN this run observes it as a match
- THEN a NEW event is reported for that (store, product)

### Requirement: First-Run Baseline Labeling

When a store has no prior successful run (no history for that store specifically — regardless of
whether other stores already have history), the system MUST report every matched offer from that
store as part of an initial baseline for that store and MUST NOT label any of them as NEW events.
This is evaluated per store, so a store added later, or a store recovering after every previous run
failed, gets its own baseline run instead of a flood of false NEW events.

#### Scenario: Very first run populates the database for all stores

- GIVEN the database has no prior observations for any store
- WHEN the first run finds five matching offers across the four stores
- THEN all five are reported as an initial baseline
- AND none are reported as NEW events

#### Scenario: A store's first successful run is its own baseline, even if other stores already have history

- GIVEN Plaza Vea, Oechsle, and Ilahui already have prior successful runs and observation history
- AND Ripley has never had a successful run before (every prior attempt failed)
- WHEN this run is Ripley's first successful fetch, finding two matching offers
- THEN Ripley's two offers are reported as Ripley's initial baseline
- AND none of Ripley's offers are reported as NEW events
- AND this has no effect on whether Plaza Vea, Oechsle, or Ilahui's offers are reported as NEW

### Requirement: Restock Event

The system MUST report a RESTOCKED event when a (store, product)'s last-known availability was
out-of-stock and this run's observation is in-stock.

#### Scenario: Product transitions from out-of-stock to in-stock

- GIVEN a product's last-known observation has availability = out-of-stock
- WHEN this run observes availability = in-stock for the same (store, product)
- THEN a RESTOCKED event is reported

### Requirement: Price Drop Event

The system MUST report a PRICE_DROP event for any decrease in price on a previously seen (store,
product), with no minimum threshold — any decrease qualifies.

#### Scenario: Any price decrease is reported

- GIVEN a product's last-known price is S/289.90
- WHEN this run observes the same (store, product) at S/288.00
- THEN a PRICE_DROP event is reported

#### Scenario: A one-cent decrease still qualifies

- GIVEN a product's last-known price is S/129.90
- WHEN this run observes S/129.89 for the same (store, product)
- THEN a PRICE_DROP event is reported

### Requirement: Non-Reportable Transitions Recorded but Not Alerted

Price increases and in-stock-to-out-of-stock transitions MUST be recorded as observations in
history but MUST NOT be reported as console events in v1.

#### Scenario: Price increase is silent

- GIVEN a product's last-known price is S/129.90
- WHEN this run observes S/139.90 for the same (store, product)
- THEN the new observation is persisted
- AND no console event is reported for this transition

#### Scenario: Going out of stock is silent

- GIVEN a product's last-known availability is in-stock
- WHEN this run observes availability = out-of-stock for the same (store, product)
- THEN the new observation is persisted
- AND no console event is reported for this transition

### Requirement: Diff Isolation Across Stores

A failed store's absent observation MUST NOT affect the diff outcome computed for any other store
in the same run.

#### Scenario: One store's failure does not skew another store's diff

- GIVEN Ripley fails during a run while Plaza Vea succeeds with a price drop
- WHEN the diff step executes
- THEN Plaza Vea's PRICE_DROP event is still reported
- AND no event is fabricated or suppressed for Ripley
