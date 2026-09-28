# EXPERIMENT-005A — Final Research Report

**Experiment:** Fee-Inferred Maker/Taker & Participant Structure Discovery  
**Disposition:** **NARROW SAME-FAMILY 5s EFFECT ONLY / NO BROAD ROLE-AWARE EDGE**  
**Executable-alpha claim:** **NO**  
**Canonical 004C freeze:** `ba938bedcf63f562be8b26c9502e828391123867`  
**Observed implementation:** `a0217eed5f775735d5ad01edf3141963be7b1787`

## Executive result

DATA-002 solved the measurement problem well: role annotation is coherent enough to use. It did **not**
unlock a broad predictive mechanism across role-aware order flow, participant identity, maker adverse
selection, or liquidity renewal.

The canonical package contains one within-family BH rejection:

- **A3 same-family cross-market flow, PRE_ELECTION, 5s**
- validation rows: **92,007**
- events: **3**
- election families: **2**
- OOS relative MSE gain: **7.6555e-06** = **0.0007655%**
- circular null p: **0.005**
- 300s block null p: **0.002**
- conservative intersection p: **0.005**
- within-family BH q: **0.050**

This is statistically narrow and economically tiny. The preregistration explicitly states that a winning
cell does not by itself promote a mechanism. It is therefore a **candidate feature for later replication**,
not an executable alpha claim.

## A1 — role identification quality

Across the ten frozen PRE_ELECTION / ACTIVE_RESULTS windows:

- exact DATA-001 ↔ DATA-002 fill joins: **374,891 / 374,891 (100.000%)**
- duplicate right-side join keys: **0**
- outcome disagreements: **0**
- price disagreements: **0**
- known infrastructure owners in the participant slot: **0**

Across the six post-V2 windows:

- joined fills: **94,962**
- active/taker + positive realised fee: **35,049**
- active/taker + no positive fee: **270**
- passive + positive realised fee: **235**
- passive + no positive fee: **59,408**
- clean role/fee agreement: **99.468%**
- frozen high-confidence maker+taker coverage: **99.468%**

The 235 passive-positive-fee contradictions remain AMBIGUOUS. A strict complementary-pair diagnostic
explains 119/235 as consistent with adjacent-log fee attribution landing on the passive member of the
pair, but 005A does not silently repair them.

**A1 disposition: STRONG ROLE-ANNOTATION QUALITY POST-V2.**

## Response mechanisms

| Family | Frozen cells | BH rejections | Interpretation |
|---|---:|---:|---|
| A2 own-market signed taker flow | 10 | 0 | No supported incremental own-market response. |
| A3 same-family flow | 10 | 1 | PRE 5s survives at q=0.05, but effect size is extremely small. |
| A3 semantic pairs | 10 | 0 | No supported semantic-pair response. |
| A4 participant × taker role | 10 | 0 | Participant identity/history adds no supported predictive increment. |
| A5 maker adverse selection | 10 | 0 | No supported maker-state mechanism. |
| A5 primary liquidity response | 30 | 0 | No supported genuine-liquidity response. |
| A5 capture placebos | 30 | 0 | No placebo family promotes. |

The canonical verifier reports **1 primary BH rejection** across the package. Multiplicity is interpreted
within the predeclared mechanism families; this count is descriptive, not a global familywise p-value.

## A2 — own-market flow

A2 has **0/10** BH rejections. Its lowest q is **0.15**. Some PRE cells have positive OOS loss gain, but
none survives the frozen within-family multiplicity rule.

This does not support aggressive role-aware flow as a robust standalone predictor of its own market's
subsequent midpoint after the frozen price/activity/liquidity/common-event controls.

## A3 — cross-market flow

### Same-family

One cell survives: PRE_ELECTION at 5s. It is incremental to the source market's actual observed price move
and the frozen target/source/common-event controls, so it is not merely a restatement of contemporaneous
source price movement.

However:

- relative validation MSE improvement is only **0.0007655%**;
- no neighboring PRE horizon promotes;
- no ACTIVE_RESULTS horizon promotes;
- the experiment has no globally pristine historical election holdout;
- the preregistration forbids promoting a mechanism from a winning cell.

The right interpretation is therefore **narrow short-horizon same-family structure worth replication**.

### Semantic pairs

A3 semantic pairs have **0/10** BH rejections. No semantic relationship class is promoted.

## A4 — participant role structure

A4 has **0/10** BH rejections. Nine cells have non-positive OOS gain. The only positive cell is PRE 1s,
with an identity-null p of **0.345** and BH q of **1.0**.

The participant history construction is leakage-safe and past-only, with 8,147 unique participants in the
history audit. The null result is therefore informative: participant identity/history, as operationalized
here, does not add robust signal beyond the role-aware market state.

**A4 disposition: NO PARTICIPANT-IDENTITY EDGE.**

## A5 — maker adverse selection

A5 maker has **0/10** BH rejections.

The largest observed relative gains occur in ACTIVE_RESULTS at longer horizons (for example 300s:
**0.3351%** relative MSE gain), but the frozen nulls do not support them. The ACTIVE 300s conservative
intersection p is **0.146**; within-family q values do not approach 5%.

Some PRE cells have attractive circular-null p-values but lack lossless block support and therefore fail
closed exactly as preregistered.

**A5 maker disposition: NO SUPPORTED MAKER-ADVERSE-SELECTION EDGE.**

## A5 — liquidity response and capture controls

Primary liquidity response has **0/30** BH rejections.

The largest observed relative primary gain is ACTIVE_RESULTS 300s future total-depth change
(**0.2806%**), but its conservative intersection p is **0.092** and q is **0.72**.

Two PRE cells produce unadjusted intersection p-values below 0.05:

- 60s future total-depth change: p **0.033**, q **0.69**
- 5s future spread change: p **0.046**, q **0.69**

Neither survives the frozen multiplicity rule. Capture-placebo responses have **0/30** BH rejections.

**A5 liquidity disposition: NO SUPPORTED LIQUIDITY-RENEWAL EDGE.**

## What 005A establishes

1. **Role labels are trustworthy enough to use.** The data-engineering premise survived.
2. **Role-aware flow is not broadly predictive under this linear incremental design.**
3. **Participant identity does not rescue the signal.**
4. **Maker and liquidity mechanisms do not survive realistic null/FDR controls.**
5. **There is one narrow same-family PRE 5s effect worth carrying forward as a candidate feature.**
6. **There is no 005A strategy/P&L result and no basis here for live order placement.**

## Recommended carry-forward

Do not search neighboring horizons, pairs, wallets, or thresholds inside 005A.

For a later experiment, carry only the frozen statement:

> Same-family source signed aggressive flow may contain a very small amount of incremental 5-second
> PRE_ELECTION information beyond the source's observed price move.

Replication should use genuinely later/unexposed data or a forward paper-trading window, preserve the
same sign convention and role-confidence policy, and require an **economic** improvement large enough to
matter after spread/fees/latency before considering execution.

The stronger lesson for the cup is that the new role variable is reliable infrastructure, but it is not by
itself the missing broad alpha source.
