# Lane D — SIG leaderboard intelligence and tournament strategy
**Date:** 12 October 2026. **Scope:** research only, no live orders. Primary issue #221; parent #201. This is a falsification plan, not an alpha claim.

## Provenance and visibility
The two committed snapshot tables are **deliberately separate**:
- data/research/leaderboard_intel_20261012/issue221_official_aggregate_20261012T2020Z.csv transcribes **only** three precise rows already recorded in issue #221 from SIG's official participant API at **approximately** 20:20Z (ranks 1, 2, 5). It is not a fresh API response or full top-25 export. The approximate source timestamp is minute-level, not an exact server clock.
- data/research/leaderboard_intel_20261012/public_web_leaderboard_top25_approx.csv transcribes the public https://sig.thesuper.market/leaderboards top 25 displayed on 12 October: **rounded PnL (+0.1k units) and displayed trades only**. Site rendering has no authoritative server timestamp in this capture. Missing volume is marked NOT_OBSERVABLE. The page's 1,459 trader count and #221's 1,467 are different nonsynchronized observations, not proof of departures. Never subtract these two sources to manufacture a trend.

Official repository DTO fields in src/predictions_cup/sig/realtime_models.py and client.py: rank, profileId, username, PnL, tradesCount, volume, winRate, ROI; supported period all/1d/7d/30d with 1–100 rows per page. The offline normalizer added here can ingest **already authorized, locally captured** exact JSON, stamp actual received-at UTC, reject invalid entries, calculate descriptive PnL / volume, and compare same-period cumulative observations only. It does not call the API. No valid 1d, 7d or 30d numerical history was available to Lane D: NOT_OBSERVABLE. Do not infer from current marks.

A limited sample of public profiles was checked using links on SIG's leaderboard: rank 1 kind-brook, rank 3 steady-finch, and rank 5 calm-badger explicitly show private profiles; ranks 2 bright-finch, 4 nimble-beacon, 7 noble-birch and 14 tidy-dawn show public profile summaries but this access did not expose fill-level trader footprints. **Competitor holdings, order identity, fill prices, market-by-market trade attribution, realized-vs-unrealized PnL, strategy and execution privileges: NOT_OBSERVABLE**. No privacy bypass or website internal endpoint calls are permissible. Sources: https://sig.thesuper.market/docs/portfolio-and-leaderboards and https://sig.thesuper.market/leaderboards.

### Low-frequency monitoring
Prefer existing OBSERVE-001 competition_context material already captured from participant API. If an operator independently verifies spare **shared-account** read budget, retain a once-a-day top-25 all/1d/7d response through the approved authenticated client, with per-request monotonic/UTC receive time, tournament slug, period, limit/offset/total, client code SHA, untouched response JSON SHA256 and no overwrite. No more than one bounded capture/day for this lane unless approved. Period PnL does not equal naive difference of two daily marks. Do not use the website's internal endpoints; the October 1 changelog explicitly prohibits treating them as the bot API. No new calls were made for this PR.

## Exact aggregate economics (only matching provenance)
| Account | PnL | Trades | Reported volume | PnL/volume | Average volume/trade | Volume / 100k start |
|---|---:|---:|---:|---:|---:|---:|
| #1 kind-brook-6861 | +941,965.08 | 3,444 | 10,562,773.800 | 8.92% | 3,067.01 | 105.63x |
| #2 bright-finch-0400 | +845,587.81 | 16,322 | 20,271,042.445 | 4.17% | 1,241.95 | 202.71x |
| #5 calm-badger-5206 | +596,015.73 | 916 | 4,986,428.355 | 11.95% | 5,443.70 | 49.86x |

Each starts with 100,000 SUSQies. Conditional implied mark-based account multiples are approximately 10.42x, 9.46x and 6.96x. None is settled or independently attributable. PnL / reported volume is **not** maker spread, economic return on risk, volume-normalized *realized* edge or cash turnover. We have not verified whether public volume counts both legs of matches or how the scoreboard marks illiquid claims.

