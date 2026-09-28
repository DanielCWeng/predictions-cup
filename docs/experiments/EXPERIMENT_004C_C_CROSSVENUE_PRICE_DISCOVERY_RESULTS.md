# EXPERIMENT-004C-C — Polymarket → SIG Cross-Venue Price Discovery

## Final report

**Final classification: INCONCLUSIVE — no promoted cross-venue edge.**

The forward run contains a positive raw 30-second EXACT forecast result, but it does not satisfy
the frozen promotion standard. The valid-OOS-contract coverage gate fails, no contract survives
FDR, the wrong-contract control is substantially stronger than the valid mapping, and the
Polymarket time-shift null is not rejected. Crossing the SIG book is strongly negative after
spread. DERIVED mappings independently do not support price discovery.

This battery therefore does not support a claim that mapped Polymarket information currently
provides robust, executable PM→SIG alpha.

No live orders were placed. PREDICTIONS_CUP_TRADING_ENABLED=false throughout the work.

## Frozen design and provenance

Branch: experiment/004c-c-crossvenue-price-discovery

Canonical base: 4e094d13b06d9e558cbc404cc4d472316d7d5bbb

Executed runner / validated code commit:
5f2e0c8a2a5dc3e388651651f077b94c006ba926

Immutable empirical-evidence commit:
239a05e9b55666530855af49ce5b00120c34a066

Kaggle:
- kernel: polyleviathan/sig-cup-exp004c-c-cross-venue-price-discovery
- executed kernel version: 8
- run ID: d8d427af680a60467d92b242
- input dataset: polyleviathan/sig-cup-exp004c-c-crossvenue-forward

Frozen hashes:
- mapping SHA-256: 9bc3d55317ace5e7e56eb0e4deaf2d36d09421b2861b7104977a96bedfc16df2
- preregistration SHA-256: 795d5c547d9a7d7c9aafc59a498efc76c87b8ffd71f7a81fa8d43923f0bafd9e
- implementation-freeze SHA-256: 85097b9356ed52dfa238be36e5e8ac504be1ae0a5eedd52aff1af40c1247d69c

The mapping universe remained frozen at 140 VERIFIED EXACT contracts and 87 VERIFIED DERIVED
contracts. NEAR mappings were excluded from the primary family.

## Clock / observability

Primary causal ordering uses local observation time rather than comparing venue clock domains.

Final forward snapshot:
2026-09-27T21:40:22.888233Z → 2026-09-27T23:51:09.607822Z

Duration: **130.78 minutes**

Observed timing:
- Polymarket panel median cadence: **5.049 s**
- SIG scalar-BBO median cadence: **11.673 s**
- median SIG quote age at mapped PM innovation: **5.815 s**
- p95 SIG quote age at mapped PM innovation: **11.115 s**

A 1-second primary target remained rejected on infrastructure grounds. The frozen primary target
was 30 seconds, with 5-second and next-SIG-update diagnostics.

The Sep-26 PM capture could not be used retrospectively because the surviving SIG database lacked
overlapping scalar BBO history. That limitation was frozen rather than rescued with sparse books
or approximate mappings.

## EXACT coverage

All 140 EXACT contracts were jointly observable and all 140 PM tokens produced book changes.

After strict as-of construction:
- evaluable pre-fold decisions: **8,612**
- OOS decisions: **3,252**
- OOS EXACT contracts: **24**
- distinct OOS SIG markets: **24**

Frozen promotion gate:
- minimum valid OOS EXACT contracts: 40 → **FAIL (24)**
- minimum valid OOS decisions: 1,000 → PASS (3,252)
- minimum distinct SIG markets: 20 → PASS (24)

The contract-coverage failure alone prevents promotion.

## EXACT raw predictive result

The full SIG+PM challenger improved 30-second absolute forecast error relative to SIG-only by:

**+0.00194855 probability points**

1,000-draw moving-block bootstrap:
- point: **+0.00194855**
- 95% lower: **+0.00155402**
- 95% upper: **+0.00211983**

The core PM challenger without optional depth fields was similar at **+0.00201318**.

So there is a raw OOS aggregate 30-second pattern. It is not enough to establish mapped PM→SIG
price discovery because the mandatory falsification controls fail.

## EXACT controls

### Relative freshness

Relative PM-versus-SIG freshness is explicitly included in the challenger. The positive raw
30-second result is not merely an omitted quote-age variable.

### Timestamp sensitivity

Conservative PM observation delays remain positive:
- +250 ms: **+0.00198183**
- +1,000 ms: **+0.00141945**

### Delayed-PM placebo

PM delayed by 30 seconds gives **−0.00153983**. Stale PM weakens and becomes harmful.

### PM circular/time-shift null

