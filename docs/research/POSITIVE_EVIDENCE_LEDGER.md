# POSITIVE EVIDENCE LEDGER

**RESEARCH-SYNTHESIS-001**  
**Starting main:** `611b048ce0ee4630c01a9500cb021a70afba8474`  
**Snapshot:** 2026-10-01  
**Purpose:** canonical audit of positive predictive, structural and economic evidence found across the Predictions Cup research programme.

## Rules used in this ledger

This ledger applies the brief literally:

- **E0 — DISCOVERY ONLY:** TRAIN/in-sample/descriptive.
- **E1 — TEMPORAL / DEV REPLICATION:** meaningful forward/development split.
- **E2 — SEALED HOLDOUT CONFIRMED:** genuinely untouched holdout/final.
- **E3 — CROSS-DATASET / CROSS-EVENT REPLICATION:** reappeared on genuinely fresh data/events.
- **E4 — ECONOMIC REPLAY SUPPORT:** improved realistic economic replay.
- **E5 — LIVE/SHADOW ECONOMIC SUPPORT:** observed positive current live/shadow economics.

These are categories, not scores. A large predictive statistic is not P&L. A movement predictor is not a directional predictor. A descriptive association is not a mechanism. Where exact periods or counts were not recoverable from the final artifact inspected, this ledger says so rather than inferring them.

Historical unmerged evidence is labelled **NON_CANONICAL / UNMERGED**. Active implementation branches are not used as research evidence.

## Executive ledger

| Result | Programme | What it predicts | WHEN | DIRECTION | Evidence | Economic conversion | Current route |
|---|---|---|---|---|---|---|---|
| System-level directed dependence | 004B | common/directed election-market structure | weak | no transferable pair direction | E0 | NOT_TESTED | RESEARCH_ONLY |
| Colombia soft competitive family | 004C-B | 30–120s family redistribution | conditional | narrow yes | E1 | NOT_TESTED | RESEARCH_ONLY |
| Same-family PRE 5s | 005A | near-term same-family repricing | conditional | narrow yes | E1 | NOT_TESTED | RESEARCH_ONLY |
| Peru late-count structural convergence | 005D | 5s/60s repair direction | LATE_COUNT | yes | E2 | NOT_TESTED | RESEARCH_ONLY |
| Colombia late-count structural convergence | 005D | 5s repair direction | LATE_COUNT | yes | E2 | NOT_TESTED | RESEARCH_ONLY |
| Same-event structural reconstruction | 005D | held-out/coherent price level | n/a | not independent future direction | E2 | NOT_TESTED | STRUCTURAL_SCANNER |
| Participant signed markout | 005E | whether observed participant direction is followed | conditional | yes, secondary | E2* | NOT_TESTED | RESEARCH_ONLY |
| Participant next-change classifier | 005E | next price-change class | conditional | yes, secondary | E2* | NOT_TESTED | RESEARCH_ONLY |
| ACTIVE jump hazard | 005F | 300s unsigned jump risk | strong | none | E2 | NOT_TESTED | SHADOW_ONLY |
| ACTIVE update hazard | 005F | 300s BBO renewal | strong | none | E2 | NOT_TESTED | SHADOW_ONLY |
| PRE update hazard / fresh replication | 005F → 005G | 300s BBO renewal | very strong | none | **E2 + E3** | **FAILED as 005G bundle** | SHADOW_ONLY |
| PRE spread/liquidity state | 005F | 15s spread change | weak | no price direction | E2 | NOT_TESTED | LIVE_LEARN_CONTEXT |
| Next-price-change hazard 1800s | PRED-006 C01 | whether price changes | strong | none | E2 | NOT_TESTED | SHADOW_ONLY |
| Next-price-change hazard 600s | PRED-006 C02 | whether price changes | strong | none | E2 | NOT_TESTED | SHADOW_ONLY |
| 5m mean reversion | 005I | reversal vs continuation | after a qualifying move | **yes** | E2 | NOT_TESTED | LIVE_LEARN_CONTEXT |
| Depth-normalised OFI | 005I | next-minute directional class | current flow state | **yes** | E2 | NOT_TESTED | LIVE_LEARN_CONTEXT |
| PRICE_DISCOVERY state | 005I | short-lived high-activity state exit | strong | none | E2 | NOT_TESTED | LIVE_LEARN_CONTEXT |
| LIQUIDITY_STRESS state | 005I | persistent stressed-liquidity state | strong | none | E2 | NOT_TESTED | RISK_CONTEXT |
| Withdrawal → replenishment | 005I | subsequent absolute movement | after withdrawal | none | E2 | NOT_TESTED | LIVE_LEARN_CONTEXT |
| State dwell → transition | 005G | 60s/300s state transition | very strong | none | E2† | **FAILED as bundle** | SHADOW_ONLY |
| Spread×distance renewal | 005G | 60s/300s BBO renewal | strong | none | E2† | **FAILED as bundle** | SHADOW_ONLY |
| Smaller renewal-state variables | 005G | BBO renewal | supported | none | E2† | **FAILED as bundle** | SHADOW_ONLY |

