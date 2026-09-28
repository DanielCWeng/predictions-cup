# EXPERIMENT-004C-C — MASTER Handoff

**Classification: INCONCLUSIVE — no promoted cross-venue edge.**

- Branch: experiment/004c-c-crossvenue-price-discovery
- Canonical base: 4e094d13b06d9e558cbc404cc4d472316d7d5bbb
- Executed/validated runner commit: 5f2e0c8a2a5dc3e388651651f077b94c006ba926
- Immutable empirical-evidence commit: 239a05e9b55666530855af49ce5b00120c34a066
- Kaggle run: kernel v8, run ID d8d427af680a60467d92b242
- Shadow only; no live orders; trading remained disabled.

## 1. Does PM add information beyond SIG?

**Raw 30-second aggregate: yes; robust mapped price discovery: not established.**

EXACT improves equal-contract OOS absolute error by **+0.00194855** over SIG-only, with a
1,000-draw moving-block 95% interval of **[+0.00155402, +0.00211983]**.

It cannot be promoted. Only **24** EXACT contracts have OOS holdout predictions versus the frozen
**40-contract** minimum, **0/140** survive FDR, wrong-contract PM is much stronger than the valid
mapping, and the PM time-shift null is not rejected.

## 2. Does it survive relative-freshness controls?

**The raw result does; the full falsification battery does not.**

Relative freshness is explicit in the model. The core result is **+0.00201318**. Conservative
timestamp delays remain positive:
- +250 ms: **+0.00198183**
- +1 s: **+0.00141945**

But:
- 30 s delayed PM: **−0.00153983**
- wrong-contract PM: **+0.0118438**
- PM time-shift null p: **0.3696**

The wrong-contract and time-shift controls block the mapping-specific claim.

## 3. How quickly does any advantage decay?

It does not look like fast PM→SIG transmission:
- next SIG update: **−0.00217796**
- 5 s: **−0.00112225**
- 30 s core: **+0.00201318**

The apparent improvement emerges only at 30 s. Given the failed controls, do not interpret this as
causal mapped-PM lead.

Reverse SIG→PM produced no valid OOS holdout rows.

## 4. Does it survive executable bid/ask crossing and costs?

**No.**

EXACT shadow markout:
- 250 ms: **−0.0798385/share**
- 1 s: **−0.0798743/share**

Both intervals are entirely negative and far below the frozen +0.005/share meaningful-effect
threshold.

SIG broad BBO lacks stored top-level size, so no capacity claim beyond one-share BBO shadow
execution is made.

## 5. EXACT and DERIVED conclusions

### EXACT

**INCONCLUSIVE — NO PROMOTED CROSS-VENUE EDGE.**

Raw forecast improvement exists, but promotion fails frozen OOS-contract coverage and mandatory
negative controls. It is also non-executable.

### DERIVED

**DERIVED PRICE DISCOVERY NOT SUPPORTED.**

- OOS contracts: 52
- OOS rows: 1,386
- mean AE improvement: **−1.17883e-05**
- bootstrap 95%: **[−1.66018e-05, −9.02069e-06]**
- FDR: **0/87**
- 250 ms markout: **−0.0799824/share**

DERIVED does not rescue EXACT.

## 6. Broad or concentrated?

**Concentrated / not replicated.**

EXACT has 24 OOS contracts: 11 positive and 13 negative. No contract survives FDR. Several large
positive means have very small OOS row counts.

## 7. Timestamp / capture limitations

- causal clock: local observation time
- forward window: **130.78 min**
- PM panel median cadence: **5.049 s**
- SIG BBO median cadence: **11.673 s**
- median SIG age at PM innovation: **5.815 s**
- Sep-26 retrospective overlap unavailable due missing surviving SIG scalar-BBO history
- reverse-direction OOS diagnostic unavailable
- SIG scalar BBO has no stored top-level size
- evidence is PRE_ELECTION only; no ACTIVE_RESULTS inference

## 8. Exact branch, commits and paths

Branch:
experiment/004c-c-crossvenue-price-discovery

Validated runner commit:
5f2e0c8a2a5dc3e388651651f077b94c006ba926

Immutable evidence commit:
239a05e9b55666530855af49ce5b00120c34a066

Design paths:
- docs/experiments/EXPERIMENT_004C_C_CROSSVENUE_PRICE_DISCOVERY.md
- data/experiments/experiment_004c/crossvenue/preregistration.json
- data/experiments/experiment_004c/crossvenue/implementation_freeze_001.json
- data/experiments/experiment_004c/crossvenue/frozen_exact_universe.csv
- data/experiments/experiment_004c/crossvenue/frozen_derived_universe.csv

Final evidence paths:
- data/experiments/experiment_004c/crossvenue/forward_snapshot_final_manifest.json
- data/experiments/experiment_004c/crossvenue/kaggle/final_forward_001/result_summary.json
- data/experiments/experiment_004c/crossvenue/kaggle/final_forward_001/phase0_audit.json
- data/experiments/experiment_004c/crossvenue/kaggle/final_forward_001/exact_controls.json
- data/experiments/experiment_004c/crossvenue/kaggle/final_forward_001/exact_bootstrap.json
- data/experiments/experiment_004c/crossvenue/kaggle/final_forward_001/exact_contract_inference.csv
- data/experiments/experiment_004c/crossvenue/kaggle/final_forward_001/derived_bootstrap.json
- data/experiments/experiment_004c/crossvenue/kaggle/final_forward_001/derived_contract_inference.csv
- data/experiments/experiment_004c/crossvenue/kaggle/final_forward_001/run_manifest.json

Runner:
scripts/kaggle/experiment_004c_c/run.py

Full report:
docs/experiments/EXPERIMENT_004C_C_CROSSVENUE_PRICE_DISCOVERY_RESULTS.md

## Validation

At 5f2e0c8a2a5dc3e388651651f077b94c006ba926:
- ruff PASS
- strict mypy PASS
- full pytest PASS
- git diff-check PASS

## MASTER disposition

Do **not** promote Battery C into live order placement from this run.

The useful next hypothesis is common latent/event-state information rather than direct mapped
PM→SIG crossing. Any follow-on should be a new preregistered battery, not a rescue search inside
004C-C.