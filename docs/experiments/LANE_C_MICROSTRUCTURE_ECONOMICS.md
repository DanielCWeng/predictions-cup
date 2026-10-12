# LANE C — microstructure economics (#201, #205, #207, #220)

**Research only. No maker config, LIVE order sending, trading privilege, global cap changes or deployment.** This stacked work depends on draft #209 and draft #214 for the corrected Lane-3 one-exchange YES/NO economics and accepted MAPPING-001 EXACT loader. Review/merge order must preserve those dependencies.

## Scientific finding as at 12 October 2026

**All eight proposed trading mechanisms: INSUFFICIENT DATA; NO PROMOTION.** West's standalone SIG capture has zero configured full-depth tracked IDs and PM captured millions of uninitialised book deltas according to #220. There is no independently verified prospective matched executable SIG/PM sample, observed passive queue/fill model calibration, complete life-cycle PnL, event/day confidence bound, or capacity measurement available to this lane. Missing data is NULL, not zero. In PR #175 the old maker-fill calibration estimated 487.4 holdout fills against 149 actual (227.1% relative error). Do not reuse it. 005F book-age update hazard, 005G renewal state and frozen 005I reversal are predictive observations, not net execution evidence.

## Frozen initial comparison (thresholds are exploratory, not trained)

| Candidate | Decision-time sign | Execution |
|---|---|---|
| PASSIVE_SPREAD | YES if spread >= 0.04 | passive, requires real journal ACK/fill |
| DEPTH_PASSIVE | L1 imbalance magnitude >0.30 | passive |
| STALE_QUOTE | signed PM minus SIG reference magnitude >0.04 | hypothetical IOC limit |
| INVENTORY_SKEW | opposite signed YES-equivalent inventory | passive |
| TRADE_REVERSION | opposite most recent observed aggressive trade direction | hypothetical IOC |
| REPLENISHMENT | signed replenishment index magnitude >0.20 | hypothetical IOC |
| SHORT_DISCOVERY | signed predecision discovery score magnitude >0.02 | hypothetical IOC |
| CROSSVENUE_STALE | reference discrepancy >0.04 **and** independently verified lag | hypothetical IOC |
| NO_TRADE | abstain | no-trade control |
| PASSIVE_RANDOM | deterministic hashed YES/NO | passive negative control |
| SIMPLE_IMBALANCE | L1 imbalance >0.30 | IOC control |
| FROZEN_005I | independently attested pre-frozen 005I sign | IOC control |
| DIRECT_FV | PM−SIG reference difference >0.02 | IOC control |
| ADVERSE_SELECTION | agrees with recent aggressive trade | IOC negative control |

Reference/midpoint differences are *hypothesis features*, never entry proceeds or realized profits. The external producer must supply reference_orientation=SIG_YES for reference-difference candidates and preserve the PM-token orientation for frozen_005i_signed; the evaluator reverses the frozen sign on a canonical COMPLEMENT mapping. Never interpret raw PM-token probabilities directly as SIG YES fair value. These feature fields still need an independent #220 preregistered causal feature producer: caller-entered numbers cannot establish evidence. No optimisation on sealed 005I HOLDOUT. The explicit thresholds are initial falsification rules, not empirically optimal model hyperparameters.

## Data contract and usage

The research CLI takes one UTF-8 JSON object per line. Required: episode_id, event_id, sig_exchange_id, pm_token_id, mapping_direction, decision_at (with UTC offset), data_origin=LANE_B_PAIRED_PROSPECTIVE_PAPER, paired_depth_trusted=true, features, feature_window_end, features_available_at, decision_book, arrivals and exits. Feature window must close and reach the researcher no later than decision time; a missing or future availability clock is rejected. STALE_QUOTE additionally requires a causally observed external price update, not merely an old reference gap. Snapshot objects use observed_at, trusted=true, complete=true, bids and asks as ordered price/quantity pairs in **YES-quote** units. Arrival keys: 1x, 2x, 5x. Exit keys: 5, 30, 300, 3600 seconds.

Passive observed_orders are keyed by candidate name and must have order_id, provenance=SIG_EXECUTION_JOURNAL_JOINED, strategy_id, sig_exchange_id, submitted_at, limit_price, side (YES/NO), action=buy, ack_at, filled_qty, fee_paid and for a positive fill: fill_at and fill_price. The order must be uniquely matched by strategy, exchange, decision-time resting quote and ACK chronology. An observed ACK with zero fill is included in the fill-rate denominator. An order must have independent time-ordered journal proof; the caller's self-declared label is never sufficient scientific verification.

