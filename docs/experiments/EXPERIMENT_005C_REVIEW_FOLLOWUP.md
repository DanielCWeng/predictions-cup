# EXPERIMENT-005C — Post-HOLDOUT Independent Review Falsification

## Status

**Classification:** `POST_HOC_INDEPENDENT_REVIEW_FALSIFICATION`

**Final strongest-cell disposition:** **DOWNGRADE — FAMILYWISE NULL NOT REJECTED**

**Programme-level verdict:** 005C remains a useful discovery result, but the US 15s/15s model is **not promoted as a validated central candidate** after the bounded post-HOLDOUT review. It should not enter the EXPERIMENT-006 central model tournament as a promoted model. It may remain as a shadow/research comparator.

This follow-up did not reopen TRAIN/DEV search, change any frozen 005C cell, add a model, retune a baseline, or promote a failed HOLDOUT candidate.

## Frozen provenance

Original 005C scientific chain:

- scientific freeze: `ea7ed06dd47cb8240cc8a1e5dedabf00d009843f`
- original preregistration SHA-256: `b6505f8b07dd064cb1e4133a660c5d1f43864823b696539aa60e474eb9b8b6f5`
- empirical implementation: `94512d3c9b785efcbf04bcabc5c57c416e5ad524`
- pre-HOLDOUT freeze SHA-256: `20f977ffb64a56ecfe230ba45d9d0021dcc97e5218b13eb307db896d14c87933`
- frozen HOLDOUT cells: 35
- original HOLDOUT results SHA-256: `69ded820bdb336aa4cda16d432c79d51aae017c89fcdb71ed5afe344a60114a9`

Follow-up review chain:

- review preregistration SHA-256: `17fa57eed1efdabaad1f9ed51a49a30c8eba62758795e009506142fb0ad0fc39`
- disposition-rule amendment SHA-256: `d54ff2ef6331e63709bacf1a98956ab83d5f38c441e0e0a214fad289eab87531`
- Kaggle review package branch HEAD: `b9b146be710eaf7f7d4dcefc99c35f52b6a00c1d`
- original runner SHA-256 embedded in the review: `ae4468a18d1a980c5db7bb8c683f4580ba91aad903cd49ee3e7dccc2756df40f`
- follow-up runner SHA-256: `da4f51d08651ddfd034dd2669e6ecd762be11bacabd0113d2b4eca141675901a`
- successful Kaggle kernel: `polyleviathan/005c-review-falsification`, version 2
- version 1 failed before reconstruction/statistics because Kaggle mounted a stale review-code dataset version; no empirical output from version 1 was used.

All original 005C result hashes remain unchanged.

## 1. Frozen B1 comparison

The primary frozen cell remains:

- family: `US_2024`
- panel: `US_2024_event_6e91434eb80f`
- event ID: `10656`
- grid / horizon: 15s / 15s
- model: M2 Ridge
- challenger lag depth: 3
- challenger alpha: 100

The already DEV-selected B1 configuration was lag depth 3, alpha 10. It was not retuned after HOLDOUT exposure.

Primary HOLDOUT MSE:

| Comparator | MSE |
| --- | ---: |
| Frozen B1 | 0.0400373881 |
| Frozen B2 | 0.0418781974 |
| Challenger | 0.0391474332 |

The challenger improves pooled MSE by:

- **+2.2228% vs B1**
- **+6.5207% vs B2**

Versus B1, median per-target improvement is **+4.4774%** and **75%** of targets are helped.

**Answer:** yes. The 15s/15s US model beats both frozen B1 and frozen B2. The B1 falsifier does not kill the result.

The other two US event cells are weaker against B1:

- 5s / 15s M2: +0.6428% pooled vs B1, but median per-target improvement is negative.
- 5s / 30s M4: **-0.6314%** pooled vs B1 even though it remains +2.4641% vs B2.

Therefore the strongest 15s/15s cell is the only US configuration that is clearly positive against both simple target history and B2 on the pooled metric.

## 2. Correlation-aware 35-cell multiplicity

The review procedure was frozen before follow-up output:

- 10,000 one-sided draws
- absolute UTC-hour clusters
- centered cell-level loss advantages
- shared Rademacher sign for every frozen cell occupying the same absolute UTC hour
- one-way UTC-hour cluster-studentized statistic
- maximum one-sided *t* across all 35 frozen cells in each draw
- no independent-test assumption

The bootstrap correlation matrix has an eigenvalue participation-ratio effective test count of **20.52**, confirming substantial but incomplete dependence among the 35 cells.

For the primary 15s/15s US cell:

- observed pooled improvement vs B2: **+6.5207%**
- observed UTC-hour cluster *t*: **1.4696**
- unadjusted one-sided block-wild p: **0.0173**
- 35-cell max-*t* adjusted p: **0.8918**
- global max-null 95th percentile: **2.3448**
- global max-null 99th percentile: **2.6222**

**Result:** the primary result does **not** survive the familywise challenge.

This is the formal downgrade trigger under the frozen review rule. The result was interesting cell-wise, but 005C exposed 35 DEV-selected cells to HOLDOUT and the strongest cell is not exceptional relative to the dependence-preserving global maximum null.