\* 005E findings are frozen **secondary** HOLDOUT diagnostics and explicitly remain FOLLOW_UP_ONLY.  
† 005G PR #114 is **NON_CANONICAL / UNMERGED** at the starting-main snapshot.

There is **no positive E4 or E5 result** in the accepted history inspected.

---

## Detailed positive results

### 004B — system-level directed structure

**Artifact:** `data/experiments/experiment_004b/review/MASTER_HANDOFF_004B_B.md` / PR #30.

The aggregate 30-second directed response statistic exceeded circular/block nulls and survived BH in HUN PRE, Peru PRE and Peru ACTIVE; HUN ACTIVE did not. Individual leader/follower identities were not established, and later 004C work showed generic pairwise propagation does not survive stronger incremental controls.

**Target:** DIRECTION / system dependence.  
**Evidence:** E0 discovery evidence.  
**Current interpretation:** election markets exhibit non-random common/directed structure, but this is not a transferable directional edge.

### 004C-B — Colombia soft competitive-family redistribution

**Artifact:** `data/experiments/experiment_004c_b/review/FINAL_REPORT_004C_B.md` / PR #35.

Frozen PRE_ELECTION Colombia first-round family, 30s:
- 20,550 controlled rows;
- controlled leave-target-out beta **0.0528136**;
- time-null p **0.000999**;
- BH q **0.00999**;
- matched-membership p **0.000999**;
- same family survives 60s and 120s, not 300s.

Comparable Peru structures did not replicate.

**Target:** DIRECTION + RELATIVE_VALUE.  
**Evidence:** E1.  
**Current interpretation:** narrow family-specific directional/redistribution effect; not a general law.

### 005A — narrow same-family 5s effect

**Artifact:** `data/experiments/experiment_005a/results/FINAL_RESEARCH_REPORT.md` / PR #38.

The sole surviving cell was A3 SAME_FAMILY / PRE_ELECTION / 5s:
- 92,007 validation rows;
- relative OOS MSE gain **0.0007655%**;
- circular p **0.005**;
- 300s block p **0.002**;
- intersection p **0.005**;
- within-family BH q **0.050**.

The broader own-market, semantic, participant-identity, maker and liquidity batteries did not survive.

**Target:** DIRECTION + RELATIVE_VALUE.  
**Evidence:** E1.  
**Current interpretation:** real enough to retain as a replication candidate, far too narrow/small to call broad role-aware alpha.

### 005D — late-count structural convergence

**Artifact:** `docs/experiments/EXPERIMENT_005D_FINAL_REPORT.md` / PR #42.

All 13 frozen predictive HOLDOUT cells were later shown to lie **100% in canonical LATE_COUNT**. None were PRE_ELECTION, ELECTION_DAY_PRE_RESULTS or ACTIVE_RESULTS.

