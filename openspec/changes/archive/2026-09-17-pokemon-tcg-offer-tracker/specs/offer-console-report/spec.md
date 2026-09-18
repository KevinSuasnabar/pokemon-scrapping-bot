# Offer Console Report Specification

## Purpose

Render each run's outcome to the console: detected events (new/restocked/price-drop), any failed
stores, and the full current matching listing on demand. No dashboard, bot, email, or notification
channel is provided.

## Requirements

### Requirement: Console-Only Output Channel

The system MUST report exclusively via console output. The system MUST NOT provide a web dashboard,
chat bot, email, or push-notification delivery mechanism.

#### Scenario: Run output is console text only

- GIVEN a run completes with two detected events
- WHEN the report step executes
- THEN the events are printed to the console
- AND no external notification channel is invoked

### Requirement: Event Report

The system MUST print every NEW, RESTOCKED, and PRICE_DROP event detected in the run, identifying
the store, product title, and relevant value (price for PRICE_DROP; availability transition for
RESTOCKED).

#### Scenario: Mixed events are all shown

- GIVEN a run detects one NEW offer, one RESTOCKED product, and one PRICE_DROP
- WHEN the report is printed
- THEN all three events appear in the console output

#### Scenario: No events is explicitly stated

- GIVEN a run detects zero events
- WHEN the report is printed
- THEN the console output explicitly states no changes were detected, rather than printing nothing

### Requirement: First-Run Baseline Report

When a store's offers in this run are that store's initial baseline (no prior successful run for
that store specifically), the report MUST label them as an initial baseline for that store, not as
NEW events — even if other stores in the same run have established history and report normal NEW
events alongside them.

#### Scenario: Baseline run is labeled distinctly

- GIVEN the first-ever run finds four matching offers across all stores
- WHEN the report is printed
- THEN the output labels these as an initial baseline listing, not as new-offer alerts

#### Scenario: One store's baseline appears alongside another store's NEW events in the same run

- GIVEN Ripley's first successful run finds one matching offer
- AND Plaza Vea, with established history, finds one genuinely new offer in the same run
- WHEN the report is printed
- THEN Ripley's offer is labeled as part of Ripley's initial baseline
- AND Plaza Vea's offer is labeled as a NEW event
- AND the two labels are not conflated

### Requirement: Failed Store Visibility

The report MUST explicitly list any store that failed during the run, distinct from stores that
succeeded with zero matching offers.

#### Scenario: A failed store is called out

- GIVEN Ripley failed during discovery this run
- WHEN the report is printed
- THEN the output explicitly names Ripley as failed for this run
- AND this is visually/textually distinct from a store that succeeded with zero matches

### Requirement: Current Matching Listing View

The system MUST provide a way to display the full set of currently matching offers (latest
observation per store/product), independent of this run's event list.

#### Scenario: Full listing is viewable on demand

- GIVEN the database holds last-known state for six matching products across three stores
- WHEN the current-listing view is requested
- THEN all six current offers are printed with store, title, price, and availability
