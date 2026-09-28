# EXPERIMENT-005C — Joint Panel / Reduced-Rank Cross-Market Prediction

## Disposition

**Runner label:** `REGIME-SPECIFIC JOINT CANDIDATE`.

**Independent mapping:** **WITHIN-FAMILY JOINT CANDIDATE — event-concentrated, not cross-family.**

005C establishes that a synchronized multi-market price panel can add out-of-sample predictive information beyond target-only history and a leave-target-out common-state baseline in at least one historical election event. The strongest evidence is confined to one US_2024 event panel. It is not evidence for a broad, universal low-rank election-market factor.

The task remained research-only. No execution or P&L inference is made here.

## Frozen provenance

- Canonical starting SHA: `ba938bedcf63f562be8b26c9502e828391123867`.
- Scientific freeze commit: `ea7ed06dd47cb8240cc8a1e5dedabf00d009843f`.
- Frozen preregistration SHA-256: `b6505f8b07dd064cb1e4133a660c5d1f43864823b696539aa60e474eb9b8b6f5`.
- Completed implementation commit embedded in the Kaggle run: `94512d3c9b785efcbf04bcabc5c57c416e5ad524`.
- Immutable pre-HOLDOUT freeze SHA-256: `20f977ffb64a56ecfe230ba45d9d0021dcc97e5218b13eb307db896d14c87933`.
- The downloaded `PRE_HOLDOUT_FREEZE.json` hashes to exactly the value recorded in `run_manifest.json`.
- Earlier Kaggle attempts were terminated by an accidental CLI runtime cap (`kaggle kernels push -t 120`). They ended before meaningful DEV output and were not used for model selection. Version 11 was run without that cap and completed.

A pre-outcome data-source completeness amendment allowed DATA-002 matched-fill rows to recover US_2024 and CAN_2025 raw economic-fill fields absent from the current DATA-001 fills mount. It did not add fee, wallet, participant or role-derived alpha variables.

## Reconstruction audit

The economic-trade reconstruction passed cleanly in all five families.

| Family | Economic condition-second observations | Valid passive-notional fraction | Invalid role-structure groups |
| --- | ---: | ---: | ---: |
| US_2024 | 4,468,573 | 1.000000 | 0 |
| CAN_2025 | 297,906 | 1.000000 | 0 |
| COL_2026 | 239,771 | 1.000000 | 0 |
| HUN_2026 | 260,802 | 1.000000 | 0 |
| PER_2026 | 346,114 | 1.000000 | 0 |