**Peru Fuerza Popular Senate → Chamber most-seats**
- 5s: n=10,841, q=0.01299, standardized effect 0.0285, delayed-state ratio 0.620.
- 60s: n=9,942, q=0.01818, standardized effect 0.0595, delayed-state ratio 0.773.
- 15s/30s fail the preregistered delayed-state gate.
- longer-block dependence sensitivity is mixed.

**Colombia Abelardo overall winner → Antioquia runoff**
- 5s: n=17,156;
- q=0.00649;
- bootstrap interval [2.38e-10, 6.67e-10];
- standardized effect 0.00392;
- positive-contribution fraction 97.9%;
- delayed-state ratio 0.444;
- fixed 30m/1h/4h/8h sensitivity stays positive, but economic scale is extremely small.

**Target:** DIRECTION + RELATIVE_VALUE.  
**Evidence:** E2.  
**Current interpretation:** narrow late-count convergence, not generic election-time structural alpha.

### 005D — reconstruction/coherence

The 22 evaluable frozen reconstruction cells produced median HOLDOUT RMSE improvements versus the declared naive same-event baseline of:
- Canada **+47.0%**;
- Colombia **+96.3%**;
- Hungary **+99.8%**;
- Peru **+97.8%**;
- U.S. **+96.0%**.

This is genuine structural information, but it is reconstruction rather than independent future-price alpha. Hard arbitrage was not established.

**Target:** RELATIVE_VALUE + STRUCTURAL_INCONSISTENCY.  
**Evidence:** E2 reconstruction evidence.  
**Route:** STRUCTURAL_SCANNER.

### 005E — participant-flow quality, secondary only

**Artifact:** `data/experiments/experiment_005e/results/FINAL_REPORT_005E.md` / PR #43.

The preregistered primary participant-conditioned 60s price target failed. Two frozen secondary diagnostics were positive:

**Signed markout**
- 60s gain **+0.00924532**;
- 300s gain **+0.01231031**;
- positive in all five families.

**Next-change classification**
- log loss **0.677277315 → 0.666660659**;
- gain **0.010616656**;
- accuracy **0.584708 → 0.604946**.

**Target:** PARTICIPANT_BEHAVIOUR + MARKOUT / DIRECTION.  
**Evidence:** E2 secondary evidence, **not a primary promoted result**.  
**Current interpretation:** useful hypothesis generation for signed flow quality. Fresh preregistration is required.

### 005F — genuine-age / renewal-hazard family

**Artifact:** `docs/experiments/EXPERIMENT_005F_FINAL_REPORT.md`.

The first sealed HOLDOUT produced four supported coordinates.

**ACTIVE_RESULTS: `genuine_age_s → 300s jump hazard`**
- 14,037 observations;
- 61 markets, 5 events, 3 families;
- **16.04%** relative MSE improvement;
- sign-flip p=0.0115;
- bootstrap lower=0.00423;
- all market/event/family leave-outs positive.

**ACTIVE_RESULTS: `genuine_age_s → 300s update hazard`**
- 14,177 observations;
- **35.16%** relative MSE improvement;
- p=0.000443;
- bootstrap lower=0.04125;
- all leave-outs positive.

**PRE_ELECTION: `genuine_age_s → 300s update hazard`**
- 297,384 observations;
- 108 markets, 5 events, 3 families;
- **35.35%** relative MSE improvement;
- p=0.000244;
- bootstrap lower=0.02763;
- all leave-outs positive.

**PRE_ELECTION: `trade_abs_impact_60 → 15s spread change`**
- 91,739 observations;
- **0.74%** relative MSE improvement;
- p=0.000976;
- effect is economically small and delayed variants preserve most lift.

The delay falsifications matter. The update-hazard signal remains predictive when shifted, so the correct interpretation is a **persistent economic-book staleness / renewal state**, not an instantaneous causal impulse.

### 005F → 005G — strongest fresh replication

**005G artifact:** `experiment/005g-data003-orderbook-atlas:data/experiments/experiment_005g/FINAL_RESULT.json` / PR #114 (**NON_CANONICAL / UNMERGED**).

The exact 005F PRE update signal was frozen and retested on the fresh DATA-003 orderbook corpus:

`genuine_age_s → update_h300`
- 222,567 HOLDOUT rows;
- 658 markets;
- **+26.5024%** relative MSE improvement;
- **48/48** positive blocks;
- sign-flip p **0.000122**;
- block-bootstrap lower **0.02495**;
- positive leave-market minimum.

This is the cleanest E3 result in the programme. It materially strengthens the scientific claim that observable economic book age carries information about future renewal. It does **not** supply direction or P&L.

### PRED-006 — current-universe price-change hazard

**Artifact:** `docs/experiments/PRED_006_FINAL_REPORT.md` / PR #58.

Both frozen one-shot FINAL candidates passed.

**C01 — 1800s next-price-change hazard**
- 6,924 FINAL rows;
- 462 conditions, 198 SIG markets;
- **8.18%** relative Brier improvement;
- AUC **0.712**;
- bootstrap 2.5% **0.007876**;
- 59.96% conditions positive;
- first/second halves **11.15% / 5.16%**.

**C02 — 600s next-price-change hazard**
- 6,967 rows;
- 466 conditions, 198 SIG markets;
- **8.36%** relative Brier improvement;
- AUC **0.697**;
- bootstrap 2.5% **0.005791**;
- 58.37% conditions positive;
- first/second halves **12.12% / 4.29%**.

These answer **WHEN a mapped market is likely to reprice**, not which direction it moves. Future CAPTURE confirmation is still required before promotion.

### 005I — canonical directional evidence

**Artifact:** `docs/experiments/MASTER_HANDOFF_005I.md` / PR #93, merged at `4a040a7d309df26098af5e81e96c7f1108d03117`.

This lane is the important exception to a pure “we only know when” story.

**Five-minute mean reversion**
- 55,340 qualifying sampled HOLDOUT minute-states/move observations;
- **67.26% reversal**;
- every temporal worker above **66%**;
- all frozen move-size buckets above 63%;
- price regions above 63%;
- relative-spread buckets above 64%;
- PRICE_DISCOVERY reverses ~68.1%.

The 55,340 observations are **not independent episodes** because adjacent five-minute windows overlap.

**Depth-normalised OFI**
- 1,195,563 non-zero next-minute HOLDOUT observations;
- frozen challenger improves log loss by **0.004165**;
- **5/5 temporal workers positive**.

Raw OFI sign is not the result; it is often contrarian. The multivariate frozen model carries the increment.

**PRICE_DISCOVERY**
- 281,619 states;
- exit hazard **53.56%/min**;
- p90 dwell **3–4m**;
- 47.38% exits to POST_SHOCK, but that transition is partly mechanical because of taxonomy construction.

**LIQUIDITY_STRESS**
- 723,144 states;
- exit hazard **4.93%/min**;
- p90 dwell **~26–38m**;
- 23.15% exits to REPLENISHMENT.

**Withdrawal / replenishment resilience**
- replenished observations: 321,350, mean subsequent abs-5m **0.000842**;
- non-replenished: 533,083, mean **0.001272**;
- **33.83% lower** subsequent absolute movement when replenishment is observed;
- same direction in **5/5 workers**.

These results are predictive/descriptive. None has yet demonstrated spread/fee/fill/inventory-adjusted economics.

### 005G — sealed current-universe state hazards (NON_CANONICAL / UNMERGED)

PR #114 froze 12 candidates and evaluated all 12 once. Ten passed.

The strongest new discovery family is:

- `state_dwell_s → state_transition_h300`: **+40.2969%**, 48/48 blocks, 295 markets, 25,801 rows.
- `state_dwell_s → state_transition_h60`: **+20.7621%**, 48/48, 295 markets, 25,871 rows.

Other supported current-universe results:
- `spread_x_distance → bbo_update_h60`: **+4.9550%**, 44/48, 692 markets.
- `spread_x_distance → bbo_update_h300`: **+1.7933%**, 38/48.
- `distance_from_0_5 → bbo_update_h60`: **+1.2378%**, 48/48.
- `volatility_x_liquidity → bbo_update_h300`: **+0.7058%**, 48/48.
- `spread_x_distance → abs_h60`: **+0.1929%**, 36/48.
- `price_change_age_s → bbo_update_h300`: **+0.1477%**, 41/48.
- `price_change_age_s → bbo_update_h60`: **+0.0810%**, 45/48.

