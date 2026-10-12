# LANE-3 — executable 005I conversion | issue #205

**Scope:** independent research and PAPER only. No production execution, live keys, risk or maker-control edits. Draft PR review mandatory.

## Scientific distinction

EXPERIMENT-005I's frozen five-minute reversal was 67.26% over 55,340 **dependent** minute-state samples in its prior sealed holdout; the frozen OFI challenger improved next-minute direction log-loss by 0.004165. Neither statistic is executable SIG PnL. In particular, OFI's probability concerns *next-minute sign*, **not** fair-value magnitude, 5-minute liquidation price, queue priority or a fill.

This evaluator does not respend the historical sealed 005I HOLDOUT (2026-09-21 to 2026-09-28). The CLI allows DEV through 2026-09-20 or prospective PAPER on/after 2026-10-12. Historical direction statistics are citations to prior research only.

## Current measurement

- Accepted, connected PM plus SIG full-depth prospective PAPER captures evaluated in this lane: **none**.
- Measured cross-SIG eligible opportunities: **unavailable / NOT RUN**.
- Measured net executable PnL / 95% lower bound: **unavailable / NOT RUN**.
- Empirically calibrated maker or taker fill probability: **unavailable / NOT RUN**.
- Promotion: **BLOCKED**, pending non-synthetic event/day evidence and observed order/fill parity.
- This lane has not sent any real SIG orders.

Synthetic unit tests exist to reject latency lookahead, non-EXACT mapping, bogus feature availability, incomplete depth, no-IOC arrivals and illiquid exits; these tests **do not demonstrate alpha**.

## Implementation

- `src/predictions_cup/replay/features_005i.py`: reconstructs frozen 005I inputs from accepted PM replay events. Uses **receive-time**, requires an observable quote in every one of six consecutive closed minutes, and rejects incomplete depth/stale end-of-minute states. Returns original-style 1m/5m price differences, five-return sample standard deviation, quote-event count, relative spread, five-level depth, L1 imbalance and the exact previous-to-current BBO depth-normalised OFI formula. This is a deliberately stricter coverage surface than the retrospective 005I minute panel; no population-frequency claim or automatic equivalence is allowed without parity validation.
- `src/predictions_cup/replay/economic_005i.py`: scores frozen baseline/challenger coefficients without refitting; samples decision only **strictly after** feature availability; checks receive-time books, depth and trust; quantifies a hypothetical IOC at a post-latency observed SIG full-depth snapshot, then hypothetical market liquidation at 5s/30s/5m/1h. Unavailable future depth is missing, not silently replaced with midpoint.
- `scripts/research/lane_005i_economics.py`: explicit read-only CLI using existing SIG/PM capture loaders; derives a causal feature tape by default; optionally consumes an independently audited precomputed minute tape.
- `tests/test_economic_005i.py`: adversarial scenario tests, with no claim of real-world returns.

The upstream replay infrastructure must be audited for exact-contract and depth completeness before meaningful economics. Note that no passive maker queue simulation is certified: only aggressive hypothetical IOC touches can be scored. A touch is **not** a measured fill and consequently cannot earn E4/E5 promotion by itself.

## Preregistered research comparison

Strategies share identical timestamps, fees, capital and depth assumptions:

| Candidate | Frozen directional condition |
| --- | --- |
| NO_TRADE | abstention (zero trading return) |
| REVERSAL | sign opposite preceding 5-minute PM price difference |
| PERSISTENCE | same 5-minute sign (negative control) |
| OFI_BASELINE | direction from frozen 005I baseline logit |
| OFI_CHALLENGER | direction from frozen challenger including depth-normalised OFI |
| DIRECT_PM_FV | orientation by executable SIG discount to contemporaneous PM midpoint |
| RANDOM_TIME | deterministic hash-gated randomized time/direction control |

All must pass the **same** minimum edge vs PM midpoint after an explicit fee reserve, a positive edge buffer, conservative visible depth haircut and post-latency IOC limit. This still uses PM midpoint as an *independent reference*, not guaranteed realizable fair value. Do not promote an apparent signal until its challenger improves post-cost returns *relative to its proper baseline*, with stable independent event/day results and regime/market stratification.

