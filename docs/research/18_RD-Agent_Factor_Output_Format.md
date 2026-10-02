# 18. RD-Agent Factor Output Format

## Goal
Define a concrete, reusable output contract between RD-Agent-generated factor ideas and downstream data engineering components (including this project financial feature store).

## Scope
This document focuses on interface shape and operational constraints, not specific factor formulas.

## Required Artifacts
For each factor-release cycle, RD-Agent output should include:

1. Factor manifest (metadata)
2. Factor value table (event-level or daily-level)
3. Validation report

## 1) Factor Manifest
A machine-readable manifest (JSON or YAML) should include at least:

- factor_id: globally unique id (for example fin_revenue_growth_v1)
- version: semantic or timestamped version
- owner: team/person
- frequency: event | daily
- input_dependencies: upstream datasets and required columns
- pit_semantics: disclosure and availability assumptions
- transformation_steps: deterministic processing steps
- output_schema: columns, dtypes, index definition
- rollout_status: draft | canary | production | deprecated

## 2) Factor Value Table Contract
Two accepted shapes are supported.

### A. Event-level (preferred for PIT traceability)
Columns:

- instrument: string (for example TD_TO, CSCO)
- period_end: date
- report_date: date or null
- available_from: date (mandatory)
- field_or_factor: string
- value: float
- source_table: string
- version: optional string

Rules:

- available_from >= period_end
- if report_date exists, available_from >= report_date is allowed and preferred
- same (instrument, field_or_factor, period_end, version) should be unique

### B. Daily-level materialized panel (for model ingestion)
Index:

- MultiIndex(datetime, instrument)

Columns:

- factor columns only (float)

Rules:

- no values should appear before first available_from for a factor/instrument
- data sorted by index
- deterministic regeneration from event-level source

## 3) Validation Report Contract
Validation output should include:

- duplicate key count
- monotonic available_from violations
- leakage violations (daily datetime < first available_from)
- null ratio by factor
- symbol/factor coverage summary

## Compatibility with Qlib
Qlib StaticDataLoader can directly consume parquet in daily-level shape when:

- parquet index is MultiIndex(datetime, instrument)
- columns are numeric factor fields
- index is sorted

## Change Management
When factor logic changes:

- increment version
- keep old version immutable for audit/backtest reproducibility
- record migration note (what changed, expected behavior impact)

## Recommendations
- Keep event-level table as source of truth.
- Generate daily materialized table as downstream cache.
- Enforce PIT checks in CI before publishing new factor versions.