Failed:
- `distance_from_0_5 → abs_h60`: complete frozen gate not passed.
- `ofi_acceleration → bbo_update_h60`: **-0.0351%**, 16/48.

This whole branch remains **NON_CANONICAL / UNMERGED** at the frozen main snapshot.

---

## Full-life traces and supersessions

### Renewal hazard: discovery → confirmation → fresh replication → economic failure

```text
005F genuine_age renewal hazard
→ sealed HOLDOUT supported (PRE + ACTIVE)
→ 005G exact PRE_UPDATE specification frozen
→ fresh DATA-003 sealed replication +26.50%, 48/48 blocks, 658 markets
→ 005G broader state-hazard family also supports state_dwell / spread×distance
→ economic replay V1 tests WIDTH/SIZE/WAIT/REFRESH conversion
→ 0 DEV champions across 28 frozen policies
→ failure forensics: hazard predicts update/transition but not adverse-fill toxicity
→ retain as SHADOW / LIVE_DIAG context, not MAKE control
```

This is the single most important scientific chain in the repository.

### 005B movement atlas: historical positive → fresh transfer failure

```text
historical sealed HOLDOUT shows seven realised-movement positives
→ Polygon ordering correction materially changes rows/features/targets
→ all seven historical positives survive post-hoc corrected-ordering falsification
→ exact models/features/horizons frozen
→ fresh DATA-003 transfer
→ 0/7 pass; MAE worse than persistence at every horizon
→ archive as historical relationship, not current-universe signal
```

### 005C reduced-rank panel: attractive HOLDOUT → familywise demotion

```text
US_2024 15s/15s panel beats B1/B2 on pooled HOLDOUT
→ dependence-preserving 35-cell max-t review
→ adjusted p=0.8918
→ strong time concentration
→ familywise null not rejected
→ discovery comparator only
```

### 004C-C mapped cross-venue: raw positive → wrong-control falsification

```text
raw mapped PM→SIG 30s predictor positive
→ mandatory wrong-contract control stronger
→ no EXACT contract survives FDR
→ crossing economics strongly negative
→ direct mapping-specific price-discovery story rejected
→ common event-state interpretation retained
```

### EXPERIMENT-003 participant cell: nominal positive → invalidated

```text
PARTICIPANT-001@300s nominal BH rejection
→ post-result protocol audit finds baseline/placebo deviation
→ dominant identities include NegRisk exchange contracts
→ negative-control calibration problems
→ confirmatory validity invalidated
→ audit-only exploratory participant/protocol structure
```

---

## HISTORICAL POSITIVE — SUBSEQUENTLY FALSIFIED / DOWNGRADED

| Historical positive | Peak evidence | Why it is not surviving alpha |
|---|---|---|
| EXPERIMENT-003 participant 300s | nominal raw p ~0.0014, BH q ~0.035 | protocol deviation invalidates confirmatory claim; protocol-contract identities contaminate interpretation |
| 004C-C raw mapped PM→SIG 30s | raw mapped prediction positive | wrong-contract control stronger; no EXACT FDR survival; crossing economics negative |
| 005B seven realised-movement horizons | historical sealed HOLDOUT; corrected ordering still positive | fresh DATA-003 transfer **0/7** |
| 005C US joint-panel 15s | +2.22% vs B1, +6.52% vs B2 | familywise max-t adjusted p **0.8918**; strong hourly concentration |

These results are not counted in the surviving positive ledger.

---

## Economic conversion audit

