# EXPERIMENT-005F — Final Report

## Disposition

**PASS as a completed preregistered discovery/evaluation package.**

The experiment produced 17 frozen TRAIN/DEV coordinates: 14 were confirmatory-eligible and 3 were shadow-only. The first and only HOLDOUT run returned:

- **4 `HOLDOUT_SUPPORTED`**
- **10 confirmatory candidates not supported**
- **3 `SHADOW_ONLY`**

No order placement was enabled. HOLDOUT-supported means a frozen predictor improved the frozen baseline and passed the predeclared dependence/stability/falsification gates. It does **not** establish causality, executable PnL, participant skill, maker/taker economics, queue position, passive fill probability, or exact cancellation timing.

## Provenance and contamination control

- Starting commit: `ba938bedcf63f562be8b26c9502e828391123867`.
- Outcome-blind design freeze: `b5bf4cfe7482a58161523b66097910903050fc6b`.
- Canonical TRAIN/DEV freeze: 17 unique coordinates; 14 confirmatory, 3 shadow.
- Canonical fit manifest SHA-256: `b6375379b5c2bc47ac29407436edfbbb7e4810dc558b8af08f59de26823c8363`.
- HOLDOUT runner SHA-256: `73ddd72a4e7cc5d369d425fff7dc211b15f230eb9249cf5d43fc95e1ddcf2ff4`.
- Pre-HOLDOUT freeze commit: `0f2c65c08c80c734cdbb0c92e60db7656719b9dd`.
- HOLDOUT kernel: `polyleviathan/005f-holdout`, version 1.
- All 120 fit/scaler artifacts matched their frozen hashes before HOLDOUT.
- All seven committed raw HOLDOUT outputs match the sealed Kaggle outputs byte-for-byte.
- Independent recomputation of all 17 final dispositions exactly matches the runner output.
- All primary HOLDOUT labels have zero reconstructed cross-segment transitions.

## Supported HOLDOUT findings

| Regime | Target | Frozen feature | Model | Support | Relative MSE improvement | Dependence-aware evidence | Interpretation |
| --- | --- | --- | --- | --- | ---: | --- | --- |
| ACTIVE_RESULTS | 300s jump hazard | `genuine_age_s` | HGB depth 2, LR 0.03 | 14,037 obs; 61 markets; 5 events; 3 families | **16.04%** | sign-flip p=0.0115; bootstrap 95% lower=0.00423; all market/event/family LOO positive | Current age since the last genuine BBO change predicts 300s jump risk beyond price/volatility history and capture controls. |
| ACTIVE_RESULTS | 300s update hazard | `genuine_age_s` | HGB depth 2, LR 0.1 | 14,177 obs; 61 markets; 5 events; 3 families | **35.16%** | p=0.000443; bootstrap lower=0.04125; all LOO positive | Strong stale-economic-state / quote-renewal hazard signal during results. |
| PRE_ELECTION | 15s spread change | `trade_abs_impact_60` | HGB depth 3, LR 0.03 | 91,739 obs; 60 markets; 3 events; 2 families | **0.74%** | p=0.000976; bootstrap lower=5.49e-8; all LOO positive | Statistically supported but economically small; mechanism is likely slow activity/liquidity regime state rather than fresh 60s trade impulse. |
| PRE_ELECTION | 300s update hazard | `genuine_age_s` | HGB depth 3, LR 0.03 | 297,384 obs; 108 markets; 5 events; 3 families | **35.35%** | p=0.000244; bootstrap lower=0.02763; all LOO positive | Strong cross-regime replication of stale-economic-state / update-hazard prediction. |

### Capture-process controls

The supported clock candidates remain positive against the separately frozen capture-only comparators:

- ACTIVE jump hazard: capture-only minus selected+capture = **0.01374**, sign-flip p=0.0237.
- ACTIVE update hazard: **0.05293**, p=0.000648.
- PRE liquidity: **2.60e-7**, p=0.00146.
- PRE update hazard: **0.02730**, p=0.000244.

Raw capture age alone does not explain the update-hazard result. The economic-state age signal is substantially stronger than raw observation-age controls.

## Mechanism falsifications

The predictive results should not be described more narrowly than the diagnostics support.

### Genuine-age update hazard is persistent, not a short-lived lead/lag impulse

For ACTIVE update hazard:

- current feature: mean loss improvement **0.05631**
- 300s delayed feature: **0.03755**
- 1800s shifted feature: **0.02439**

For PRE update hazard:

- current feature: **0.02936**
- 300s delayed: **0.02448**
- 1800s shifted: **0.01270**

The current value is strongest, but stale versions remain predictive. The supported object is therefore best described as a **persistent economic-book staleness / renewal-hazard state**. It is not evidence of a transient cross-market propagation mechanism.

### ACTIVE jump hazard is materially more local