Read-only example with no residual capacity:

    PYTHONPATH=src python scripts/research/lane_c_microstructure.py \
      --input /path/to/verified-paired-paper.jsonl \
      --output /tmp/lane-c-result.json --cash-free 0 --gross-free 0

These zero values are deliberately safe defaults for an account at full gross exposure, **not** a simulation of unconstrained deployment. The CLI requires explicit free cash and free gross allowance, and validates exchange/token/orientation against SHA-attested MAPPING-001 EXACT canonical mapping. It computes a SHA256 of the input tape. Never put credentials or order keys into the tape or report.

## Execution economics and evidence classification

- Reuses Lane 3's one-exchange side adapter: buying NO crosses YES bids at 1-bid; selling NO crosses YES asks at 1-ask. Other party outcomes are different exchanges and never silently used as NO. No complementary-position netting without authoritative portfolio reconciliation.
- Set IOC limit from **decision-time marginal level** for whole-share size, reserve limit × shares plus fees, walk only **post-latency observed** opposite-side depth inside that limit and apply visible-depth haircut. Aggressive IOC touch is labeled HYPOTHETICAL_IOC_TOUCH, never called a fill. If arrival depth is missing or unplaceable: reject.
- Passive executions count only uniquely journal-matched actual ACKs and partial fills. If a passive quote has no trustworthy queue/fill outcome, default deny. Orders that did not fill remain in rejected/no-fill opportunity ledger; their trade PnL is not fabricated. Even an observed passive entry's future depth-based hypothetical exit is not a closed trade.
- Quote trust, bounded event-time gaps, explicit mapping, observed bids/asks, event IDs, separate cash/gross budgets, and 5-minute per-contract episode cooldown are enforced. An unobservable 5s/30s/5m/1h exit is NULL, never exit at midpoint or free recovery. Confidence bounds cannot be formed with fewer than 5 days and 5 events.
- Every candidate has a ledger row, including no-signal, capacity rejection, untrusted data, ACK-no-fill and model abstention. Scorecard distinguishes observed ACKs, observed partial entry counts, hypothetical touch count, conditional markouts and unavailable realised economics.
- Profit per capital-second and genuine capacity ceilings are deliberately NULL without reconciled complete-entry/exit risk. Conditional capital-seconds are an illustrative opportunity-cost denominator only. Global correlated election exposure, reversals, unresolved orders and whole-portfolio locks require independent portfolio-level risk/ledger reconciliation upstream.

## Stress protocol

1. 1x, 2x and 5x arrival latencies must use separately observed books, never a repeated or projected 1x book; missing arrival = NULL.
2. Reduce qualified size/depth at least 50%, simulate spread compression and high volatility on independently tagged future tape **without** choosing favourable states after observing outcomes. Competitor quote cancellation and late ACK require empirical journal-linked no-fill/adverse-fill evidence, not a trade-print shortcut.
3. Day/event cluster estimates require sufficient independent prospective days and elections, not repeated microsecond samples from one race. At least 5 separate events and 5 separate days for even a descriptive bootstrap interval; observed book exits are still hypothetical.
4. Benchmark no-trade, passive random, simple imbalance, frozen 005I, direct fair value and adverse selection. Compare maker and taker separately, and never call many trades a profitable strategy.
5. Promotion would require positive *observed reconciled* full-lifecycle net PnL and conservative independent event/day lower bound, valid full-depth exit capacity, stable latency/cost/depth/volatility stresses, actual calibrated queue/fill observations and separate approval by #207. The current prototype **does not** auto-classify any mechanism PROMISING.

## Known limitations and independent review request

This is an evaluator/protocol, not a production feature or a complete portfolio scheduler. It does not infer queue priority, certify user-provided causal features, price dynamic order impact, prove independent paired-depth provenance, or complete global multi-market capital arbitration. These are hard missing evidence, not research wins. Lane B #220 must provide trusted full-depth paired tape, source-receipt clocks, order/fee confirmations, fill/cancel samples and regime labels. Lane 3 #205/#209/#214 owns the common economics interface. Lane 5 #207 must independently review mapping attestation, NO accounting, queue/cancellation latency, 429 and reconnect gaps, event correlation, capital reservation, code tests and any positive claims. No LIVE promotion from a merged research branch.

Deterministic synthetic tests: PYTHONPATH=src pytest -q tests/test_microstructure_c.py. Machine-readable initial **NULL-valued** scorecard: data/research/lane_c_microstructure_initial_scorecard.json. A real input run writes the complete ledger and per-strategy scorecard without truncating negative/null opportunities.
