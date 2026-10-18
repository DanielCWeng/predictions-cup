# Lane C — actual SIG maker-fill economics: 14 October evidence verdict

**Verdict: INSUFFICIENT_VERIFIABLE_FILLS. No passive-maker alpha claim, no historically supported SHADOW parameter change, no LIVE action.** This is a bounded assessment of the *existing* historical venue execution provenance, not another cross-venue maker model. It supplements the prospective default-deny evaluator already in PR #223.

## What was actually read

Read-only inspection of Lane A's **retained private** `/home/ec2-user/alpha_lane_a_outputs/fill_provenance_20261013/{fill_provenance.csv,fill_aggregate.json}` on the authorised research host; no SIG API request, live journal SQL query, SQLite/WAL modification, producer restart, recorder enablement, order or cancellation.

- Upstream source: Lane A's authenticated **12 October 2026** venue snapshot (fills dated **1–6 October**) and read-only historical journal reconciliation. Lane C reused the *derived* CSV and aggregate; Lane C did **not independently reauthenticate the original venue snapshot/journal** in this pass.
- Immutable CSV SHA256: `0b8040f82c489dc4450f3c568e1f2700f5bc878fc15e34eb094effaf6e40690e`.
- Upstream venue snapshot SHA256: `bd38a5b0df97656d796a979b4455ca6041fb8ecc3f6f8555aa9f217b7e98911c`.
- Source is dated; filesystem mtimes do **not** establish whether trades occurred after 6 October.
- Private identities/order IDs and raw per-fill quantities remain on the research host; GitHub receives aggregates, code and synthetic tests only.

## Verified aggregate denominator, then the crucial maker/taker split

| Evidence class | Venue fills | Interpretation |
|---|---:|---|
| Authoritative fill ID (76 single + 9 identical duplicate-event cases) | 85 | Fill-level matching, duplicate *journal events* count once |
| One-to-one venue/realtime signed quantity, price, order, exchange and source-clock match | 104 | Fill-level, no shared native fill ID |
| Journal FILL_SUMMARY only | 312 | Order association, *not* a verified individual fill |
| Other matching journal order event | 66 | Order association only |
| NULL-exchange ACK linked by order ID only | 1 | Weak order association only |
| **Total** | **568** | **189 fill-level / 379 order-level-only** |

There were **448 unique venue order IDs with fills** across this mixed-strategy 568-fill cohort. These are **not** 448 maker eligible ACKs and do not include unfilled orders.

**New substantive attribution check:** in the 189 fill-level matched rows, only **3** matched journal fill events explicitly carry `strategy_family=MAKE`, **87** carry `TAKE`, and **99** have an empty/unresolved family field. These are **fill-event labels, not reconstructed full order lifecycles**. In particular, **3 is not the number of actual maker fills**: many confirmed fills were matched to journal events without a strategy-family field, and 379 further venue fills have weaker order associations. The 104 realtime economic matches are YES-side; 85 authoritative ID matches split 53 NO / 32 YES. Do not impute NO-side complement prices or passivity.

Raw journal event inventory in the upstream summary includes **11,210 ACK**, **10,619 CANCEL_ACK**, **285 CANCEL_UNCERTAIN** and **104 CANCEL_REJECTED** events. These are *event counts*, not uniquely matched maker orders or causal no-fill counts; some are for TAKE and some for multiple legs/operations. Treating ACK counts as maker fill-probability denominator would be an identification error. A cancel request is not proof of cancellation; an order-level association is not a verified fill.

## Economic test results — what is and is not observable

| Requested statistic | Current observation |
|---|---|
| Venue fill ID, exchange/order ID, side, venue signed quantity/price, source clock | Present for 568 venue fills; source-clock-matched journal event for 189 only |
| Quote submitted, ACK, partial fill, successful cancellation, non-fill, reject, unresolved, resting quote orientation | **Not completely joined at maker-order level** in available private CSV |
| Spread captured at the independent pre-fill BBO | **NOT MEASURED** — missing valid quoted BBO and causally timed arrival |
| Signed price movement; 5s / 30s / 5m / 1h post-fill markouts | **NOT MEASURED** — no receipt-time-valid quote series or independent executable exit ladders in these artifacts |
| Execution fees and NO signed cash/whole-share basis | **NOT MEASURED** — no individually authenticated fee joins or adequate side conversion |
| Inventory accumulation, opportunity cost, settlement outcome | **NOT MEASURED** — no complete correlated capital/entry-unwind ledger |
| Realised maker net PnL; net SUSQies per gross-share-day; adverse-selection subgroup | **NOT ESTIMABLE** — no validated maker attribution + complete closed lifecycles |
| Clustered 95% day/event lower bound versus no-quote and unchanged controls | **NOT ESTIMABLE** — no eligible maker sample with measured after-cost outcome |

