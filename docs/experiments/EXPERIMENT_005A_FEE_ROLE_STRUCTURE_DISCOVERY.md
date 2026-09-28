# EXPERIMENT-005A — Fee-Inferred Maker/Taker & Participant Structure Discovery

**Status:** empirical execution COMPLETE; canonical result package VERIFIED
**Branch:** `experiment/005a-fee-role-structure-discovery`
**Base:** `69cb1924751515a495bf99556819147ad090d67d` (PR #36 / DATA-002 merged)

## Current boundary

Gate 1 and Gate 2 are satisfied. The canonical EXPERIMENT-004C programme freeze is
`ba938bedcf63f562be8b26c9502e828391123867`, and the 005A empirical gate pins that exact SHA.

The empirical discovery matrix and frozen null batteries are complete. The canonical fail-closed result verifier passes
across A2/A3, A4, A5, N23, N4 and N5. The terminal interpretation is **NARROW SAME-FAMILY 5s EFFECT ONLY /
NO BROAD ROLE-AWARE EDGE**. No strategy-P&L, threshold-search or live-order claim is made.

Canonical result entry points:

- `data/experiments/experiment_005a/results/FINAL_RESEARCH_REPORT.md`
- `data/experiments/experiment_005a/results/MASTER_HANDOFF_005A.md`
- `data/experiments/experiment_005a/results/canonical_verification.json`
- `data/experiments/experiment_005a/results/artifact_hashes.json`

## Role semantics

DATA-001 already held both sides of each on-chain match, but its old `source_side` could not be treated as aggressor direction.
DATA-002 adds the evidence required to distinguish the signed order owner that actively took liquidity from the passive order owner.

- Pre-fee: zero fee is uninformative. Order-role evidence is supportive semantic evidence only.
- V1: gross fee presence is not a taker label because fees were charged and refunded on both roles. Positive realised net fee plus active-order role may reach high confidence.
- V2: active-order + clean positive taker-only fee is `TAKER_HIGH_CONFIDENCE`; passive-order + scanned zero fee is `MAKER_HIGH_CONFIDENCE`.
- Fee/order-role contradictions remain `AMBIGUOUS`; they are never coerced into a side.
- Multiple fee records block a high-confidence upgrade and remain visible for audit.

Primary discovery uses high-confidence V2 rows. V1, pre-fee and supportive V2 evidence are separate strata and cannot be silently pooled for power.

## Canonical signed aggressive flow

The frozen sign convention is canonical YES probability:

| Aggressive owner action | YES pressure |
|---|---:|
| BUY YES | +1 |
| SELL YES | -1 |
| BUY NO | -1 |
| SELL NO | +1 |

This is provenance-backed, not inferred from the field name. The upstream `polymarketwhale` role utility states that
`maker_address` is the signed order owner and exchange membership in `taker_address` identifies the active row.
A custody reconciliation independently measured that maker-relative `buy` adds outcome shares and `sell` subtracts them.
DATA-002 defines `participant_address` as that same order owner.

## Scientific target

The primary cross-market question is incremental information, not raw correlation:

`B_future ~ B_history + common_event + A_price_move`

versus

`B_future ~ B_history + common_event + A_price_move + A_signed_aggressive_flow`

A flow→B result that disappears after controlling A's actual observed price movement is not a new role-aware mechanism.

Five discovery families are frozen separately: own-market subsequent markout, same-family cross-market incremental flow,
participant×role structure, maker adverse selection, and genuine liquidity-renewal response.
Horizons are fixed at 1s, 5s, 30s, 60s and 300s. Unsupported quote horizons must be reported as `UNSUPPORTED`, never interpolated.

## Provenance

All 13 files in the downloaded DATA-002 Kaggle package were verified byte-for-byte and size-for-size against the frozen manifest.
`source_commit` and `pipeline_commit` are genuinely null in DATA-002 and remain null here; no build SHA is invented.
The canonical participant-infrastructure registry is pinned by SHA-256 in `provenance.json`.

## Next action

Do not mine 005A for neighboring horizons, best pairs, wallets or thresholds.

Carry the role-enrichment infrastructure forward. Carry only the same-family PRE_ELECTION 5s signed-flow result as a
frozen replication candidate: it survives its declared null/FDR rule, but its relative OOS MSE gain is only 0.0007655%
and a winning cell does not promote a mechanism under the preregistration.

Any later trading claim requires genuinely forward/unexposed evidence and an effect large enough to survive spread,
fees and latency. The frozen 005A role rules, sign convention and null definitions must not be retuned to improve this result.
