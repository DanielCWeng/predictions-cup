# EXPERIMENT-005B — Prior-work overlap crosswalk

This crosswalk was written before the 005B TRAIN/DEV predictive screen. Its purpose is provenance: distinguish exact repetition from a broader scalar reformulation without using prior winning cells, best horizons, subgroup winners or effect magnitudes to design 005B.

| Prior work | Prior mechanism | 005B overlap | Classification | 005B treatment |
|---|---|---|---|---|
| EXPERIMENT-003 LEADLAG-001 | Target own logit move baseline plus explicit reference-market logit movement; predefined short-horizon prediction battery | Own price/logit history and leave-target-out family/event aggregate movement are both present | **Mechanism reformulation, not exact repeat** | 005B uses the DATA-002 canonical economic-fill series, a broad scalar feature census, additional 15s/120s and event-time targets, and no pairwise directed source-target leaderboard. |
| EXPERIMENT-003 RV-001 structural residual | Structural/relationship residual prediction | Superficial overlap only through target-excluding family/event context | **Not repeated** | Structural fair-value/residual logic remains outside 005B and belongs to the structural-RV lane (005D). |
| EXPERIMENT-003 MICROSTRUCTURE-001 | Order-book/microstructure predictor battery | None beyond generic price/activity baselines | **Not repeated** | Order-book depth, spread and imbalance are excluded from 005B; 005F owns microstructure/liquidity. |
| EXPERIMENT-003 PARTICIPANT-001 | Participant-derived predictors and participant-label controls | None | **Not repeated** | Identity/role/fee/address predictors are prohibited in 005B; 005A/005E own participant and role mechanisms. |
| EXPERIMENT-003 LOWRANK-001 | Leave-target-out TRAIN-only low-rank panel/SVD model | 005B includes only simple leave-target-out scalar family/event aggregates | **Not repeated** | Reduced-rank whole-panel prediction is explicitly reserved for 005C. |
| EXPERIMENT-004A | Event-time coverage and frozen election-regime definitions | PRE_ELECTION / ELECTION_DAY_PRE_RESULTS / ACTIVE_RESULTS / LATE_COUNT / enabled POST_RESOLUTION_DIAGNOSTIC indicators | **Documented infrastructure reuse** | 005B copies committed 004A boundaries verbatim where available. US_2024 and CAN_2025 remain missing rather than inferred. No boundary is moved using prices. |
| EXPERIMENT-004B Dynamics/Stats | Directed source→target price response plus depth/BBO/fill-activity response matrices across regimes | Price→price and fill-activity→future-price ideas are represented at a broader scalar level | **Mechanism reformulation, not exact repeat** | 005B does not enumerate directed pair edges, does not use depth/BBO state, and does not reuse 004B’s ranked response cells. It screens generic own-history/activity/context predictors across all declared horizons. |
| EXPERIMENT-004C-A Information Propagation / Freshness | Tests whether 004B directed structure survives quote-age, update-timing, target-history, common-event and activity controls | Own movement, common-event movement and anonymous activity are present | **Control-variable overlap; causal mechanism not repeated** | 005B does not infer quote renewal, staleness or source→target excitation. The shared controls enter as ordinary scalar predictors only. |
| EXPERIMENT-004C-B Structural / Competitive-Family Redistribution | Coherent/competitive probability redistribution inside related market families | Leave-target-out family/event context is mechanically related | **Mechanism reformulation only** | 005B does not project into structural constraints, fit competitive-family redistribution, or evaluate basket coherence. Those remain structural-RV work. |
| EXPERIMENT-004C-C Cross-Venue Price Discovery | Polymarket→SIG mapped cross-venue transmission | None | **Not repeated** | 005B is Polymarket historical price/fill discovery only; SIG/cross-venue timing is excluded. |
| EXPERIMENT-004C-D Conditional Response / Genuine Quote Renewal | Genuine BBO-change age, family innovation/activity interactions, conditional quote renewal and magnitude response | Generic movement/activity state exists in both | **Not exact repeat** | 005B has no BBO renewal state, quote-age clock, spread feature, outside-family matched control or conditional-hazard model. |

## Exact-repeat statement

005B intentionally repeats **primitive baselines and transformations**, not a prior experiment battery: canonical probability/logit coordinates, own recent movement, anonymous activity, leave-target-out aggregation, chronological purging/embargo and documented 004A regimes.

No EXPERIMENT-003 or EXPERIMENT-004 primary hypothesis/model is rerun unchanged. In particular, 005B does not rerun pairwise lead-lag, structural residual, participant, low-rank panel, book-microstructure, quote-freshness, competitive-redistribution, cross-venue or genuine-quote-renewal batteries.

## Sources used for this crosswalk

- `data/experiments/experiment_003/preregistration.json`
- `data/experiments/experiment_003/results/battery_summary.json`
- `data/experiments/experiment_004a/regime_definitions.json`
- `data/experiments/experiment_004b/review/MASTER_HANDOFF_004B_B.md`
- `data/experiments/experiment_004c/a/FINAL_RESEARCH_REPORT.md`
- `data/experiments/experiment_004c_b/review/FINAL_REPORT_004C_B.md`
- `data/experiments/experiment_004c/crossvenue/MASTER_HANDOFF_004C_C.md`
- `data/experiments/experiment_004c_d/results/FINAL_RESEARCH_REPORT.md`
