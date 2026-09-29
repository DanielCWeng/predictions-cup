# DATA-004 — ETS P0/P1 Fill Corpus

**Status:** v2 `BLOCKED_QUALITY_GATE`; PR #59 remains open. Kaggle upload is pending team handoff.
**Branch:** `data/data004-ets-p0p1-fills`.
**Current OCI source copy:** `polymarket-bot-state/research/data004_ets_p0p1/v2/`.

## Purpose and frozen universe

DATA-004 captures the full available OrderFilled history for the frozen P0/P1 ETS and adjacent
universe. The scope is exactly 298 markets, 298 conditions and 596 Gamma-aligned outcome tokens:
210 P0 markets and 88 P1 markets. The delivery preserves the selected market-graph links, 134 SIG
exchange IDs, SIG anchor relationships, and accepted acquisition classes, tiers and intended uses.
Metadata for the full 1,279-market / 2,558-token frozen universe remains in the package; fill
history is limited to P0/P1, and the 797 `book_needed_later` markets remain metadata only.

The DATA-003 pairing plan covers 231 accepted anchors (140 EXACT, 87 DERIVED and 4 NEAR), 693
source conditions and 1,386 source tokens, with no overlap against DATA-004. These histories were
not reacquired or copied into DATA-004.

## Source and row grain

The v1 extraction scanned the Polyleviathan OCI daily trades lake and joined block numbers from the
custody lake by transaction hash. It read 348 trade-object dates, 2025-10-09 through 2026-09-21,
with no missing trade-object day in that interval. There are 231,964 rows across 343 fill dates,
from 2025-10-14 through 2026-09-21. The latest available trade-object date was measured from the
live object inventory, not assumed.

Each row is one participant order fill, deduplicated on `(tx_hash, log_index, token_id)`. It retains
the raw transaction, condition, token, market/event metadata, UTC timestamp, log index, signed-order
side, price/size/value, maker/taker addresses, source object provenance, outcome alignment and SIG
graph links. `side` is preserved verbatim. `economic_direction` is derived only from registered
signed-order semantics; `order_role` uses the registered exchange-address set. Neither is a
strategy label. Source numeric fields retain the upstream Parquet schema; conservation sums use
`Decimal(str(value))` without an epsilon tolerance.

## v1 and v2 versioning

v1 was never accepted. Its fill and metadata Parquet objects remain unchanged under
`research/data004_ets_p0p1/v1/`; its status manifest is marked `BLOCKED_SUPERSEDED` and points to
v2. The original v1 manifest SHA-256 was
`048bd5a59642f14c174316590adbdce220f9efabbf571a3a8348da30bc33ce72` before the supersession
annotation.

v2 reuses all 231,964 v1 rows; it does not rescan the trade lake. It changes block numbers and their
provenance for the custody-gap rows, sorts the two affected date partitions, and recomputes the
related gate and coverage outputs. No market, token, side, price, size, timestamp or SIG graph field
was changed.

### Block-number derivation and ordering

Custody objects are absent for 2026-09-20 and 2026-09-21. Before deriving any block numbers, the
same timestamp lookup was tested on custody-covered fills from 2026-09-15 through 2026-09-19:
9,803 of 9,803 known-custody rows had exactly one `public.block_timestamps.ts` match, and every
mapped block agreed with custody. The read-only table query over 2026-09-20 through 2026-09-22
contained 133,665 rows and 133,665 distinct timestamps (no timestamp collisions).

Each of the 4,989 v1 gap rows has exactly one block timestamp match: 1,625 rows on Sep 20 and 3,364
on Sep 21. Their provenance is `block_timestamps_unique_ts`. The remaining 226,975 rows retain
`CUSTODY_TX_HASH_JOIN` provenance. v2 has zero missing blocks, zero imputed blocks, and zero
per-token ordering failures under strict `(block_number, log_index)` order.

### Maker/taker conservation

The v1 gate grouped by `(tx_hash, token_id, timestamp, price)`. That partition can split
complementary-token matches and a taker order's aggregate price can span multiple maker price
levels, so it was not a valid conservation key.

The v2 gate groups the taker-order rows (`order_is_match_taker_order=true`, meaning an exchange
counterparty) and maker-order rows by `(tx_hash, condition_id)`. It sums all applicable same-token
price levels and the binary complement relationship (the other token at `1-p`) using exact decimal
arithmetic and no epsilon tolerance. There are 96,630 groups: 96,587 match exactly, zero are
one-sided, and 43 retain a residual of exactly ±0.0001 shares. A targeted read-only source lookup
scanned only those 43 transaction hashes on their source dates: it found zero out-of-scope fill
rows, so none is classified as an explained scope residual. All 43 unexplained residuals are listed
individually in `maker_taker_tx_condition_residuals.csv`. The gate remains failed.

## Coverage and gate outcome

The full package has per-market, condition, token, event and date coverage; every selected market
has fills, and the zero-fill file is empty. Source-day inventory retains the measured object list
and documents custody gaps separately from the final v2 block-number coverage. All 298 markets
remain within their Gamma creation/end bounds.

v2 passes universe scope, Gamma outcome alignment, graph/anchor links, key completeness,
deduplication, block provenance, ordering, core-field completeness, price bounds, condition
identity, lifecycle bounds, source-date coverage and per-market coverage. Only
`maker_taker_size_symmetry` fails because of the 43 exact-decimal residual groups; therefore v2
remains `BLOCKED_QUALITY_GATE`. Do not expand beyond P0/P1 or begin R3 predictive/fair-value work
until those residuals are explained.

## Delivery and evidence

Delivery is **Kaggle (pending team upload)** to the private dataset
`polyleviathan/sig-cup-data-004-ets-p0p1-fills`. The package source is
`/home/ubuntu/inbox/data004_20260929/sig-cup-data-004-ets-p0p1-fills/`; the matching `.tar.zst`
and its SHA-256 are recorded in `data/manifests/fills/data_004_kaggle_run.json`. GitHub handoff
files are under `data/kaggle_handoff/data004/`. The host has no Kaggle CLI credentials, so no upload
was attempted.

The baseline plan is not a fill extract. No order-book history, chain RPC, Postgres writes,
outcome-based graph changes, or R3/FV tests are part of DATA-004.

Evidence:

- `data/research/data004_ets_p0p1/v2/data004_manifest.json` — v2 source, counts, objects and gates.
- `data/research/data004_ets_p0p1/v2/data004_quality.json` — gate summary and each failure.
- `data/research/data004_ets_p0p1/v2/block_timestamp_evidence.json` — validation and timestamp map.
- `data/research/data004_ets_p0p1/v2/maker_taker_conservation.json` — exact-decimal gate method.
- `data/research/data004_ets_p0p1/v2/maker_taker_scope_audit.json` — targeted source check.
- `data/research/data004_ets_p0p1/v2/maker_taker_tx_condition_residuals.csv` — all 43 residuals.
- `data/research/data004_ets_p0p1/v2/data004_baseline_pairing.json` — frozen DATA-003 pairing plan.
- `data/manifests/fills/data_004_kaggle_run.json` — package hash, source manifest and pending team upload.
- `/home/ubuntu/campaigns/data004_20260929/REPORT.md` — seven-item delivery report.
