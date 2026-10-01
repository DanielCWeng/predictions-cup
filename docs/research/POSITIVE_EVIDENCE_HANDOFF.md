# POSITIVE EVIDENCE HANDOFF

## Freeze

STARTING_MAIN_SHA: `611b048ce0ee4630c01a9500cb021a70afba8474`

SCIENTIFIC_EVIDENCE_FREEZE_SHA: `7c958695b94ad9ab40eec0681c4688a37bf36f57`

SCIENTIFIC_EVIDENCE_FREEZE_SHA semantics: this freezes the scientific conclusions/effect sizes before the later administrative canonicality refresh. PR #114 subsequently merged into `main` at `662969df4e481f1cbd9417c923ae35d3a0aa9f9f`; later synthesis commits update 005G canonicality and repository synchronization only, not the scientific conclusion.

BRANCH: `research/synthesis-001-positive-evidence`

CANONICALITY_REFRESH_MAIN_SHA: `662969df4e481f1cbd9417c923ae35d3a0aa9f9f`

MERGE_POLICY: `DO_NOT_MERGE_FROM_THIS_LANE`

REAL_SIG_ORDERS_SENT: `NO`

NEW_EXPERIMENT_RUN: `NO`

KAGGLE_RUN: `NO`

PRODUCTION_CONFIG_MODIFIED: `NO`

## Outputs

- `docs/research/POSITIVE_EVIDENCE_LEDGER.md`
- `docs/research/POSITIVE_EVIDENCE_SYNTHESIS.md`
- `data/research/positive_evidence_ledger.json`
- `data/research/positive_evidence_graph.json`
- `docs/research/POSITIVE_EVIDENCE_HANDOFF.md`

## PRS_REVIEWED

Primary research/evidence PRs reviewed directly or through their canonical landed artifacts:

```text
#27   EXPERIMENT-004A
#28   EXPERIMENT-004A.2
#30   EXPERIMENT-004B
#34   EXPERIMENT-004C-A
#35   EXPERIMENT-004C-B
#37   EXPERIMENT-004C-C
#39   EXPERIMENT-004C-D
#38   EXPERIMENT-005A
#41   EXPERIMENT-005C
#42   EXPERIMENT-005D
#43   EXPERIMENT-005E
#44   EXPERIMENT-005F
#45   EXPERIMENT-005B original historical atlas
#51   EXPERIMENT-005B ordering falsification
#52   EXPERIMENT-005B DATA-003 transfer
#58   PRED-006
#60   PRED-007
#63   R3-FV-001
#64   R3-FV-001M
#72   MM-REPLAY-001
#75   STRUCT-SCAN-001
#93   EXPERIMENT-005I
#94   EXPERIMENT-005H
#114  EXPERIMENT-005G
```

The archaeology also used later merged closeouts/current-main files to supersede earlier PR interpretations where required.

## EXPERIMENTS_REVIEWED

```text
EXPERIMENT-002
EXPERIMENT-003
EXPERIMENT-004A
EXPERIMENT-004A.2
EXPERIMENT-004B
EXPERIMENT-004C-A
EXPERIMENT-004C-B
EXPERIMENT-004C-C
EXPERIMENT-004C-D
EXPERIMENT-005A
EXPERIMENT-005B
EXPERIMENT-005C
EXPERIMENT-005D
EXPERIMENT-005E
EXPERIMENT-005F
EXPERIMENT-005G
EXPERIMENT-005H
EXPERIMENT-005I
PRED-006
PRED-007
R3-FV-001
R3-FV-001M
STRUCT-SCAN-001
MM-REPLAY-001
```

The prompt list was treated as a minimum, not as an exhaustive boundary.

## ARTIFACTS_REVIEWED

Principal canonical/relevant evidence surfaces included:

```text
EXPERIMENT_REGISTRY.md
RESEARCH_HANDOFFS.md
CURRENT_STATE.md

docs/experiments/EXPERIMENT_003_HISTORICAL_ALPHA_BATTERY.md
data/experiments/experiment_003/results/battery_summary.json
data/experiments/experiment_003/results/run_manifest.json
data/experiments/experiment_003/protocol_conformance_audit.json
data/experiments/experiment_003/inference_limitations.json

data/experiments/experiment_004b/review/MASTER_HANDOFF_004B_B.md
docs/experiments/EXPERIMENT_004C_FINAL_HANDOFF.md
data/experiments/experiment_004c/a/FINAL_RESEARCH_REPORT.md
data/experiments/experiment_004c_b/review/FINAL_REPORT_004C_B.md

data/experiments/experiment_005a/results/FINAL_RESEARCH_REPORT.md
data/experiments/experiment_005b/FINAL_DISPOSITION.md
docs/experiments/EXPERIMENT_005C_REVIEW_FOLLOWUP.md
docs/experiments/EXPERIMENT_005D_FINAL_REPORT.md
data/experiments/experiment_005e/results/FINAL_REPORT_005E.md
docs/experiments/EXPERIMENT_005F_FINAL_REPORT.md

docs/experiments/PRED_006_FINAL_REPORT.md
docs/experiments/PRED_007_FINAL_REPORT.md

data/research/r3_fv_001/MASTER_HANDOFF_R3_FV_001.md
data/research/r3_fv_001_math_challengers/R3_METHOD_HANDOFF.md

docs/experiments/MASTER_HANDOFF_005I.md

experiment/005g-data003-orderbook-atlas:
  docs/experiments/EXPERIMENT_005G_FINAL.md
  data/experiments/experiment_005g/FINAL_RESULT.json
  docs/experiments/EXPERIMENT_005G_FAILURE_FORENSICS.md
  data/experiments/experiment_005g/FAILURE_FORENSICS.json

experiment/005h-fill-orderbook-interaction-atlas:
  data/experiments/experiment_005h/EVIDENCE_REGISTRY.json

data/experiments/mm_replay_001/MASTER_HANDOFF_MM_REPLAY_001.md
```

