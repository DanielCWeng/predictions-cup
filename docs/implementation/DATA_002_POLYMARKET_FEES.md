# DATA-002 — Polymarket Fee / Maker-Taker Evidence

**Status:** MERGED / ACCEPTED — PR #36
**Branch:** `data/002-polymarket-fees`
**Depends on:** DATA-001 (`docs/implementation/DATA_001_HISTORICAL_REPLAY_CORPUS.md`) for join keys only — see
[Relationship to DATA-001](#relationship-to-data-001). Neither dataset is mutated by the other.

## Purpose

DATA-002 is a frozen auxiliary dataset of on-chain fee, refund and maker-rebate evidence for every
scoped fill across five election families. It exists to let participant/order-flow research
distinguish informed aggressive taker flow, passive liquidity provision and persistent rebated
market making, using raw fee evidence rather than a hard-coded maker/taker label. It does not
compute or assert a taker/maker classification itself.

**Fill-complete, not merely fee-transfer-complete.** Every scoped fill has a row, including fills
with zero matched fee (`fee_evidence` explains why). A row missing here would silently bias any
`P(taker | fee evidence)` vs `P(maker | no fee evidence)` estimate. This was checked directly:
`fees_<FAMILY>.parquet` row counts match `fills_total` in `data_002_quality.json`'s source stats
exactly, for all five families, so no separate `fill_fee_labels_<FAMILY>.parquet` export was
needed.

## Families

Scope: every fill of `US_2024`, `COL_2026`, `PER_2026`, `HUN_2026`, `CAN_2025` — 1,747 markets,
3,646 tokens, 18,317,515 fills total, identical to the `sisterreq_fills_20260926` population used
by DATA-001. No wallet pre-filter. Source: the internal trades/ + custody/ daily Parquet lake only
(no RPC, no re-scrape).

| Family | Rows | First | Last | Unique participants | Fee charged $ | Fee refunded $ |
|---|---|---|---|---|---|---|
| CAN_2025 | 854,625 | 2024-12-30 | 2025-08-21 | 73,202 | 0.00 | 0.00 |
| COL_2026 | 693,573 | 2025-07-29 | 2026-07-15 | 35,728 | 93,833.00 | 3,029.71 |
| HUN_2026 | 840,134 | 2025-07-25 | 2026-05-09 | 53,938 | 14,671.88 | 12,083.21 |
| PER_2026 | 1,212,643 | 2025-12-16 | 2026-07-30 | 54,571 | 198,527.41 | 0.00 |
| US_2024 | 14,716,540 | 2023-01-14 | 2024-12-18 | 306,893 | 0.00 | 0.00 |

CAN_2025 and US_2024 are wholly or mostly pre-fee-era; their zero fee totals are a regime fact, not
missing data (see [Pre-fee era](#pre-fee-era-and-regime-changes) below).

## Fee regimes

Three regime changes govern how a zero-fee or fee-present row should be read, all recorded
explicitly in `data/manifests/fees/data_002_fee_regimes.csv` (`regime_id`, effective dates, scope,
taker fee, maker rebate, carrier, fee contract, evidence) so no downstream consumer infers
"no fee = maker" across a boundary where that inference is invalid:

- **Before 2026-01-05:** no trading fees anywhere. `OrderFilled.fee = 0` carries no maker/taker
  information at all.
- **2026-01-05 → 2026-04-27 (CLOB V1, phased rollout; broad rollout 2026-03-30, Geopolitics/World
  exempt):** the exchange charges a *gross* fee into a FeeModule on each order fill, then refunds
  part or all of it to the order owner in the same transaction. Realised fee =
  charge − refund (`fee_net_usd_equiv`). Makers were charged too and mostly refunded — `fee > 0` on
  a maker order in V1 is normal, not an attribution error.
- **2026-04-28 onward (CLOB V2):** the realised fee is a single collateral transfer,
  exchange → fee receiver `0x115f48dc...`, on the **taker** order only. No refunds. Maker rebates
  are paid separately and daily by distributor `0x3a9418b2...`, not per fill.
- **2026-07-10:** Sports fee 0.03 → 0.05, maker rebate 25% → 15% (context only; not material to
  these five election families).

**Confidence, V2 era only:** 98.3% of taker-order fills (242,052 / 246,261) carry a fee leg, vs
0.43% of maker-order fills (1,738 / 400,909). This is strong evidence, not ground truth — it ships
as the raw `fee_evidence` / `order_is_match_taker_order` columns so an experiment can preregister
its own classification rule rather than consuming a baked-in `is_taker` label. In V1 the gross
charge is **not** a valid taker signal; only `fee_net_usd_equiv` is.

### Pre-fee era and regime changes

`fee_evidence` distinguishes two fundamentally different pieces of evidence that a naive zero-fee
check would conflate:

- `custody_not_scanned_pre_fee_era` — the day predates fee introduction and custody logs for it
  were not fully scanned (scanned on every day ≥ 2025-12-01 and the 15th of each earlier month: 24
  probe days, 359,937 scoped fills, 0 fee legs found). This is an absence of evidence, not evidence
  of absence, and must **not** be translated into `maker`.
- `no_fee_leg_observed` (`scanned_and_no_fee_found` in the original spec) — the day's custody was
  scanned and no matching charge/refund leg was found. This is a real zero-fee observation.

US_2024 and CAN_2025 keep this distinction throughout; their near-total pre-fee-era coverage is
why both show $0.00 fee totals above without being flattened into a maker label.

## Attribution

- **Charge legs:** transfer into `0x115f48dc2a731aa16251c6d6e1befc42f92accc9` (V2 receiver),
  `0xe3f18acc55091e2c48d883fc8c8413319d4ab7b0` (V1 CTF FeeModule) or
  `0xb768891e3130f6df18214ac804d4db76c2c37730` (NegRisk FeeModule); asset is USDC collateral or the
  outcome token itself (V1 BUY fees were taken in shares).
- **Refund legs:** transfer out of a FeeModule to the order owner.
- **Rebates:** transfers out of distributor `0x3a9418b2651c8164db5ebc56f12008137865e0f7`.
- **Receipt-verified matching:** a charge leg sits exactly 2 logs from its `OrderFilled` in the same
  transaction (V1: before it; V2: after it), and a token-denominated fee must be in the fill's
  token. When two fills are 2 logs away, the taker-order fill is chosen. Refunds go to the fill in
  the transaction whose owner is the recipient, same token, nearest preceding leg.
- **Conservation:** attributed + unattributed legs = all scoped legs exactly — 265,067 + 594 =
  265,661 (`data_002_quality.json.reconciliation_check.status == "PASS"`). The 594 unattributed
  legs (0.22%) ship raw in `unmatched/unattributed_fee_legs.parquet`, not dropped.
- **Ambiguity is surfaced, not resolved:** `attribution_rules` names the rule(s) used;
  `*_AMBIGUOUS` marks a non-unique choice, and `flag_attribution_ambiguous` /
  `flag_multiple_fee_records` / `flag_fee_on_fee_disabled_market` / `flag_fee_on_non_taker_order`
  are kept as columns rather than filtered out.

## Corpus layout and contract

```text
sig-cup-data-002-polymarket-fees/schema_version=1/
  data_002_manifest.json
  data_002_quality.json
  matched_fills/
    fees_PER_2026.parquet
    fees_COL_2026.parquet
    fees_HUN_2026.parquet
    fees_US_2024.parquet
    fees_CAN_2025.parquet
  unmatched/
    unattributed_fee_legs.parquet
  rebates/
    maker_rebate_payouts_scoped_wallets.parquet
  regimes/
    polymarket_fee_regimes.csv
    polymarket_fee_regimes.parquet
  docs/
    POLYMARKET_FEE_DATA_NOTES.md
    DATA_DICTIONARY.md
    STATS.md
    STATS.json
```

The corpus itself (Parquet, ~705 MB) lives outside Git, on Kaggle (see
[Kaggle hosting](#kaggle-hosting) below) — GitHub holds only the manifests, hashes and quality
evidence, the same split DATA-001 uses for its corpus.

### `fees_<FAMILY>.parquet` — one row per `OrderFilled` (order fill)

Grain: each matched trade emits one `OrderFilled` per maker order plus one for the taker order
(whose `taker_address` is the exchange contract), so every participant in a match has its own row.

| Column | Meaning |
|---|---|
| `family`, `event_id`, `market_id`, `outcome_side` | election inventory identity (`outcome_side` YES/NO/OTHER) |
| `condition_id`, `token_id` | market condition and outcome token — the DATA-001 join key |
| `day`, `timestamp` | UTC day; block timestamp (unix s) |
| `tx_hash`, `log_index` | the `OrderFilled` event id (unique with `token_id`) — the other DATA-001 join key |
| `participant_address` | order owner (= source `maker_address`) |
| `counterparty_address` | source `taker_address` (exchange contract when this is the taker order) |
| `order_is_match_taker_order` | true when the counterparty is an exchange contract, i.e. this order was the active/taker order |
| `exchange_if_taker_is_exchange` | `V1_CTF` / `V1_NegRisk` / `V2_CTF` / `V2_NegRisk` / `ComboV3` |
| `participant_side`, `price`, `size_shares`, `value_usd` | source side, price, shares, USDC notional of this order |
| `maker_address`, `taker_address` | source fields kept verbatim for comparison |
| `fee_evidence` | `custody_not_scanned_pre_fee_era` / `no_fee_leg_observed` / `fee_charged` / `fee_charged_partly_refunded` / `fee_charged_fully_refunded` |
| `fee_charged_usdc`, `fee_charged_shares` | gross fee legs attributed to this fill |
| `fee_refunded_usdc`, `fee_refunded_shares` | FeeModule refunds attributed to this fill |
| `fee_charged_usd_equiv`, `fee_refunded_usd_equiv`, `fee_net_usd_equiv` | USDC + shares × fill price (derived) |
| `n_charge_legs`, `n_refund_legs` | counts of attributed legs |
| `fee_asset` | `USDC_collateral` and/or `outcome_token` |
| `fee_contract` | receiving / refunding fee contract(s) |
| `fee_sent_by_exchange` | a leg was sent by an exchange contract (vs. directly by a wallet) |
| `fee_leg_refs` | `tx_hash:log_index` of every attributed custody leg (raw evidence) |
| `attribution_rules` | rule(s) used; `*_AMBIGUOUS` marks a non-unique choice |
| `flag_multiple_fee_records` | >1 charge leg on this fill |
| `flag_attribution_ambiguous` | any attribution on this fill was not unique |
| `flag_fee_on_fee_disabled_market` | fee charged while the snapshot says `fees_enabled=false` |
| `flag_fee_on_non_taker_order` | fee charged on a maker order (normal in V1, rare in V2) |
| `fee_type`, `fee_rate`, `fee_taker_only`, `fee_rebate_rate`, `fees_enabled`, `fee_source`, `fee_category` | **current** Gamma market settings (snapshot, not historical) |
| `custody_scan` | `true` / `skipped_pre_fee_era` / `skipped_no_scoped_fills` for that day |

Nothing here is collapsed into a single `is_taker=true/false` column, by design — the underlying
evidence ships so an experiment can preregister its own classification rule against
`order_is_match_taker_order`, `fee_evidence` and the regime file, rather than inheriting one.

### `rebates/maker_rebate_payouts_scoped_wallets.parquet`

`wallet, utc_date, rebate_usdc, tx_hash, log_index, block_number` — every distributor payout to a
wallet that appears in these fills: 97,124 transfers, $2,230,607.84, 2026-04-29 → 2026-06-25 (direct
rebate payouts are not observed in custody after 06-25). This is **wallet-day evidence**, not a
fill-level label — no specific maker fill is attributed a rebate. Candidate downstream features:
`wallet_received_rebate_that_day`, `wallet_rebate_usd_that_day`, `historical_rebate_frequency`,
`historical_rebate_amount`. The distributor also pays non-participant system addresses (e.g. one
address received $85.1M in 166 transfers); those are excluded by the wallet scope, which is why the
scoped file's totals are much smaller than the distributor's all-payouts total recorded for context
in `data_002_kaggle_run.json`'s source stats.

### `unmatched/unattributed_fee_legs.parquet`

Raw custody legs in scoped transactions that no rule could tie to a scoped fill (`event_type`,
`from`/`to`, `token`, amounts, `tx`, `log`, `block`, `leg_kind`, `fee_contract`) — 594 legs, 0.22%
of all scoped legs.

### `regimes/polymarket_fee_regimes.csv` / `.parquet`

`regime_id, effective_from_utc, effective_to_utc, scope, taker_fee, maker_rebate, carrier,
fee_contract, evidence` — seven rows, R0 (no fees) through R6 (2026-07-10 sports rate change),
committed in the repo verbatim as `data/manifests/fees/data_002_fee_regimes.csv`.

## Manifest and quality evidence

`data/manifests/fees/data_002_manifest.json` lists, for all 13 files: `path`, `bytes`, `rows`,
`sha256`, `kind`, plus top-level `dataset_id`, `schema_version`, `generated_at`, `families`,
`time_coverage` per family and `fee_regime_dates`. `source_commit` / `pipeline_commit` are `null` —
no commit hash exists anywhere in the build logs (`_work/stage.log`, `_work/assemble.log`,
`_work/preera.log` on the build host), unlike DATA-001's `159873f` pipeline commit.

`data/manifests/fees/data_002_quality.json` freezes the reconciliation numbers:

```text
fee_transfer_records       = 265,661
matched_fee_transfers      = 265,067
unattributed_fee_transfers = 594
matched + unattributed == total  → PASS
```

plus per-family `fee_charged_usd_equiv` / `fee_refunded_usd_equiv` totals and a note on the two
different rebate-payout scopes (all-distributor vs. wallet-scoped).

## Kaggle hosting

Uploaded as a new, **private**, standalone dataset — DATA-001 is not mutated:

- **Dataset:** `polyleviathan/sig-cup-data-002-polymarket-fees`
  (https://www.kaggle.com/datasets/polyleviathan/sig-cup-data-002-polymarket-fees)
- Static upload (`kaggle datasets create -p sig-cup-data-002-polymarket-fees -r zip`) of the
  `schema_version=1/` tree above, not a Kaggle-kernel build like DATA-001's
  `sig-cup-data-001-build` kernel — there are no mounted input datasets to record.
- 721,776,003 bytes uploaded; confirmed live via
  `kaggle datasets list --user polyleviathan --sort-by updated` after `kaggle datasets status` and
  `kaggle datasets files` both returned 403 for this account (the same 403 reproduces against the
  pre-existing `sig-cup-polyleviathan-fills` dataset, so it reads as an account/API-permission
  limitation rather than a sign this upload failed).
- Full record: `data/manifests/fees/data_002_kaggle_run.json`.

## Relationship to DATA-001

DATA-001 (historical books + fills) and DATA-002 (fees + maker/taker evidence + rebates) are
deliberately separate, immutable datasets joined only by shared keys:

```text
DATA-001                                          DATA-002
historical books + fills                          fees + maker/taker evidence + rebates
        │                                                  │
        └──────── condition_id / token_id / tx_hash ───────┘
                   (participant_address = DATA-001 maker_address)
```

DATA-001 is accepted and research is pinned to its hashes; nothing here mutates it. An experiment that wants both declares both dataset hashes explicitly, which keeps existing
EXPERIMENT-002/004A/004B/004C results exactly reproducible while adding a route to
participant/order-flow research that doesn't rely on unsigned-fill heuristics or infrastructure-
contract addresses to guess who initiated a trade.

## Non-goals

No hard-coded `is_taker`/`is_maker` label. No maker-fill attribution manufactured from a wallet-day
rebate payment. No translation of pre-fee-era zero-fee rows into a maker signal. No mutation of
DATA-001. No strategy, alpha claim, or classification rule — DATA-002 supplies raw evidence for an
experiment to preregister its own rule against.