A separate post-run Kaggle integrity audit then enforced the already-frozen exact source-identity rule directly on every source row. Across all five families it found **zero null-key rows and zero duplicate \`(tx_hash, log_index, token_id)\` keys**. This includes all 14,716,540 US_2024 DATA-002 fallback rows and all 854,625 CAN_2025 fallback rows. The first PyArrow implementation of this audit failed from a runtime memory/vector error before producing a scientific result; the completed external-memory DuckDB audit passed cleanly and changed no predictive design choice.

The modelling path used canonical YES prices, logit-price changes as the primary coordinate, explicit missingness/age masks, chronological TRAIN/DEV/HOLDOUT splits, and no random row splits.

## Search scale

- 45 metadata/coverage-defined panels were eligible.
- 16,006 DEV model rows were evaluated across frozen grids, horizons, lag depths, ridge penalties and rank candidates.
- 35 DEV selections passed the preregistered DEV gate and were written into the immutable pre-HOLDOUT freeze.
- Those same 35 selections were evaluated once on HOLDOUT.

## HOLDOUT result

Seven HOLDOUT rows satisfy the frozen joint-candidate gate: positive pooled MSE improvement versus B2, positive median per-target improvement, at least 50% target breadth, at least 3/5 positive time blocks, and effective source-market count of at least 3.

Those seven rows occur only in US_2024, COL_2026 and PER_2026 event panels. No CAN_2025 or HUN_2026 row passes that gate, and no family-wide panel is among the seven.

Only **three** of the seven also pass the equivalent probability-coordinate robustness gate. All three belong to the same US_2024 event panel `US_2024_event_6e91434eb80f`.

Only **two** of the seven have a block-bootstrap loss-advantage interval wholly above zero. Both are again in `US_2024_event_6e91434eb80f`.

### Strongest cell

`US_2024_event_6e91434eb80f`, 15-second grid / 15-second horizon, M2 ridge:

- pooled HOLDOUT MSE improvement vs B2: **+6.52%**;
- median per-target improvement: **+4.18%**;
- targets helped: **100%**;
- positive HOLDOUT blocks: **3/5**;
- effective source markets: **4.84**;
- logit-space bootstrap loss-advantage CI: **[0.000115, 0.009340]**, wholly above zero;
- probability-coordinate pooled improvement: **+2.28%**;
- probability-coordinate median target improvement: **+0.95%**;
- probability-coordinate breadth: **50%**;
- probability-coordinate positive blocks: **3/5**.

This is the strongest evidence in 005C.

The result is not explained by a single dominant source. Removing each of the three strongest source markets one at a time still leaves pooled improvements of approximately **+4.04%**, **+4.94%**, and **+6.48%**. Removing the highest-activity source leaves **+4.94%**. The effective source count of 4.84 is consistent with a moderately distributed panel.

The result is also not simply the leading common factor: projecting out the leading TRAIN predictor direction still leaves **+5.92%** pooled improvement with 100% target breadth. Both the circular-shift null (**-4.34%**) and the one-horizon delayed-panel placebo (**-5.53%**) fail sharply.

The full joint model does still depend on target own-history structure: zeroing own-market source coefficients drives the cell to **-1.18%** versus B2. That means the evidence supports a joint panel that improves prediction conditional on own-state information, not an autonomous cross-market signal that works without the target's own history.

### Other US_2024 cells

The same event panel supplies two additional frozen-gate positives:

- 5s grid / 15s horizon, M2: **+1.34%** pooled improvement, 50% breadth, 3/5 blocks, effective sources 4.51, probability-space **+2.33%**, and a bootstrap interval just above zero. This cell is more source-sensitive: removing any of the three strongest sources eliminates its pooled advantage.
- 5s grid / 30s horizon, M4 rank 4: **+2.46%** pooled improvement, **+5.45%** median target improvement, 75% breadth and 5/5 positive blocks. Probability-space improvement is **+3.71%** with 100% breadth. Its bootstrap interval slightly crosses zero, so it is supportive rather than decisive.

### PER_2026

Three rows in `PER_2026_event_77ddfdade7d2` pass the frozen logit-space gate at 15-second grid with 30s, 60s and 120s horizons.

The 60s and 120s rows show pooled improvements of about **+2.72%** and **+3.89%** respectively, but all three Peru bootstrap intervals cross zero. None passes the full probability-coordinate robustness gate. The 30s and 60s cells also lose their advantage when the leading common factor is removed. The 120s cell retains a small positive result after that ablation, but its probability-space median improvement is negative and only 2/5 probability-space blocks are positive.

Treat Peru as **suggestive / weak**, not independently confirmed joint structure.

### COL_2026

One Colombia event cell passes the frozen logit-space gate: 30s grid / 120s horizon, M4 rank 4, with **+1.98%** pooled improvement and 55.6% breadth.

It does not survive the important robustness checks:

- probability-coordinate pooled improvement is **-13.17%**;
- its bootstrap interval crosses zero;
- removing the leading common factor makes pooled improvement negative.

Treat this as **COMMON-STATE / coordinate-sensitive evidence**, not a robust joint candidate.

### CAN_2025 and HUN_2026

Neither family produces a HOLDOUT row that satisfies the frozen joint-candidate gate. There is therefore no basis in 005C to claim persistent joint price-panel predictability for those families.

## Low-rank structure

The strongest US_2024 15s/15s cell selected the full ridge model M2 rather than an explicitly truncated model, so the best result does **not** require hard rank truncation.

However, the TRAIN singular spectrum is materially compressed:

- return covariance effective rank: **6.13**;
- predictive cross-covariance effective rank: **3.02**;
- fitted predictive coefficient matrix effective rank: **3.02**;
- the first four fitted predictive singular directions account for effectively all fitted-matrix energy.

The US 5s/30s robustness cell explicitly selected M4 rank 4. Therefore 005C supports **low-dimensional predictive structure inside the successful event panel**, but not the stronger claim that one universal low-rank factor explains prediction markets across election families.

## Distributed versus concentrated

The evidence is mixed by horizon.

For the strongest US 15s/15s cell, the advantage is moderately distributed across sources: effective source count is 4.84, single-source removals preserve positive performance, common-factor removal preserves most of the gain, and time-shift/placebo controls fail.

For the US 5s/15s cell, the gain is more concentrated: removing any of the three strongest sources destroys the pooled advantage.

The Peru and Colombia candidates are materially more fragile to coordinate choice, common-factor removal or bootstrap uncertainty.

The correct overall statement is therefore:

> **Price-only joint prediction exists in at least one event-level panel and is not reducible to a single source or a single common factor there. It is not broad enough to call a cross-family law, and some secondary positives are concentration/common-state artifacts.**

## Relationship to baselines

B0 and B1 are useful sanity baselines, but the key comparison throughout this report is B2: target own-history plus leave-target-out contemporaneous common-state information.

The successful US cells beat B2 on HOLDOUT. This matters because the result is incremental to a simple market-wide/common-move explanation rather than merely showing that related markets co-move.

## Final research label

**WITHIN-FAMILY JOINT CANDIDATE — event-concentrated.**

This maps the runner's `REGIME-SPECIFIC JOINT CANDIDATE` label into the requested 005C taxonomy.

Do **not** promote the stronger `CROSS-FAMILY JOINT CANDIDATE` claim. The evidence does not survive broadly enough across families.

## Follow-up boundary

Any follow-up should be a new preregistered experiment rather than a reinterpretation of 005C HOLDOUT. The most defensible target is the US_2024 event-level mechanism: identify the economic relationships among the five selected markets, test whether the same multi-source structure appears in an untouched analogue, and only then consider whether it warrants an execution-oriented experiment.