Historical PR descriptions/comments were used where they contained scientific evidence or later failure-forensics state not duplicated in a main-branch summary.

## POST-MERGE 005H REFRESH

The original synthesis was merged before EXPERIMENT-005H completed its one-shot B0. PR #94 remains unmerged, but two frozen historical B0 candidates now survive and are carried in this follow-up as **NON_CANONICAL / FUTURE_CONFIRMATION_REQUIRED** evidence:

- **C04 Arrival-State:** B0 AUC **0.6882**; token-cluster bootstrap 95% **0.6777–0.7000**; chronological halves **0.6913 / 0.6852**; 14,396 fills + 14,396 matched controls; 1,101 token clusters.
- **C05 Direction-State:** B0 AUC **0.6745**; bootstrap **0.6398–0.7192**; halves **0.6973 / 0.6547**; 12,661 scored fills; 1,074 token clusters.

Interpretation boundary: C04 is market interaction/fill-arrival state, **not** queue-position-aware probability that our own quote fills. C05 predicts aggressor BUY/SELL conditional on a fill, **not** post-fill toxicity or P&L. Both route to LIVE_DIAG / PAPER context and do not authorise MAKE or LIVE execution.

005H rejected C01 relative size, C02 failed replenishment and C03 fill-beyond-state before B0. Economic-positive counts remain **E4 = 0, E5 = 0**.

## SURVIVING_POSITIVE_FINDINGS

### Strongest replicated finding

`genuine_age_s → 300s BBO update / renewal hazard`

- 005F sealed HOLDOUT PRE result: **+35.35% relative MSE**, 297,384 obs.
- fresh 005G DATA-003 replication: **+26.5024%**, 222,567 rows, 658 markets, 48/48 positive blocks.
- evidence ladder: **E2 + E3**.
- canonicality: PR #114 merged into `main` at `662969df4e481f1cbd9417c923ae35d3a0aa9f9f`; the E3 replication is canonical repository evidence.
- economic conversion: **FAILED** as part of 005G_STATE_HAZARD_V1; predictive evidence remains intact.

### Other major surviving families

- 005F ACTIVE genuine-age update hazard: +35.16%.
- 005F ACTIVE genuine-age jump hazard: +16.04%.
- PRED-006 C01/C02 current-universe price-change hazards: +8.18% / +8.36% relative Brier.
- 005I five-minute mean reversion: 67.26% reversal; every temporal worker >66%.
- 005I depth-normalised OFI: +0.004165 HOLDOUT log-loss improvement; 5/5 workers positive.
- 005I liquidity/discovery/resilience states.
- 005D late-count Peru/Colombia structural convergence.
- 005D broad same-event reconstruction/coherence.
- 005E frozen secondary signed-flow / next-change diagnostics.
- 004C-B narrow Colombia competitive-family effect.
- 005A narrow same-family 5s effect.
- 005G state-dwell/renewal discoveries, now canonical via merged PR #114.

Machine-readable individual rows: **28 surviving-positive ledger records**, of which **9 are canonical 005G discovery rows**. The combined 005F→005G replication row is retained separately from those nine because it traces the same frozen mechanism across genuinely fresh datasets.

## FALSIFIED_HISTORICAL_POSITIVES

1. **EXPERIMENT-003 participant 300s nominal rejection**
   - nominal raw p ~0.0014 / BH q ~0.035;
   - confirmatory validity invalidated by protocol deviation;
   - identity interpretation contaminated by NegRisk exchange contracts;
   - archive only.

2. **004C-C raw mapped PM→SIG 30s positive**
   - wrong-contract control stronger;
   - no EXACT contract survives FDR;
   - crossing economics strongly negative;
   - common-state interpretation retained, mapping-specific leadership rejected.

3. **005B seven historical realised-movement models**
   - historical findings survive corrected Polygon ordering;
   - frozen fresh DATA-003 transfer **0/7**;
   - current-universe alpha claim: none.

4. **005C US joint-panel 15s**
   - descriptive HOLDOUT gains positive;
   - dependence-preserving familywise max-t adjusted p **0.8918**;
   - downgraded to discovery comparator.

## SEALED_HOLDOUT_POSITIVES