- current `genuine_age_s`: **0.01412**
- 300s delayed: **0.00483**
- 1800s shifted: **0.0000076**

This decay supports a more local freshness-conditioned jump-risk interpretation, while still not establishing causality.

### PRE liquidity support is real but mechanistically weak

- current `trade_abs_impact_60`: **2.298e-7**
- 300s delayed: **2.223e-7**
- 1800s shifted: **2.123e-7**

The delayed variants preserve nearly all of the primary predictive lift. The formal HOLDOUT support is valid under the frozen gate, but the feature should be treated as a **slow activity/liquidity-regime proxy**, not as evidence that the most recent 60 seconds of unsigned trade impact drives the next 15 seconds of spread movement.

## Source-version stability

The strongest freshness findings are positive on both PMXT generations:

- ACTIVE jump hazard: V1 **0.02546**, V2 **0.01098** mean loss improvement.
- ACTIVE update hazard: V1 **0.03557**, V2 **0.06208**.
- PRE update hazard: V1 **0.01605**, V2 **0.03402**.
- PRE liquidity uses PMXT_V2 HOLDOUT observations only.

This reduces, but does not eliminate, concern that the freshness results are purely a source-version artifact.

## Confirmatory candidates that failed HOLDOUT

### ACTIVE_RESULTS

- **VOLATILITY / `rv_300`**: positive observation-weighted lift, but sign-flip, block-bootstrap, and leave-family stability fail.
- **DEPTH_VOL / `snapshot_ofi_norm`**: zero incremental HOLDOUT improvement; capture comparator also zero; `CAPTURE_CONFOUNDED`.
- **MICRO_FV / `snapshot_ofi_norm`**: zero incremental HOLDOUT improvement; `CAPTURE_CONFOUNDED`.
- **PRICE_EVENT / `qbid`**: negative HOLDOUT loss improvement and unstable across groups.
- **VOL_EVENT / `qbid`**: negative HOLDOUT loss improvement and unstable.

### PRE_ELECTION

- **PRICE / `rv_60`**: negative HOLDOUT performance and negative capture-controlled increment; `CAPTURE_CONFOUNDED`.
- **VOLATILITY / `rv_300`**: positive mean, but bootstrap lower bound crosses zero.
- **DEPTH_VOL / `depth_slope_1c_5c`**: positive raw mean but sign-flip and bootstrap fail.
- **MICRO_FV / `imbalance_top`**: small positive mean but sign-flip fails.
- **VOL_EVENT / `qask`**: positive aggregate mean, but leave-market, leave-event and leave-family stability all fail.

These negatives are scientifically important: the broad atlas did **not** validate generic OFI/microprice/depth imbalance or event-time top-size alpha as robust election-market predictors.

## Shadow-only coordinates

The following remain non-promotable regardless of HOLDOUT behavior because they did not pass DEV FDR:

- ACTIVE_RESULTS clock LIQUIDITY — `trade_burstiness_60`.
- ACTIVE_RESULTS clock PRICE — `genuine_age_s`.
- PRE_ELECTION event PRICE_EVENT — `imbalance_top`.

They are retained only as descriptive generalization evidence.

## Data-quality boundary

The experiment deliberately distinguishes raw archive arrivals from genuine economic BBO changes. Historical PMXT does not provide reliable queue position, exact cancel timing, hidden liquidity, passive fill probability, or guaranteed within-millisecond ordering. Same-time ambiguity is failed closed for sequential calculations.

Accordingly, queue-dependent MATHS_LEDGER objects remain `UNTESTABLE_WITH_CURRENT_HISTORICAL_DATA`; 005F does not rename weaker proxies as those objects.

## Main scientific conclusion

The broad 005F search materially narrows the usable microstructure state space.

The strongest reusable result is **age since the last genuine economic BBO change**:

1. it predicts the probability of another BBO update over 300 seconds in both PRE_ELECTION and ACTIVE_RESULTS;
2. it survives capture-process controls, market/event/family leave-outs, high-activity removal, freshness restriction and dependence-aware bootstrap;
3. in ACTIVE_RESULTS it also predicts 300-second jump hazard;
4. its long-delay persistence means it should be modeled as a state/hazard variable, not a transient lead-lag alpha.

A second, weaker result is PRE_ELECTION unsigned trade-impact state predicting near-term spread change, but its effect is small and long-delay placebos show that the interpretation should remain regime/state based.

Everything else should remain negative or unconfirmed unless a separately preregistered follow-up tests a new mechanism.

## Canonical evidence

Raw HOLDOUT outputs live under:

`data/experiments/experiment_005f/holdout/`

The hash-bound summary is:

`data/experiments/experiment_005f/holdout_evidence_manifest.json`

The pre-HOLDOUT freeze is:

`data/experiments/experiment_005f/pre_holdout_freeze.json`

No rerun or post-HOLDOUT model/threshold/candidate change was made.