Primary horizon is executable 5-minute close. Hypothetical entry quantity consumes scarce gross capital for the holding period. Each market purges overlapping 5-minute opportunities; missing closes keep exposure reserved. Scorecards report known conditional exits separately from a **zero-salvage stressed bound**, 5s/30s/5m/1h marks, opportunity counts, and day-cluster resampling only where at least five independent days exist. A no-exit synthetic stress is not an actual liquidation PnL result. Fee rates and transport delays are mandatory caller assumptions, **not measured fee schedules or ACK latency**.

## Reproducible operator run (PAPER, read-only)

```bash
python scripts/research/lane_005i_economics.py \
  --mode prospective --start 2026-10-13T00:00:00Z --end 2026-10-14T00:00:00Z \
  --sig-db /path/to/accepted_sig_capture.sqlite \
  --pm-capture /path/to/accepted_pm_capture_or_parquet \
  --sig-exchange-id 1234 --registry-root . \
  --fee-bps-per-side 20 --latency-ms 100 --min-edge 0.02 \
  --quantity 1 --capital-limit 100 --gross-share-limit 100 --depth-haircut 0.5 \
  --output /tmp/lane3_paper_scorecard.json
```

Mapping is read from `data/mappings/sig_polymarket_2026.json` and the
accepted `sig_polymarket_2026_acceptance.json`, with a verified SHA-256
crosswalk digest, complete CLOB-smoke evidence and `VERIFIED` `EXACT`
`SAME/COMPLEMENT` entries only. The exchange ID filter is optional.
The CLI **does not take a user-supplied pair file**: self-declared
`ACCEPTED_EXACT` strings no longer establish anything.

For each **one** SIG exchange ID, YES buy consumes YES asks; NO buy
consumes YES bids at `1 - YES bid`. YES sell consumes YES bids;
NO sell consumes YES asks at `1 - YES ask`. Each consumes the observed
*same-exchange* depth. NO BUY increments negative YES-equivalent
inventory; a partial NO close sells NO and reduces that negative
inventory while locking residual cash and gross. Nothing offsets
different party exchanges or assumes exactly two political parties
can win. PM token outcome and orientation are taken only from canonical
reviewed identity (PM token itself may represent NO).

`--capital-limit` is an actual SUSQies cash commitment; 
`--gross-share-limit` separately caps quantity exposure. Both are
applied independently with entry fee reserve. The 20 bps / 100 ms
example is **illustrative**, not measured venue fees or latency.
`--quantity` must be integral under the SIG order contract.

Do not place real orders or add LIVE toggles. Run offline against captured data, freeze choices on TRAIN/DEV, and use prospective PAPER forward observations before considering review. Verify the rows' same-time and gap semantics, event coverage and original 005I feature formula parity first.

## Promotion decision

**NO PROMOTION.** A passed lint/test suite only means the evaluator is mechanically safer; it is not evidence of positive net PnL. Minimum future evidence: full-depth paired EXACT capture + independently verified feature parity, actual order acknowledgements/fill and fee calibration, profit positive under latency/depth/no-fill stress, non-overlapping independent day/event lower bound positive, gains over frozen baselines and negative controls, and no material concentration by market or liquidity regime. Any failed gate kills the candidate. Master orchestrator should retain 005I as a predictive research result until then.

## Adversarial review response (12 Oct 2026)

Master reviewer identified a genuine P1 semantic defect in PR #209:
`sig_no` was incorrectly a distinct SIG exchange and BUY NO
incorrectly purchased another SIG YES outcome. This has been removed.
The PAPER evaluator now orders NO by `side=no`, `action=buy`
on the **same** SIG exchange, with NO prices/liquidity derived from
YES bids; sale/liquidation uses YES asks and conserved partial
signed YES-equivalent inventory. Cash reservations now use
actual price-weighted entry commitment and fees, separate from gross.
Mapping comes only from SHA-bound canonical MAPPING-001 acceptance,
not an arbitrary pair JSON file. Added targeted regressions for
NO settlement contrast/third-party non-equivalence, partial closes,
complement orientation, invalid mapping/digest, trust/health outages,
and no phantom depth tick on reconnect.

**Science status unchanged:** prospective paired episode count unmeasured,
empirical fill probability unavailable, executable net PnL unavailable,
economic confidence lower bound unavailable. These fixes correct the
offline apparatus but do not confer strategy profitability or readiness
for LIVE. The draft PR must receive independent regrade before merge.