Canonical/merged:
- 005D late-count structural convergence (Peru + Colombia).
- 005D reconstruction/coherence.
- 005E secondary signed-flow and next-change diagnostics (FOLLOW_UP_ONLY).
- 005F four supported coordinates.
- PRED-006 C01/C02 one-shot FINAL.
- 005I five supported preregistered gates/families.
- 005G 10/12 frozen candidates pass the complete sealed-holdout gate, including one strict fresh replication and nine new discoveries.

005H is **not** in this list: its B0 process completed replay but failed before canonical holdout scoring tables were produced.

## ECONOMIC_POSITIVES

`NONE`

- **E4 positive count: 0**
- **E5 positive count: 0**

The most direct economic conversion attempt, 005G_STATE_HAZARD_V1, failed:
- WIDTH_ONLY 0/8;
- SIZE_ONLY 0/8;
- WAIT_ONLY 0/4;
- REFRESH_ONLY 0/8.

The state signal still predicted its intended state targets, but adverse-fill AUC was ~0.456 and the monotone “high hazard = quote less” conversion destroyed more favourable-fill value than adverse loss avoided.

004C-C executable crossing economics were also strongly negative.

MM-REPLAY-001 had no accepted completed result at the snapshot.

## DIRECTIONAL_POSITIVES

Strongest canonical directional evidence:
- **005I five-minute mean reversion**.
- **005I depth-normalised OFI**.

Narrower directional/conditional evidence:
- 005D late-count structural convergence.
- 005E participant signed-markout / next-change secondary diagnostics.
- 004C-B competitive-family redistribution.
- 005A same-family 5s.

Important boundary:
- the broadest, most replicated research family remains movement/renewal hazard rather than direction.
- directional evidence is credible but less independently replicated on fresh future datasets.

## MOVEMENT_HAZARD_POSITIVES

- 005F genuine-age update and jump hazard.
- 005G exact fresh genuine-age replication (canonical, merged PR #114).
- 005G state-dwell transition hazard and smaller renewal variables (canonical, merged PR #114).
- PRED-006 C01/C02 next-price-change hazard.
- 005I PRICE_DISCOVERY state transition.
- 005I LIQUIDITY_STRESS persistence/replenishment.
- 005I withdrawal/replenishment → future absolute-movement association.

## CENTRAL_HYPOTHESIS_RESULT

**SUPPORTED WITH AN IMPORTANT EXCEPTION.**

The strongest replicated/current-universe results do primarily predict impending movement, book renewal or state change.

However, the repository now contains credible sealed-holdout directional information, especially 005I mean reversion and depth-normalised OFI. Therefore the accurate statement is not “we cannot predict direction.” It is:

> Movement/change timing is the strongest and most replicated layer; direction exists but is newer/narrower; economic conversion is weaker still.

## LIKELY_REDUNDANCY

High-risk duplicate cluster:
- genuine age;
- state dwell;
- price-change age;
- PRED-006 change hazard;
- PRICE_DISCOVERY transition context;
- parts of spread×distance / renewal descriptors.

Interpretation: different measurements of a common **market transition / renewal hazard** state.

Common-event/family cluster:
- 004B directed structure;
- 004C-C raw response;
- 005A same-family state;
- 004C-B competitive family.

Do not count all of these as independent alpha sources.

## GENUINELY_COMPLEMENTARY_CANDIDATES

Research combinations worth testing, not strategy promotion:
- transition hazard + 005I mean reversion;
- transition hazard + depth-normalised OFI;
- renewal hazard + structural relative value;
- liquidity state + participant signed-flow quality;
- structural reconstruction + mean reversion.

The common logic is **WHEN + DIRECTION/LEVEL**, not “more correlated hazard features”.

## UNRESOLVED_AMBIGUITIES

1. **005G canonicality:** resolved. PR #114 merged into `main` at `662969df4e481f1cbd9417c923ae35d3a0aa9f9f`; this changes canonicality only, not the sealed scientific result or the failed economic-conversion conclusion.
2. **005H holdout:** B0 replay completed, scoring result unavailable after technical failure; C04/C05 cannot be counted.
3. **MM-REPLAY-001:** no accepted economic result at snapshot.
4. **005I serial dependence:** 55,340 mean-reversion observations are overlapping sampled states, not independent episodes.
5. **005E promotion status:** secondary findings are frozen HOLDOUT diagnostics but were not the primary preregistered promotion target.
6. **Older exact split/count details:** several early programmes do not expose every requested train/dev/holdout count in the final top-level artifact; the ledger leaves those fields null rather than inferring them.
7. **Economic bridge:** no validated fill-probability or fill-toxicity model connects current predictive evidence to post-cost execution.

## FINAL SYNTHESIS

The project is not empty-handed. It has a strong, repeatedly validated ability to identify **elevated transition/renewal risk**, plus a newer canonical directional layer. What it does **not** have is proof that these layers combine into positive after-cost execution.

The smallest post-launch question should therefore be incremental and prospective:

> On genuinely future capture, conditional on frozen renewal/change hazard, does the frozen 005I directional layer add incremental signed-markout information beyond hazard-only and direction-only baselines?

No giant new feature search is justified before that narrower question is answered.

**The Predictions Cup research programme is currently best at predicting when a market or order book is likely to change state or reprice.**