## Public trader-style taxonomy — observable signatures, not strategy diagnosis
- High-turnover fingerprint, >=10,000 displayed trades within top 25: #2 bright-finch (16,301), #3 steady-finch (10,821), #7 noble-birch (25,595), #12 clever-robin (10,239), #15 warm-beacon (13,317), #21 witty-pebble (17,037), #25 tidy-badger (23,945). HIGH evidence for counts; **LOW for inferring MM**.
- Moderate-turnover high-current-PnL fingerprint: #1 kind-brook (3,431 displayed trades, +942.4k rounded), #4 nimble-beacon (3,220, +722.6k), #6 bright-badger (2,371, +564.4k), #8 merry-coral (5,015, +435.6k). Again no proof of roundtrips or inventory turnover.
- Lower-count / concentrated-compatible fingerprint: #5 calm-badger (916), #14 tidy-dawn (240, +193.3k), #17 ready-maple (403, +128.0k), #19 quick-ember (695, +102.3k). **Concentration itself NOT_OBSERVABLE**.
- Verification flags, not accusations: #5 high PnL/volume, #14 high PnL with 240 trades, #25 23,945 trades but +54.3k PnL. Unusual observation could reflect different sizes, events, marks, or actual strategy. Never infer self-trading or manipulation. Top-25 conditioning causes survivorship bias; failures are missing.

## DMM / API entitlement facts
Official SIG October 1 changelog: https://sig.thesuper.market/docs/changelog?page=2
- Standard account: 100 reads/min, 30 writes/min, shared by **all** API keys.
- Designated DMM: 600 reads/min, 200 writes/min; a higher account-level entitlement **not available by generating more keys**.
- Bots must use the published API rather than internal website routes. October 2 price/trade REST freshness can be up to 2 seconds old and books up to 1 second old; realtime revisions must be checked for gaps.
- Existing OBSERVE-001 docs explicitly mark DMM trader analytics / Super Signal endpoint admin-only and unavailable to participant key.
- September 23 maker-liquidity-sweep changelog does not prove special powers for our participant.

**Our specific DMM status is NOT VERIFIED**; no readable authenticated entitlement proof in source evidence. Budget all research for standard limits until SIG or account response establishes a DMM designation. No assumption of fee rebates, priority queue, superior fills or order-handling speed. DMM access/application eligibility likewise unverified; cannot promise that we qualify.

## Capital and turnover arithmetic
The last authoritative account snapshot in #221 says our account value 100,333.42 with 61 positions; prior own RISK-002 says gross **100,000/100,000 shares**, net directional -39,752. This is dated and not refreshed by Lane D. Public #3 displayed +765.5k implies third-place current mark ~865.5k and a naive ~765.2k value gap to us if these two non-synchronized snapshots were comparable. **Final podium threshold unknown and moving**.

The conditional 765k incremental profit requirement, if prospectively executable *net* profit-to-comparable-turnover were:
| Realized net profit/volume assumption | Comparable volume necessary | Equivalent 100k-SUSQie cycles |
|---|---:|---:|
| 0.5% | 153 million | 1,530 |
| 1.0% | 76.5 million | 765 |
| 2.0% | 38.25 million | 383 |
| 5.0% | 15.3 million | 153 |

The figures ignore fees, risk decay, capacity, market impact and fill uncertainty and are **not estimates that we can achieve those margins**. 100k SUSQies in cycle notional is a sensitivity unit, not the same as the 100k **gross-share** cap. A static holding of at most 100,000 binary winning-side shares pays at most 100,000 SUSQies on those shares at settlement plus remaining cash, subject to actual signed YES/NO accounting. It cannot produce +765k in *additional* payouts without repeated trades and recycling. No present proof exists for any capital-churn schedule that produces that gain. The previous maker fill model estimated 487.4 vs 149 actual fills: do not rely on it.

## Ranked falsification-first mechanisms
Ordering prioritizes experimentally testable economics, not purported competitor attribution.