The preregistered time-shift statistic does not reject the null:
- observed equal-contract covariance: 3.7362e-05
- p-value: **0.3696**

This blocks promotion.

### Wrong-contract control

A same-category but economically invalid PM contract gives:

**+0.0118438**

This is materially larger than the valid mapped PM core result of +0.00201318. The positive
30-second result is therefore not specific to the accepted economic mapping.

### Reverse SIG → PM

The symmetric reverse-direction diagnostic produced no valid OOS holdout rows under the frozen
walk-forward requirements. No directional claim is made from it.

## Timing / decay diagnostics

Equal-contract forecast-error improvement:
- next observable SIG update: **−0.00217796**
- 5-second robustness target: **−0.00112225**
- 30-second core target: **+0.00201318**

The apparent improvement is absent or negative at the shortest observable horizons and appears
only by 30 seconds. Given the failed controls, this should not be interpreted as slow mapped-PM
transmission.

## Breadth and multiplicity

Of the 24 OOS EXACT contracts:
- 11 are positive
- 13 are negative

Across the complete 140-contract EXACT family:
**0 / 140 survive BH FDR at q ≤ 0.05.**

The minimum unadjusted p-value is about 0.034 but its q-value is 1.0. Several large positive mean
effects occur on very small OOS row counts. The evidence is concentrated rather than replicated.

## Executable SIG markout

Shadow crossing markout:
- 250 ms entry latency: **−0.0798385/share**
- 1 s entry latency: **−0.0798743/share**

Moving-block 95% intervals:
- 250 ms: **[−0.0799520, −0.0797904]**
- 1 s: **[−0.0799915, −0.0798313]**

Frozen smallest meaningful executable effect: **+0.005/share**.

The economic result is strongly negative. The dominant effect is paying the SIG spread without
enough subsequent movement to recover it.

SIG scalar BBO lacks stored top-level quantity, so no capacity claim beyond the frozen one-share
top-of-book assumption is made.

## DERIVED family

DERIVED remained a separate family and used only reviewed mutually-exclusive-margin union
identities with executable component bounds.

Results:
- frozen DERIVED contracts: 87
- contracts with evaluable rows: 54
- OOS contracts: 52
- OOS rows: 1,386
- mean AE improvement: **−1.17883e-05**
- bootstrap 95%: **[−1.66018e-05, −9.02069e-06]**
- FDR: **0 / 87**
- 250 ms markout: **−0.0799824/share**
- 1 s markout: **−0.0799844/share**

**DERIVED PRICE DISCOVERY IS NOT SUPPORTED.**

## Final promotion decision

### EXACT

**INCONCLUSIVE — NO PROMOTED CROSS-VENUE EDGE**

Promotion is blocked because:
1. 24 valid OOS EXACT contracts versus the frozen 40-contract minimum;
2. 0/140 contract hypotheses survive FDR;
3. PM circular/time-shift null p=0.3696;
4. wrong-contract PM substantially outperforms the valid mapping;
5. short-horizon diagnostics are negative; and
6. executable crossing loses roughly 8¢/share.

The correct label is not EXACT PRICE DISCOVERY SUPPORTED and not EXACT INFORMATION / NOT
EXECUTABLE because the information claim itself does not survive the full promotion controls.

### DERIVED

**DERIVED PRICE DISCOVERY NOT SUPPORTED**

DERIVED does not rescue EXACT.

## Cup interpretation

Do not promote Battery C into live order placement from this run.

The useful follow-on hypothesis is common latent/event-state information rather than direct
“Polymarket moves, therefore cross SIG” execution. Any follow-on should be a new preregistered
battery; do not reopen 004C-C by searching alternative horizons or mappings.

## Artefacts

Design:
- docs/experiments/EXPERIMENT_004C_C_CROSSVENUE_PRICE_DISCOVERY.md
- data/experiments/experiment_004c/crossvenue/preregistration.json
- data/experiments/experiment_004c/crossvenue/implementation_freeze_001.json
- data/experiments/experiment_004c/crossvenue/frozen_exact_universe.csv
- data/experiments/experiment_004c/crossvenue/frozen_derived_universe.csv
- data/experiments/experiment_004c/crossvenue/capture_limitation_001.json
- scripts/experiment_004c_c/snapshot_forward.py
- scripts/kaggle/experiment_004c_c/run.py

Final forward evidence is immutable in commit
239a05e9b55666530855af49ce5b00120c34a066 under:

data/experiments/experiment_004c/crossvenue/kaggle/final_forward_001/

The run manifest records hashes for larger Kaggle Parquet outputs not committed to Git.

## Validation

At commit 5f2e0c8a2a5dc3e388651651f077b94c006ba926:
- ruff check .: PASS
- strict mypy: PASS — 112 source files
- full pytest -q: PASS
- git diff --check: PASS