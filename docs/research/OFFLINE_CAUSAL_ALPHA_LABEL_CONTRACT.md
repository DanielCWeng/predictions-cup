# Offline causal alpha-label contract v0 (research-only)

**Scope:** Frozen 005I-style decision -> paired SIG/PM causal archive -> full L2
arrival -> hypothetical crossing -> full L2 exit / terminal settlement ->
fee evidence. The module is **not** a strategy, an execution replay, an API
client, a data importer, a collector, an order generator, a trading signal
approval, or an economic proof.

## Input ownership and upstream boundary

1. Invoke existing #277 `validate_offline_tape(manifest, mapping, sig, pm, expected_manifest_sha256)`
   on **immutable operator-owned paths**, without duplicating its manifest
   SHA256, EXACT mapping, native producer/seq, PM WS seed/epoch, receive/source
   clock, UTC, full-L2, rights-string and reconnect-gaps checks. #277 returns
   `SCHEMA_ONLY/PAPER_NOT_ADMITTED` for a synthetic-positive shape, never
   externally authenticated source.
2. Separate candidate projections are passed as frozen typed values. **The
   current #277 `GateResult` does NOT expose individual bound row IDs/native
   input hashes/issuer-authenticated records.** Matching a caller-written
   `paired_interval_id` string to a manifest is NOT an authenticated join.
   Hence all candidate outputs are at best `SCHEMA_ONLY/PAPER_NOT_ADMITTED`,
   even if six PM minutes and SIG full L2 are internally well formed.
3. A candidate pins model and policy hashes, split, horizon and threshold;
   the fixed 005I actual research study used TRAIN 2026-09-01..10,
   DEV 2026-09-11..20, HOLDOUT 2026-09-21..28 and five-minute response,
   with one frozen holdout read. The new module does **not** fit/tune/replay
   or inspect those historical splits. Synthetic tests intentionally use
   independent fictional 5-second timestamps; their hashes/window are
   `HYPOTHETICAL`, not a claim of frozen experiment compatibility. A future
   admission authority must verify actual frozen plan/threshold/horizon by
   original hash and signature, not by trusting these fields.

## Causal checks / output

- Separate exact SIG exchange/market and PM condition/token plus SAME or
  COMPLEMENT outcome; internally normalise a single **SIG YES** L2 book.
  A held NO purchase uses `1-SIG YES bid`; NO resale uses
  `1-SIG YES ask` **on the same exchange**, never a different party's YES.
- Exactly six continuous, completed PM minute closes already *received* by
  decision. Decision book must have been received before decision. Arrival
  book must be a fresh independently sequenced full L2 received after
  decision but no later than decision + configured transport latency.
  Exit full L2 must be from a later sequence/receipt or an explicit
  terminal settlement (not a forecast). Reject stale, future, naive UTC,
  publisher/receipt uncertainty breach, crossed/unsorted, missing book
  identity, replayed epoch and unavailable exit. The upstream #277 gate
  owns native feed continuity for the original archive itself.
- Sizes limited to whole shares 1/5/10/20. For a strictly **synthetic
  conservative** haircut `h`, executable-at-level units are
  `floor(displayed_shares_level*h)`. Accumulate only integer-level units
  for decision limit and arrival IOC crossing, and for exit-side depth.
  This matches the draft #310 frozen research convention, **NOT** a certified
  SIG native lot/queue rule. IOC arrival marginal price must not exceed
  decision-known placeable worst-case limit; no future-price filtering.
- Candidate output gives rejection reason, a purely hypothetical entry
  marginal limit / exit proceeds **without an assumed fill**, event/market/day
  cluster key, per-reason rejection counts, and NO_TRADE baseline count
  equal to candidate count. Repeated same-market/event decisions with
  overlapping horizon are rejected for episode dependence.
- Fee records, native IDs and settlement fields are still self-described
  bytes, **not operator-authenticated cash**. Missing fee is explicit
  `NATIVE_FEE_MISSING`, never filled with zero. Even caller-supplied
  `OPERATOR_RECORDED` values never become admitted real paid fees
  merely by their field labels. Every output hardcodes
  `source_admitted=false`, `paper_admitted=false`,
  `real_after_cost_susqies=null`, `network_requests=0`;
  hypothetical model prices do not appear as verified closed PnL.

## Required future operator-owned provenance for real after-cost admission

Privately attested, rights-cleared original same-market SIG/PM snapshots,
issuer+source SHA and signed native clock uncertainty, matched full L2 at
decision/arrival/exit, immutable PM seed/epoch/WS sequence with six actual
completed minutes, mapping/condition-token payoff rule and YES/NO issuer
settlement, original order+fill IDs with demonstrated uniqueness namespace,
all attempts including zero-fill/UNCERTAIN/cancel, native ACKs/partial fills,
maker-vs-taker role, actual paid fee/rebate in SUSQies, inventory reservation,
closed entry/exit/settlement cash, void/recount corrections and independent
market/day/event clusters. Original issuer authorization, file rights,
cryptographically sealed bundle-to-candidate row linkage and independent
external economic acceptance MUST exist before a different trusted pipeline
could even consider a REAL net label. **Do not modify this consumer to
promote synthetic-positive fixtures.**

**Safety:** No live/venue/REST/websocket/AWS/SIG journal/Kaggle calls, data
transfer, service/maker/risk/deployment/config action, trading/orders/cancels,
paper admission, main merge or waiver of #207 LIVE STOP-SHIP. #310 frozen
independent CI remains NOT RUN after a platform safety denial; never
circumvent that restriction. All authored PRs DRAFT/unmerged.
