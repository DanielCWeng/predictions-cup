# EXPERIMENT-005A — Fee-Inferred Maker/Taker & Participant Structure Discovery

**Status:** preregistered/scaffolded; empirical gate CLOSED
**Branch:** `experiment/005a-fee-role-structure-discovery`
**Base:** `69cb1924751515a495bf99556819147ad090d67d` (PR #36 / DATA-002 merged)

## Current boundary

DATA-002 is on canonical `main`, so Gate 1 is satisfied. Gate 2 is not: EXPERIMENT-004C is not fully master-frozen.
This branch therefore contains instrumentation, frozen semantics, provenance, tests and the discovery preregistration only.
No 005A empirical response matrix, null battery, Kaggle discovery run, wallet ranking, strategy backtest, threshold search
or live order placement has been executed.

`scripts/experiment_005a/preflight.py --empirical` deliberately fails until MASTER creates
`data/experiments/experiment_005a/empirical_gate.json` after the full 004C freeze.

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

After EXPERIMENT-004C is fully frozen, MASTER should add the exact 004C freeze SHA to the empirical gate record.
Only then should 005A join DATA-001 + DATA-002, run Phase-0 match-level reconciliation/coverage audits, freeze the empirical
implementation, and execute the discovery matrix. The role rules, sign convention, horizons, mechanism families, null classes
and no-alpha restrictions above are already frozen and must not be tuned after seeing 005A outcomes.
