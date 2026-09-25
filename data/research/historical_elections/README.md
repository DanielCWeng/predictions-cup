# Historical election research data

This directory is the canonical machine-readable package for **RESEARCH-PACKAGE-001**.

It packages the completed historical-election analogue research. It is **not** MAPPING-001 and must not be used as an exact/current SIG↔external-instrument identity map.

## Files

- `election_catalogue.json` — elections examined, inclusion/rejection status, data quality and provenance.
- `market_catalogue.csv` — representative historical market-family nodes and the IDs actually recovered.
- `analogue_matrix.csv` — historical analogue relationships by target role and by explicit similarity dimension.
- `replay_experiment_matrix.csv` — proposed historical experiments. These remain hypotheses, not validated strategy conclusions.

## Evidence correction: 2024/25 fill history

The previously referenced external Polymarket fill archive (`https://huggingface.co/datasets/PolyData/polymarket_trade_capture_5Mar2026`) is **intentionally excluded** from this package. The research owner identified that archive as materially undercounting 2024/25 fills, so it must not support B-grade replay claims.

For 2024/25 Polymarket studies:

- structural/semantic work may use verified metadata and price history;
- trade/fill-level replay must use a separately extracted and validated **Polyleviathan-derived** fill source or another market-specific source;
- this repository package does not contain such a fill extract;
- until that extract is verified, those rows are graded **C in this package**, even where the original working notes said B.

Polyleviathan repository reference: https://github.com/DanielCWeng/polymarketwhale

This correction does **not** downgrade the separately identified 2026 pmxt/Joseph book datasets.

## Data-quality meanings

- `A` — true event-time/order-book replay is supported for the relevant market family, subject to market-level continuity checks.
- `A/B_MIXED` — some phases have true book replay while another phase remains less complete or gated.
- `C_WITH_A_CANDIDATE` — price/metadata is usable; book replay is possible only after exact archived condition/token presence and continuity are verified.
- `C` — structural, semantic, price or slower event study only in this package.
- `D/C_INACTIVE` — reference/metadata only; insufficient economic activity for a serious replay.

A raw archive existing during an election date is not enough by itself to award an A grade to every market.

## Critical full-book limitation

Trade/fill history without resting-order and cancellation history is **not** a historical full-order-book replay.

Without book-event history:

- queue position cannot be reconstructed faithfully;
- cancellation behaviour is unknown;
- maker fills cannot be simulated faithfully;
- passive spread-capture estimates are not valid;
- only trade/price/event studies are justified at the available granularity.

The A-grade 2026 book datasets are therefore kept distinct from price/fill-only evidence.

## Relation to 2026

Historical elections are for:

- experiment design;
- plausible parameter ranges;
- structural relationships;
- regime definitions;
- time-to-election behaviour;
- lead/lag hypotheses;
- stress tests.

They do **not** automatically calibrate production coefficients. Live 2026 SIG data remains authoritative for venue-specific spread, queue, latency, liquidity, fill and risk calibration.

## Internal identity rules

- `election_id` is unique in `election_catalogue.json`.
- `catalogue_row_id` is unique in `market_catalogue.csv`.
- Blank `market_id`, `condition_id` or `token_id` means the completed research did not recover that identifier. It must stay blank rather than be invented.
- `analogue_row_id` is unique in `analogue_matrix.csv`.
- `experiment_id` is unique in `replay_experiment_matrix.csv`.
- Historical analogue rows are family/role similarities unless an exact historical ID is explicitly present.