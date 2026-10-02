# 19. Financial Feature Store First Implementation

## Objective
Deliver the first production-oriented financial feature store that is Qlib-compatible and built from existing Yahoo Gold raw data under ~/.qlib/stock_data.

This phase explicitly does not train models and does not generate synthetic factors.

## Delivered Components
Implemented under canada/financial_feature_store:

1. build_financial_features.py
2. validate_financial_features.py
3. test_static_dataloader.py

## Input Data
Raw source folders:

- ~/.qlib/stock_data/ca_raw
- ~/.qlib/stock_data/us_raw

Primary tables used in this phase:

- income_statement
- balance_sheet
- cash_flow
- earnings_dates

## Canonical Mapping Layer
A canonical feature namespace is used to avoid hardcoding one-off Yahoo column names.

First canonical set:

- fin_revenue
- fin_net_income
- fin_total_assets
- fin_total_liabilities
- fin_total_equity
- fin_operating_cash_flow
- fin_free_cash_flow

Each canonical field maps to multiple normalized aliases (for example spaced/camelcase variants), and extraction uses first non-null value across matched aliases.

## PIT Semantics
Event-level records carry:

- period_end
- report_date (from earnings_dates when available)
- available_from

Assignment strategy:

1. For each instrument+period_end, match earliest earnings report_date >= period_end within max-forward-days.
2. If not found, fallback available_from = period_end + fallback-lag-days.

This guarantees no feature value is valid before available_from.

## Materialization Strategy
Event-level values are transformed into a daily panel:

- Calendar: Qlib NA day calendar when available, else business-day fallback.
- Expansion: forward-fill from available_from until next availability boundary.
- Output shape: MultiIndex(datetime, instrument) with canonical feature columns.

## Output Artifacts
Default outputs:

- ~/.qlib/stock_data/processed/financial_feature_events.parquet
- ~/.qlib/stock_data/processed/financial_daily_features.parquet

## Quality Gates
validate_financial_features.py checks:

- duplicate MultiIndex rows
- index naming conformance (datetime, instrument)
- non-monotonic available_from by instrument+field
- PIT leakage (daily datetime earlier than first available_from)

## Qlib Compatibility Test
test_static_dataloader.py verifies that qlib.data.dataset.loader.StaticDataLoader can load the generated parquet and perform instrument filtering.

## Typical Commands
Build full dataset:

python canada/financial_feature_store/build_financial_features.py

Quick smoke build:

python canada/financial_feature_store/build_financial_features.py --max-symbols 20 --calendar-end 2020-12-31

Validate:

python canada/financial_feature_store/validate_financial_features.py

StaticDataLoader smoke test:

python canada/financial_feature_store/test_static_dataloader.py

## Known Limitations in Phase 1
- Uses a compact canonical field set; more financial dimensions can be added iteratively.
- Fallback lag is heuristic when earnings_dates is missing.
- Currency/unit normalization is not yet applied in this phase.

## Next Step Suggestions
1. Expand canonical mapping coverage (profitability, leverage, efficiency groups).
2. Add unit/currency harmonization and per-field scaling metadata.
3. Add version registry and immutable snapshot publishing workflow.