## 3. Temporal dependence sensitivity

The primary 15s/15s cell has 268 valid decision times.

| Requested block | Rows | Effective block equivalents | Mean loss advantage | 95% CI | Status |
| --- | ---: | ---: | ---: | --- | --- |
| 5 min | 20 | 13.40 | 0.0044419 | [0.0000736, 0.0082054] | stable enough |
| 15 min | 60 | 4.47 | 0.0044419 | **[-0.0002491, 0.0050484]** | stable enough |
| 30 min | 120 | 2.23 | 0.0044419 | not reported | too few block-equivalents |
| 60 min | 240 | 1.12 | 0.0044419 | not reported | too few block-equivalents |
| 120 min | 480 | 0.56 | 0.0044419 | not reported | insufficient duration |

The 5-minute dependence assumption remains positive. At the first substantially more conservative stable setting, **15 minutes, the lower CI crosses zero**.

Therefore the effect is also **dependence-sensitive**. Longer blocks cannot support stable confidence intervals because this particular HOLDOUT does not contain enough effective independent block-equivalents.

The supporting US 5s/30s M4 cell is even weaker: its 5-minute CI already crosses zero. The US 5s/15s cell does not have four effective 5-minute blocks.

## 4. Exact event semantics and calendar placement

Canonical market metadata does **not** expose an event title for event ID `10656`: `parent_event_slug` is null for all five selected markets and no event-title field is populated. Therefore no exact event title is invented here.

Its economic meaning is nevertheless explicit from the five canonical market questions. It is a joint-outcome family decomposing the 2024 US presidential result by **popular-vote winning party × Presidency winning party**, plus a third-party outcome:

1. `0xab0132...a288` — “Will a 3rd party candidate win the popular vote or the Presidency?” — source-only in the primary target set.
2. `0x136e99...0182` — “Will a Republican win the popular vote and a Democrat win the Presidency?”
3. `0xafde9e...8abd` — “Will a Democrat win the popular vote and the Presidency?”
4. `0xc53c00...02d6` — “Will a Democrat win the popular vote and a Republican win the Presidency?”
5. `0x2010ff...2ab` — “Will a Republican win the popular vote and the Presidency?”

These are **direct economic relatives in the same joint-outcome partition**. They are not state markets, candidate-specific markets, chamber markets, or unrelated common-event markets.

Economic fill coverage for the five markets starts on May 15, 2024 and runs into November 6–7, 2024.

For the primary 15-second grid:

- TRAIN decisions: **2024-05-16 02:09:00 UTC → 2024-10-23 15:33:00 UTC**
- TRAIN/DEV boundary: **2024-10-23 16:57:30 UTC**
- DEV decisions: **2024-10-23 16:59:30 UTC → 2024-11-04 12:18:15 UTC**
- DEV/HOLDOUT boundary: **2024-11-04 12:20:45 UTC**
- HOLDOUT decisions: **2024-11-04 12:48:15 UTC → 2024-11-06 08:07:30 UTC**
- common valid-loss times used by the primary comparison: **2024-11-04 13:18:00 UTC → 2024-11-06 07:53:45 UTC**

The US general election date was November 5, 2024. The HOLDOUT therefore spans **election eve, Election Day, election night, and the early post-midnight period**, not a weeks-before-election regime.

A precise descriptive label is:

> **ELECTION-EVE / ELECTION-NIGHT JOINT OUTCOME PANEL**

It should not be described as a generic PRE_ELECTION panel.

## 5. TRAIN source-target structure

The coefficient map uses only the already fitted primary TRAIN M2 model. Cross-market relationships are ranked by absolute standardized-return coefficient, not by HOLDOUT performance.

All cross-market sources in this panel are direct economic relatives because each represents another mutually exclusive/related popular-vote × Presidency outcome.

### Republican popular vote + Democrat Presidency target

Own-return coefficients at lags 0/1/2 are approximately:

- -0.00541
- -0.00737
- +0.00014

Own-return L2 norm: **0.00914**.

Largest cross-return coefficients:

- third-party outcome, lag 0: **-0.00671**
- third-party outcome, lag 1: **+0.00380**
- Democrat popular vote + Democrat Presidency, lag 1: **+0.00250**

### Democrat popular vote + Democrat Presidency target

Own-return L2 norm: **0.01075**, the largest own-history norm among the four targets.

Largest cross-return coefficients all come from the Democrat-popular-vote + Republican-Presidency alternative:

- lag 2: **-0.00196**
- lag 0: **+0.00137**
- lag 1: **-0.00124**

### Democrat popular vote + Republican Presidency target

Own-return L2 norm: **0.00715**.

Largest cross-return coefficients:

- third-party outcome, lag 0: **-0.00358**
- Republican popular vote + Democrat Presidency, lag 2: **-0.00156**
- Democrat popular vote + Democrat Presidency, lag 2: **+0.00135**

### Republican popular vote + Republican Presidency target

Own-return L2 norm: **0.00544**.

Largest cross-return coefficients:

- Democrat popular vote + Republican Presidency, lag 1: **+0.00168**
- Republican popular vote + Democrat Presidency, lag 1: **+0.00089**
- Democrat popular vote + Republican Presidency, lag 2: **+0.00087**

The coefficient signs vary by lag and target. They are descriptive conditional Ridge coefficients, not causal effects and not a literal no-arbitrage relationship. The useful conclusion is that the model is combining own-state history with several directly related joint outcomes rather than one unrelated dominant market.

## 6. Temporal concentration

The fixed 1-hour analysis contains:

- **34 active UTC-hour blocks**
- **21 positive blocks**
- cumulative loss advantage: **1.54015**

Concentration of positive advantage:

- top 1 hour: **63.28%**
- top 3 hours: **92.75%**
- top 5 hours: **99.28%**

The five strongest positive UTC hours are all on **November 6**, led by 06:00, 04:00, 03:00, 07:00 and 02:00 UTC.

Using the absolute midpoint of the valid HOLDOUT:

- first half pooled improvement vs B2: **+2.45%**
- second half: **+6.54%**

Removing the largest positive hour leaves **+2.51%** pooled improvement. Removing the top three positive hours still leaves **+1.32%**.

So the gain is **highly temporally concentrated around election-night / post-midnight trading**, although it is not entirely produced by one or three individual hours.

If multiplicity had survived, the frozen concentration rule would have labelled this `RETAIN — TEMPORALLY CONCENTRATED`, because the strongest hour exceeds 50% of total positive advantage. Multiplicity fails first, so the formal disposition remains the familywise downgrade.

## 7. Cross-sectional versus time-series interpretation

The frozen primary metrics are:

- cross-sectional IC: **-0.0221**
- median per-target time-series IC: **+0.2033**
- sign accuracy: **57.72%**

There is no positive evidence that the model can rank which of the four targets will move more at a given timestamp. Cross-sectional IC is approximately zero and slightly negative.

The stronger evidence is **time-series per-market prediction**: conditional on each target's own history and the related panel state, the model improves the target's future movement forecast through time.

The final description should therefore avoid phrases implying good cross-sectional ranking.

## 8. Explicit review answers

1. **Does the US 15s/15s model beat B1 as well as B2?**  
   **Yes.** +2.22% pooled MSE vs frozen B1 and +6.52% vs B2.

2. **Does it survive a correlation-aware 35-cell multiplicity challenge?**  
   **No.** Unadjusted p ≈ 0.0173; max-*t* adjusted p ≈ **0.8918**.

3. **Does it survive longer temporal bootstrap blocks?**  
   **No under the frozen criterion.** Five-minute CI is positive; the 15-minute stable CI crosses zero. 30–120 minute inference is too data-limited for stable CIs.

4. **Is the gain temporally distributed?**  
   **Not broadly.** It is highly concentrated around November 6 UTC: top hour 63.3% of positive advantage, top three 92.8%. However, removing those hours still leaves a smaller positive pooled improvement.

5. **What exactly is US event 10656?**  
   Canonical metadata provides no populated event title. Economically it is the joint **popular-vote winning party × Presidency winning party** outcome family, with a third-party case.

6. **Which markets constitute the successful panel?**  
   The five market questions listed in section 4: third-party outcome and the four Republican/Democrat popular-vote × Presidency combinations.

7. **What economic relationships appear in the TRAIN coefficient matrix?**  
   Direct economic relatives from the same joint-outcome partition. Own-history is material; multiple alternative joint outcomes also enter with signed, lagged conditional coefficients. No state/candidate/chamber relationship is involved.

8. **Is the effect primarily time-series or cross-sectional?**  
   **Time-series.** Median per-target IC is +0.203; cross-sectional IC is -0.022.

9. **Should the model enter EXPERIMENT-006 central model tournament?**  
   **No as a promoted central candidate.** The 35-cell familywise null is not rejected, and the primary result is dependence-sensitive at 15-minute blocks. It can remain as a shadow/research comparator or falsified benchmark.

10. **What claims must remain prohibited?**  
    Do not claim a universal or cross-family joint factor; do not call this a validated standalone alpha; do not claim cross-sectional ranking skill; do not claim independence from own-history; do not claim execution/P&L profitability; do not describe it as a broad pre-election effect; and do not treat secondary failed HOLDOUT cells as rescued by this review.

## Final disposition

### Strongest US event result

> **DOWNGRADE — FAMILYWISE NULL NOT REJECTED**

The cell remains descriptively interesting: it beats both B1 and B2, its raw cell-wise evidence is nontrivial, and the TRAIN relationships are economically coherent. But once the already exposed family of 35 frozen HOLDOUT tests is treated as the relevant multiplicity universe, the result is not familywise exceptional.

### 005C programme verdict

005C succeeded as **discovery** but failed the final independent-review bar for **promotion**.

The programme should carry forward the mechanism as a research hypothesis—joint short-horizon state among directly related election-night outcomes—without carrying forward the 15s/15s model as validated central alpha. A future untouched analogue could test the mechanism prospectively; this exposed HOLDOUT should not be reused to upgrade the claim.