**Control:** The no-quote/no-new-order *incremental trading cashflow* is defined as **0**, but zero is **not** an estimate of what happens to existing inventory or the opportunity cost of not trading. The older ~487.4 predicted versus 149 actual fills belong to a **different historical evaluation** and cannot be reconciled or calibrated using this mixed 568-fill pool without the original unique quote/ACK denominators.

We cannot conclude that passive maker activity made money, nor that it lost money, from these records. Positive 5-second or 5-minute mid-price returns would remain descriptive, never realised profit.

## Reproduce the bounded analysis without touching LIVE

New standalone read-only parser: `scripts/research/lane_c_historical_maker_audit.py`. It SHA-pins the private CSV, cross-checks source status/confidence categories and upstream aggregate totals, checks signed quantity/price, timestamps and one-to-one economics, and generates per-fill **NULL** markout/fee/inventory columns rather than synthetic numbers. It writes output only to an *explicit pre-existing private directory* using exclusive-create permissions. Do not check the private fill ledger or credentials into Git.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/research/lane_c_historical_maker_audit.py \
  --provenance-csv /home/ec2-user/alpha_lane_a_outputs/fill_provenance_20261013/fill_provenance.csv \
  --lane-a-aggregate /home/ec2-user/alpha_lane_a_outputs/fill_provenance_20261013/fill_aggregate.json \
  --expected-sha256 0b8040f82c489dc4450f3c568e1f2700f5bc878fc15e34eb094effaf6e40690e \
  --output /PATH/TO/APPROVED_PRIVATE_DIR/lane_c_fill_scorecard.json \
  --private-ledger-output /PATH/TO/APPROVED_PRIVATE_DIR/lane_c_fill_private.json
```

**Run status:** CSV/hash/aggregate and above strategy-family cross-tab were read and measured on host; *new script has local synthetic test coverage only*. The reproducible parser's full private-host CLI invocation and exact-head repository CI remain **NOT RUN / PENDING** at this writing. The machine-readable aggregate result is `data/research/lane_c_historical_maker_fill_scorecard_20261014.json`, explicitly sourced from the direct read-only census rather than claiming this CLI ran. A SHA match is tamper-detection, not independent venue authentication.

## Next decision (bounded 48-hour sprint)

**No SHADOW hypothesis nominated yet**: the user instruction is to choose at most two only if measured data supports them; it does not. A passive spread-width or inventory-skew adjustment today would be untested parameter tuning.

The one necessary **evidence repair** is to use a separately authorised immutable offline journal snapshot and venue fills to link each `MAKE` placement/submission → correct exchange order and signed YES/NO limit → ACK → zero/partial/terminal fill → cancel request vs confirmed ACK → fee and position lifecycle. Keep UNKNOWN family and unsettled exposure separate. Only then join independent decision-time and post-fill receipt-valid SIG quotes at 5s/30s/5m/1h. Include *all* eligible terminal maker ACKs with zero fill, unresolved reservations and rejected placements in an explicit cohort. Recompute historical 487.4 vs 149 only if unique quote events and inclusion windows can be matched; never pool the incompatible counts. Prefer preregistered market/event/day cluster intervals, signed quote distance, inventory direction, spread and stale-health strata versus a no-quote and unchanged-maker baseline. If quote/fee/exit history does not exist, declare the earlier economic question **unanswerable historically** and collect a newly approved prospective SHADOW denominator without extra venue polling or changing LIVE maker behaviour.

**Release boundary:** Draft PR #223 remains stacked behind #209 → #214 and requires independent #207 economics review. The LIVE recorder/source safety STOP-SHIP, trading cutoff and all existing risk/capital locks remain unchanged.
