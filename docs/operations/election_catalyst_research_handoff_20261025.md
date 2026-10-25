# Election-night information advantage: source, decision and deployment handoff
**As of 25 October 2026 · Research lane · Deadline 30 Oct · Production freeze 2 Nov · NOT DEPLOYED**

## 1. Watchlist and scope
Machine-readable, ranked **24 SIG EXACT/VERIFIED/SAME contracts across 18 races**: `data/watchlists/election_night_2026_priority_24.json`. Ordered by existing portfolio exposure, event pairing and independently sourced competitiveness (Cook Oct 23 House / Oct 20 Senate). This is a **monitoring-urgency list, not candidate preference, predicted winner, alpha ranking, or proof of tradability**. Snapshot equity ~101,526 SUSQ, extensive tied-up inventory. Current liquidity and cash availability were **not** independently verified for each contract: every watchlist item therefore has `live_depth_verified=false` and `action=MONITOR_ONLY`. Dynamically remove/replace any market that is closed, unmapped or fails live depth/fee/cash checks; no paper fill claims.

## 2. Election-night sources: verified capabilities vs inaccessible feeds

| Source | Credibility/release mechanism | Access verified 25 Oct? | Role |
|---|---|---|---|
| **AP Elections API** https://developer.ap.org/ap-elections-api/ | Licensable/keyed results data and AP race calls, designed for election data integrations | **Public documentation confirmed, authorised API credentials NOT established** | Preferred fast wire adapter only after access / permitted cadence / schema validation |
| **AP public election coverage** https://apnews.com/hub/election-2026 | Dated AP reporting; AP separately counts votes and makes calls | Public webpage available, **no documented low-latency push access established** | Corroboration, not assumption of API-equivalent speed |
| **Official state/county elections offices** (directory https://www.nass.org/can-I-vote) | Source election totals, turnout and corrections; states have differing publication methods | Directory exists; **actual 18-race official endpoints, machine schemas and update times not yet validated** | Primary-source verification; polling results unofficial until canvass/certification |
| **Reuters/public broadcaster result pages** https://www.reuters.com/world/us/ | Reporting / mirrors; may rely on AP/DDHQ/other underlying wires | Public reporting accessible; **authenticated low-latency API not established** | Secondary corroboration only if independent *underlying* reporting |
| **Cook Political Report** https://www.cookpolitical.com/ratings/house-race-ratings | Dated qualitative pre-election competitiveness assessments; not a vote count | Public dated Oct 23 House rating accessible, API/licensing not verified | Watchlist construction, *not* same-night subsecond trading signal |
| **SIG live market feed / Polymarket market feed** | Exchange realtime prices/depth; timestamps distinguish receive vs authoritative book | Existing maker has subscriptions; recent SIG REST limiter 1 req/s and 180s book-refresh configured | Price/economics; Polymarket is already aggregated market consensus, not independent results confirmation |

AP documents how it verifies sources and reports live results:
https://www.ap.org/elections/our-role-in-the-u-s-elections/how-ap-counts-the-vote/
https://www.ap.org/the-definitive-source/behind-the-news/inside-aps-election-night-vote-count/

**Access blocker:** no authenticated independent AP/official-state producer is implemented in this branch. Do not connect unlicensed scrapers or infer official API access. Do not count two sites that reproduce the same AP wire as independent evidence. No source freshness claim without first-observed timestamps.

## 3. Timestamp provenance

Maintain `published_epoch_ms` (source's published time, if reliably provided) and `received_monotonic_ns` (our first receipt). At decision time, separately record receipt→qualification, source publish→receipt, quote snapshot→qualification, qualification→SIG decision, dispatch→venue ACK and opportunity expiry. Do not overwrite stale timestamps on retries, websocket FIFO catch-up or duplicated publications. Clock-domain conversions require explicit source clock, UTC offset and monotonic capture; never compare two hosts' monotonic clocks without synchronisation.

## 4. Lightweight synthetic implementation

- `maker/election_catalyst.py`: in-memory bounded evidence registry (max 128 active event keys), exact SIG exchange allowlist, configured exact source URL host and reporting family, rejection of duplicate/future/stale/conflicting evidence, two different reporting families before qualification, conservative lower-bound BUY / upper-bound SELL, existing `evaluate_taker_economics` with trusted book depth, fee/slippage/latency, cash and reducible inventory. **The probability bounds are externally supplied and must have a separately validated provenance**. The module does NOT convert vote percentages into win probabilities, authenticate news content, query networks, make trade orders or override risk.
- `CatalystWakeHandoff` accepts the actual `MakerRuntimeLoop.notify_sig` keyword-only interface, waking the same maker/taker observer path. It does **not** submit or pre-authorize an order. Existing residual taker still uses trusted PM/SIG fair-value logic and may reject a catalyst even if the independent fair bound differs materially. **That is an intentional safe limitation of this first integration seam.**
- Production remains **off**. There is no licensed live producer, authenticated evidence adapter, venue live book wire or production service registration in this branch. A full direct-priced catalyst order path should be considered only with the Infrastructure Lane and separate RISK-002 review.

## 5. Deterministic validation

West isolated test path `/home/ec2-user/catalyst-lane-isolated/`, independent of the dirty live checkout:
- **26 deterministic tests passed**: duplicate reports, two apparent sources from the same wire family, direction contradiction quarantine, nonoverlapping probability bounds, stale/future publications, forged-looking hostnames, market suspension, BBO/depth/account trust, insufficient cash, no executable edge, SELL bound and 300-event burst / bounded memory.
- One initial burst test correctly found an off-by-one capacity error (129 rather than 128). Fixed, then **26/26 passed**.
- 3,000 synthetic *two-report CPU-only* qualifications on West: **p50 0.035 ms, p95 0.0432 ms, p99 0.0723 ms**. This does not include network/verification/dispatch/ACK.
- No real election night event has occurred. **ZERO independently verified actionable opportunities and ZERO profitable fills are claimed**.

## 6. Production integration checklist by 30 October

1. Acquire permission and stable authorised AP/wire feed OR validate concrete official-state feeds for selected races, with provenance, timestamps, known schema and independent corroborator. Record actual fresh-feed receipt latency; unresolved until credentials/feed approved.
2. Implement a *trusted* provider adapter. Source URL hostname is not content authentication. Authenticate upstream adapters (separate scoped credentials/signatures), archive **only bounded evidence IDs and diagnostic counters**; no continuous historical research capture.
3. Wire `CatalystGate.consider` to a bounded in-process producer callback; build `MarketView` from authoritative fresh SIG snapshots and portfolio/reservations, including market open status and effective available cash. On qualified signal call `CatalystWakeHandoff(runtime.notify_sig).forward`, **without** bypassing current `ExecutionObserver`, RISK-002, execution journal or price reconciliation.
4. For independent catalyst fair probabilities that diverge from Polymarket, design a separately reviewed catalyst-fair valuation adapter; current PM-based residual taker will not act solely on the extra bound. Do NOT silently replace PM fair or write orders without explicit authority.
5. Verify with end-to-end synthetic feed-to-ACK simulations (network/venue doubles), burst sequencing, suspend/uncertain orders, 429/503 REST delays, blocked markets, conflicting updates, and source-clock skew. Benchmark p50/p95/p99 for each stage, not just total.
6. Coordinate runtime changes/restart/reconciliation with Infrastructure Lane; **no live deploy before sign-off**, freeze by Nov 2, and maintain documented rollback / restart state.

**Go/no-go:** until (1) source access/authentication, (3) real in-process adapter, and (5) end-to-end failure tests are completed, this is a tested *research qualification and handoff seam*, **not a production-ready automatic election-night catalyst trading system**.