| Predictive family | Conversion | Result | Reason / boundary |
|---|---|---|---|
| 005G state/renewal hazard bundle | **FAILED** | 0/28 WIDTH/SIZE/WAIT/REFRESH policies passed DEV | hazard did not rank adverse fills; favourable-fill value discarded exceeded avoided bad-fill loss |
| 004C-C mapped cross-venue | **FAILED** | executable crossing economics strongly negative | raw predictive relation was not mapping-specific leadership |
| 005I mean reversion / OFI / regimes | NOT_TESTED | MM-REPLAY-001 not complete at snapshot | no fee/fill/inventory-adjusted result yet |
| 005F renewal/jump hazard | NOT_TESTED standalone | later 005G analogous/current-universe conversion failed as a bundle | do not infer same economic sign from predictive hazard |
| PRED-006 hazard | NOT_TESTED | future CAPTURE confirmation required first | runtime model artifact also not serialized at earlier candidate-runtime review |
| 005D structural convergence/reconstruction | NOT_TESTED | no executable P&L | predictive scale tiny / reconstruction not alpha |
| 005E participant-flow secondary | NOT_TESTED | no fresh primary confirmation | follow-up only |
| 004C-B / 005A narrow family effects | NOT_TESTED | no economic conversion | weak/narrow effects |

**E4 positives: 0.**  
**E5 positives: 0.**

### 005G failure mechanism in numbers

The accepted DEV baseline net 60s markout P&L proxy was **1.6715 across 100 filled sides**. Every frozen policy family was worse:

- WIDTH_ONLY best delta: **-0.7800**;
- SIZE_ONLY: **-0.7500**;
- WAIT_ONLY: **-1.5000**;
- REFRESH_ONLY: **-0.7650**.

Forensics showed:
- BBO update AUC ~**0.675**;
- state-transition AUC ~**0.740**;
- adverse-fill AUC ~**0.456**;
- hazard vs signed maker markout Spearman ~**+0.346**;
- hazard vs absolute markout ~**+0.520**.

The state signal survived. The economic interpretation failed.

---

## Pending / not countable yet

### EXPERIMENT-005H — NON_CANONICAL / UNMERGED

C04 ARRIVAL-STATE and C05 DIRECTION-STATE reached a pre-holdout freeze. The one-shot B0 job completed **504/504** state-replay files, but the process errored before canonical holdout score tables were written. The evidence registry explicitly says `result_available=false`.

Therefore 005H contributes **no sealed positive result** to this ledger.

### MM-REPLAY-001 — NON_CANONICAL / UNMERGED

At the starting-main snapshot the strict-as-of economic replay programme did not yet have an accepted scientific result. No E4 result is inferred from a running/superseded job.

### STRUCT-SCAN-001

STRUCT-SCAN is a merged executable-certificate engine. It is infrastructure, not itself evidence that a positive structural arbitrage currently exists.

---

## Programmes reviewed with no surviving positive signal

- **EXPERIMENT-002:** accepted experiment machinery only.
- **EXPERIMENT-003:** no valid family promoted; participant nominal positive invalidated.
- **004C-A:** generic internal pairwise propagation did not survive stronger controls.
- **004C-D:** no conditional age/activity/renewal edge under its frozen design.
- **PRED-007:** no machine-speed synthetic index signal at 1s/2s/5s/10s/30s.
- **R3-FV-001 / 001M:** no confirmed independent structural point-FV challenger; persistence remains the production baseline.
- **005H:** no holdout result available.
- **MM-REPLAY-001:** no completed economic result at snapshot.

---

## Canonical interpretation

The positive evidence is not one giant alpha stack. It is several layers with very different maturity:

1. **Strongest replicated layer:** renewal / transition / movement hazard.
2. **Canonical directional layer:** 005I mean reversion and depth-normalised OFI.
3. **Narrow directional/relative-value layer:** 005D late-count structural convergence, 004C-B and 005A.
4. **Conditional participant-flow layer:** 005E secondary markout/classification, unconfirmed as a primary hypothesis.
5. **Execution layer:** still missing a validated fill-probability / toxicity mapping.
6. **Economics layer:** no positive E4/E5 result.

The JSON ledger at `data/research/positive_evidence_ledger.json` is the machine-readable source of truth for individual fields and routing.