### 1. Exact same-event relationships / release of wasted inventory — Lane A / #206 / Lane B
**Source:** mathematically exact SIG YES/NO complement or independent mutually exclusive/partition contracts, including our 9,280 potential gross-share release from already held same-race paired NO positions (issue #201). **Executable premise:** accepted official rules, all relevant outcomes including third-party/cancel; noncrossed synchronized full SIG ladders, sufficient depth at worst necessary size, fees, net incremental close-vs-hold value, atomic/partial-leg risk, risk budget. **Capital:** paired legs reserve shares; releasing 9,280 gross shares is NOT automatically 9,280 cash. **Adverse:** unmatched second leg, no depth, third-party wins, source revision. **Kill:** verified worst-leg, post-cost executable economics nonpositive or relationship semantics not exact, versus hold/no-trade and shuffled-pair controls.

### 2. Verifiable quote repricing after fresh independent information — Lane B / #206
**Source:** official result feed or mapped Polymarket event that is demonstrably available before SIG quote response. **Executable premise:** publication and local receipt clocks, accepted EXACT contract match, fresh full depth and asOf book, queue/cancellation/ack latency, event-to-book lag and uncertainty, fillable positive net price edge. **Capital:** order size at actual depth plus pending gross and partial fills. **Adverse:** stale call or corrected ballots, SIG cancels quote before execution, false lag due to receive ordering. **Kill:** advantage vanishes with true decision-time causality, conservative latency/fill haircuts or shuffled-time controls; historical midpoint dislocations not sufficient.

### 3. Selective maker spread capture — Lane C / #220
**Source:** resting SIG spread when queue and information risks are compensated by genuine fills. **Executable premise:** fixed SHADOW quote policy with measured acknowledgements, negative selection, actual fills and 5s/30s/5m/1h post-cost markouts; no predictive maker fill counts treated as fills. **Capital:** inventories accumulate on both sides; share-gross cap, quote reservations and refunds counted. **Adverse:** informed order flow preferentially hits our stale side; cancellations outpace fills. **Kill:** adverse-selection-corrected PnL nonpositive vs no-trade/naive maker, or maker-fill calibration remains grossly optimistic.

### 4. Risk-capital turnover by economically justified inventory release — Lane A / Lane C
**Source:** existing negative-marginal-EV positions crowd out proven profitable opportunities. **Executable premise:** precise held YES/NO signed shares, liquidation at full executable depth, verified trade fees, releasing cap room, positive *future* candidate EV per gross-share-day vs continued hold. **Capital:** capped gross shares fully occupied in prior snapshot; exit may incur losses. **Adverse:** liquidation loses spread, new opportunity fails to fill, released room stays idle. **Kill:** conservative exit-and-redeploy terminal EV below simply holding or gross stays blocked.

### 5. Election result nowcasting with late-count uncertainty — Lane #204 / B / #207
**Source:** official count releases before SIG quotes reflect correct posterior distribution. **Executable premise:** verifiable source publication/revision/receipt, early-vote and late-mail composition, held-out calibration, explicit uncertainty, SIG depth and trading cutoff. **Capital:** correlated race/election concentration with worst-case settlement loss; uncertain pending-market settlement. **Adverse:** red/blue early skew, missing ballots, delayed calls, runoff or correction. **Kill:** economic advantage disappears under OOS tests or 1–60 second delay; policy abstains on missing source/depth. Naive early-precinct lead invalid.

No copy-trader hypothesis is supported by accessible footprint evidence. Do not promote direction models failing PRED-007 / R3 or repeat a midpoint-only dislocation claim.

## Tournament objective model — illustrative, not fitted or executable
The offline simulation included here deliberately postulates **terminal** third-place thresholds of 300k (10%), 700k (25%), 900k (35%), 1.4m (30%). It combines *independent hypothetical* own terminal bankroll distributions and terminal competitor threshold distributions (independence is unrealistic for correlated elections). Tie with threshold is conservatively counted as not qualifying; the real competition splits prizes on ties. None of these probabilities were measured or derived from live depth. Run:

    PYTHONPATH=src python -m predictions_cup.analysis.leaderboard_intel --scenarios data/research/leaderboard_intel_20261012/hypothetical_scenarios.json

| Notional policy, all hypothetical | Expected terminal | 5th percentile | P(top 3) | Economic realism |
|---|---:|---:|---:|---|
| Capital-preserving/economic holds | 120,750 | 80,000 | 0% | Values illustrative, not actual position settlement |
| EV-focused profitable recycling | 500,000 | 100,000 | 14.5% | Huge profitable recycling **unvalidated** |
| Podium-tail concentrated lottery | 300,000 | 0 | 16.8% | 1.25m windfall **not feasible from one static 100k gross-share hold** |

Under stipulated assumptions, maximizing expected bankroll can conflict with maximizing a top-three *probability*: the tail lottery's higher podium chance comes with **lower expected wealth** and a 76% total-loss scenario. This is an existence example, not a recommendation or credible 16.8% forecast. Maximizing controlled turnover is neither objective by itself; if no positive after-cost profit, high turnover worsens fees, latency and exposure. Concentrated settlement exposure may avoid crossing spread but starves recycling and has strongly correlated downside.

**Tournament decision principle:** optimize final settled balance first with explicit scenario-level podium probability and downside; reject capital-consuming trades that have no conservative executable edge. Demand independently verified sizing/depth before considering high-variance arms.

## Exact handoffs and stop rules
- Lane A: executable inventory exit ladders, verified paired settlement, gross shares **and cash** actually unlocked and scenario-specific marginal hold EV.
- Lane B #220: valid 10–20 audited full-depth SIG books + paired PM receive/revision snapshots, gap rates and book freshness; no new LIVE consumer.
- Lane C: maker ACK/fill observation, post-cost economics and calibrated queue/fill uncertainty, with prior overprediction as failure baseline.
- Independent #207: adversarial stale clocks, missing revisions, partial fills, NO complement, correlated loss, outage and close deadline.
- Master #201: observe rather than copy leaderboard; only fund prospects with prospective positive after-cost evidence.

**Research status:** dataset = 25 *rounded web display* rows and 3 precise *prior API issue* rows; cross-snapshot change and 1d/7d numerics NOT_OBSERVABLE; account DMM NOT VERIFIED; current account not freshly polled; validated economic alpha **none**; LIVE orders **zero**; no production changes.


## Master-review closure — 13 October 2026

### What the source **defines** versus what remains unknown

| Quantity | Supported statement | What is NOT independently verified |
|---|---|---|
| Initial tournament bankroll | Each contestant starts with **100,000** SUSQies; one trade required to be ranked. Source: https://sig.thesuper.market/docs/tournaments-and-organizations | Different starting balances for top accounts (do **not** assume) |
| **Terminal official ranking** | Ranking after **all** markets settle is by **final SUSQie balance**; outstanding markets may settle later and ties split prize pools. Sources: https://sig.thesuper.market/docs/tournaments-and-organizations and https://sig.thesuper.market/docs/settlement-and-payouts | Forecast terminal third-place threshold |
| **Interim PnL / value** | Public portfolio shows available balance, **current position value** and daily gains/losses; 2 Oct SIG changelog says admin standings are ranked by account value. Own authorised `PortfolioPnlDto` has `realizedPnl`, `unrealizedPnl`, `totalAccountValue` and `totalHoldingsValue` as separate fields; public leaderboard `LeaderboardEntryDto` only has `pnl`, `roi`, `volume`, `tradesCount`, `winRate` and rank. Sources: https://sig.thesuper.market/docs/portfolio-and-leaderboards and https://sig.thesuper.market/docs/changelog?page=2 and repository `src/predictions_cup/sig/{trading_dto,realtime_models}.py` | **Exact server formula** for interim leaderboard PnL; mark source on sparse books; quantity of top accounts' **realised** or **unrealised** profits. Since held positions have current marks, mark-to-market gains **can plausibly dominate**, but no competitor attribution is possible |
| **ROI and reported volume** | Participant response exposes named numeric fields `roi` and `volume`; descriptive PnL/volume is mathematically computable for nonzero volume | ROI percent-vs-fraction display contract; whether volume is one-sided/two-sided, notional vs shares, includes maker-side entries or netted reversals; realised profitability per volume; filled gross cash turnover. Until documented, do not equate reported volume to account capital churn |
| **Competitor behaviour** | Public aggregate rank, PnL, count, volume and selected profile summary fields | Private orders, inventory, execution timestamps/price/role, strategy, market exposures, queue priority and personal account entitlements: **NOT OBSERVABLE** |

Do not promote conditional `(reported PnL / 100,000) × 100` or `PnL / volume` to the vendor's officially specified ROI or realised execution edge. Avoid using the public site to automate private-profile or website-internal endpoints. Verified winning-share terminal payout is 1 SUSQie/share; cancellations/refunds may differ, and partial settlements mean displayed marks are not final bankroll.

### Precise, zero-extra-request DMM entitlement check

1. Inventory **already-authorised own-account** records, previously retained profile/tier/config responses and signed organiser correspondence for a **directly attributable DMM/account-level designation**. Keep credentials private; record source, date, account matching method and entitlement evidence only. Do not treat a configuration string `DMM` or higher local governor limit as proof of venue entitlement.
2. Check the *existing* participant account UI if it already displays an explicit DMM designation, or any documented API response already in local logs expressly declaring the **account** tier. A rate-limit response/header or absence of HTTP 429 is not proof by itself. No fresh authenticated API reads required for this handoff.
3. If no unambiguous signed-in entitlement exists, request **written confirmation from SIG support/organisers** of whether the specific tournament participant account has DMM status, effective date, read/write limits, execution privileges and any programme obligations. Do not submit private API keys. A public changelog states **standard 100 reads +30 writes/min, designated DMM 600/200**, **shared across keys**, but establishes no rights for *our* account: https://sig.thesuper.market/docs/changelog?page=2.
4. Default all plans to the standard shared budget, no special fees/priority or additional order features. **Do not load test, 429 probe, spawn extra credentials, switch keys, poll website internals or change the LIVE governor** to infer entitlement.

### Bounded permitted snapshots: offline runbook (not deployed)

The existing `OBSERVE-001` competition_context stream is the first source. Research may only consume already-captured, participant-permitted `/api/v1/tournaments/{slug}/leaderboard` JSON. The offline CLI enforces an official endpoint URL *shape*, stamps user-supplied timezone-aware receipt time, retains a SHA256 of input **raw bytes**, persists normalized fields and **refuses to overwrite** output CSV. It cannot attest how raw bytes were originally obtained and cannot prove that the declared source URL is authentic; the capture operator must retain original immutable JSON and its transport/credential-scope provenance outside GitHub when appropriate. The prior three issue #221 rows are **transcriptions**, not full raw official payloads. The top-25 rounded **website** table is not valid `--official-json` input.

Example, **only when a captured file already exists** (no API request is made by this command):

```bash
PYTHONPATH=src python -m predictions_cup.analysis.leaderboard_intel \
  --official-json /safe/read_only/previously_captured_official_all.json \
  --observed-at 2026-10-13T00:00:00Z \
  --source-url 'https://sig.thesuper.market/api/v1/tournaments/ACTUAL_SLUG/leaderboard?period=all&limit=25&offset=0' \
  --csv-out /safe/read_only/leaderboard_all_20261013T000000Z.csv
```

The `--observed-at` timestamp above is a **placeholder**, never reuse it as observed evidence: use the real capture timestamp only. For actual new daily collection, require source-authorised cadence and approved **residual aggregate account budget** first; otherwise **no new capture**. If authorised, cap at one top-25 request per period per day (all/1d/7d only where necessary); log actual UTC receive time, request parameters, response SHA, client SHA, status/429, page/period and total; do not capture hidden profiles; never infer true daily realised PnL from a change in displayed marks. No new SIG API call occurred in this lane.

### Tournament terminal-settlement and competitor-correlation sensitivity

The model in `hypothetical_scenarios.json` is a **discrete hypothetical illustration only** with independent terminal competitor third-place thresholds of 300k (10%), 700k (25%), 900k (35%) and 1.4m (30%). It does **not** estimate likely competitor settlements. Illustrative own "EV-focused" final values are 100k (30%), 500k (40%) and 900k (30%), expected 500k; the extreme unvalidated "tail" values are 0 (76%) and 1.25m (24%), expected 300k. The latter cannot be achieved by simply holding one static 100k-share portfolio, and neither is an executable opportunity.

| Assumed terminal threshold stress | EV-focused P(top-three) | Tail hypothetical P(top-three) |
|---|---:|---:|
| Base arbitrary independent thresholds | **14.5%** | **16.8%** |
| Each terminal competitor threshold shifted +250k SUSQies | **3.0%** | **16.8%** |
| Each shifted +500k | **3.0%** | **8.4%** |

Even the 14.5% base number is not a forecast. Under exactly the **same** EV-focused own-bankroll and competitor-threshold marginal distributions, varying their **joint correlation only** allows podium probabilities ranging from **0% (adverse alignment)** to **35% (favourable alignment)** instead of the independent 14.5%: an adverse coupling assigns the 900k own outcome to 1.4m competitor threshold (30%), 500k to 900k (35%) and 700k (5%), 100k to 700k (20%) and 300k (10%); a favourable coupling assigns 500k to 300k (10%), 900k to 700k (25%) and 900k (5%), 500k to 900k (30%) and 100k to 1.4m (30%). These are *mathematical couplings*, not plausible calibrated election states. They demonstrate why shared national/state election factors and the effect of mass settlement cannot be ignored.

The needed **~38.25m** gross comparable turnover under **assumed** 2% realised net margin is purely `765k / 0.02` arithmetic: not a costed trading plan, not 100k-share-cap cycles, not a fill/depth estimate, and no reason to overtrade. Terminal competition cutoff and our prospective position payoff must eventually be evaluated jointly by risk scenario, with realistic restrictions on total cash, settled payout and capital reuse.

### Owner handoff

See [one-page matrix](LANE_D_FALSIFICATION_HANDOFF_20261013.md): #223/#205 selective maker; #206/#220 venue repricing; #206 hard settlement certificates; #225 inventory recycling; #204 official count shocks; #207 independent execution/economic red team. All five are **INSUFFICIENT EXECUTABLE EVIDENCE** until validated forward. Stop expanding this leaderboard lane unless permitted public information gains discriminatory power.
