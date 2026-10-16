# SIG Alpha 2 — Structural Mispricing: independent gate verdict (2026-10-16)

**Command:** #201 · **Lane:** #206 · **Status:** **DATA_BLOCKED / KILL programme allocation** · **Authority:** read-only research, no LIVE activation.

## Decision

**Do not allocate gross exposure, deploy a scanner, or ask #207 for a trading exception.** An independently rerun 50-pair native-SIG full-depth screen shows repeated *conditional* normal-settlement NO(D)+NO(R) cost-floor discrepancies. Only VA-01 reaches the nominal **50 SUSQies per 5,000 gross shares** hurdle **before** costs. The best observed gross is **54.625 SUSQies per two 2,500-share legs**; this leaves just **4.625 SUSQies** (0.185 cents per paired share; 0.0925 cents per gross share) for all additional trading costs, price arrival uncertainty, funding opportunity cost and asymmetric execution risk. No conservative net, atomic/fill, exact settlement, or spare-capital certificate exists. The evidence fails the precommitted keep-alive rule; stop this lane rather than re-fit thresholds.

## Reproducible independent rerun

Re-ran the unchanged pre-existing exploratory script, read-only, on an authorised West research host on 2026-10-16. It only reads repository mapping JSON and pre-existing native SIG websocket Parquet; it does **not** call SIG or PM endpoints, deploy, open orders, write a production database, or change risk.

- Original West script: `/tmp/sig_DR_NO_NO_50_exact_2500_20261016.py`
- Original SHA-256: `a36f3e13e400d6eaf29d00d89342a32697b0f40f5e9dafc3d2ffa6bf2d4d7860`
- Verbatim source snapshot archived: `scripts/research/sig_dr_no_no_50_exact_2500_20261016.py` on this research branch. Note: the original script's legacy docstring and two output counter names say *20-lot*; the executable `no_buy_cost(states*,2500)` shows both legs are in fact **2,500** NO shares. Do not misreport the size.
- Input mapping: `data/mappings/sig_polymarket_2026.json`, accepted `EXACT` SIG→PM mappings **on each leg separately**. This does **not** certify a D-versus-R settlement relationship.
- Input depth: West `data/sig_research/raw_events/2026/10/16/15/*.parquet`; underlying Parquet is host-local and **not** reproduced/committed by this source archive. Reproduction requires controlled access to the original capture. No immutable capture-file SHA manifest was available in this review.
- Command: `/home/ec2-user/predictions-cup/.venv/bin/python /tmp/sig_DR_NO_NO_50_exact_2500_20261016.py`
- Exit: 0; elapsed 41.75 seconds. Output matched the earlier #206 descriptive counts.

| Frozen UTC window | Full-depth paired 2,500-lot samples | Raw positive | Raw >=1c/pair | Max raw cents/pair | Max gross SUSQies at 2,500 pairs |
|---|---:|---:|---:|---:|---:|
| 15:30–15:37 (discovery) | 1,044 | 440 | 182 | 2.025 | 50.625 |
| 15:45–15:52 (later validation) | 872 | 381 | 136 | 2.185 | 54.625 |

**VA-01 House** is the one later-window race that reaches 50 SUSQies *gross*: 27 ten-second buckets, 2.006–2.185 cents/pair (50.15–54.625 SUSQies gross). In the earlier window VA-01 had 34 buckets at 1.526–2.025 cents/pair. Later-window next-best examples: NH-01 1.796c (44.90 gross); MI-07 1.578c (39.45 gross); IA-01 1.310c (32.75 gross). These are serial observations on persistent book states, NOT separately fillable orders. 50 title-pairs / 100 SIG exchange IDs were screened; the original script reported one target-topic/epoch revision gap in the later window. It applies topic↔market identity checks, apparent non-crossed whole-share books, native source age <=5s, paired receive lag <=2s and 50% displayed-bid per-level haircut, but is **not** a complete cross-epoch/source-continuity or arrive-and-fill validation.

The historical held NO/NO package previously had a minimum normal-settlement payout around **3.89 SUSQies below original cost**. It is a different sunk inventory question, not new-trade alpha.

## Settlement identity — conditional mathematical floor, NOT individual-rule proof

The official [Markets & Trading guide](https://sig.thesuper.market/docs/markets-and-trading) confirms selling unheld YES at price `p` corresponds to buying NO at `1-p`. The official [Settlement & Payouts guide](https://sig.thesuper.market/docs/settlement-and-payouts) confirms a normal winning share pays 1 SUSQie and a losing share 0; cancelled/N/A positions instead refund refundable held cost. The [official competition rules](https://predictionscup.com/rules/) §3 additionally permit Sponsor to change market rules, outcome, or resolution, including after the published resolution date, and require reading each market's specific **Info** rules and source.

If, *and only if*, D and R winner predicates resolve on the same seat, are mutually exclusive, each has normal binary settlement, and neither is voided/redefined, then `NO(D) + NO(R)` pays at least 1 per paired share (2 if neither D nor R wins). An out-of-sync cancellation breaks that payout floor: if one leg is refunded its cost and the other NO loses, the resulting package loses the entire purchase cost of the losing leg (plus any costs). If both legs are refunded, the gross predicted profit disappears. There is no signed contract-specific rule/void-coupling record for **VA-01** or the other quote candidates. Two accepted EXACT SIG↔PM crosswalks and identical-looking race names do not fill that evidentiary gap. **Settlement-semantic gate: FAIL / unverified**.

## Whole-share, depth and cost hurdle

The screen walks native YES bids (NO purchase complements) at integer 2,500 shares **per leg**, and consumes no more than 50% of each displayed level. This gives a conservative conditional quote-size calculation, **not** verified execution. It has no order-ACK, entry-arrival book, queue/competition, cancels, actual fee assessment, independently matched fill sample, explicit terminal/cancel states or dated depth hash across source reconnects.

At the highest quote-derived discrepancy:

- Pair count `n=2,500`; gross order demand `2n=5,000` shares.
- Hypothetical all-filled total NO premium `C=2,500 - 54.625 = 2,445.375 SUSQies`.
- Normal-state floor profit before incremental costs `2,500-C=54.625 SUSQies`.
- Fixed programme hurdle `H=50`; required **all-in** cost + conservative execution-risk adjustment `<=54.625-H=4.625 SUSQies` per full bundle.
- That allowance is **0.00185 SUSQie = 0.185 cents per pair** and **0.000925 SUSQie = 0.0925 cents per gross share**. A nominal 0.2-cent/pair cost alone drops the best screen to 49.625 SUSQies, below H.
- Any unmatched one-leg order removes the theoretical floor. One NO leg can settle at 0; its premium becomes an unhedged loss, potentially orders of magnitude larger than 4.625 SUSQies. The two legs together cost 2,445.375 in the best scenario, so at least one alone costs **at least 1,222.6875 SUSQies**. This is a bound, not a prediction of which leg or how often it would be orphaned.

The repository `src/predictions_cup/sig/trading_client.py` supports `POST /orders/multi-leg`, but the DTO's per-leg `results[index].ok` and uncertain execution transport make it unsafe to *assume* proven all-or-none matching and resolution of timeouts. No real prospective fill or independent official atomicity guarantee was examined. Do not convert 27 persistent quotes into 27 realised profit opportunities. **Net-capacity/orphan gate: FAIL / unverified**.

## Capital, permissions, #207 safety and alternate alpha

The last authenticated account observation cited on #207 at **2026-10-16 15:49 UTC** had **100,000 / 100,000 gross exposure**, zero available incremental gross, zero open/uncertain order exposure and 61 market exposures. It is not a substitute for a newer signed account. The existing `RISK-002` evaluator conservatively denies positive new gross from that state. Draft #241 is an inert mathematical proposal, not permission to bypass the cap. No risk changes in this lane.

Independent #207 red-team review has **not** signed off the settlement identities, operational recorder/reconnect provenance, measured real order ACK/cancel economics, true multi-leg atomicity/orphan handling, or any LIVE change. Merged research-only #210 certificates are scoped to verified external official-result events: do not spoof that event type to validate static D/R quote disparities. **Safety gate: FAIL / blocked**.

Previously posted **BUY_YES and BUY_NO PM↔SIG convergence** studies across disjoint 16-Oct 14:35–14:46 and 14:55–15:04 windows had *negative* short-horizon SIG quote exit markouts (and no positive matched exits); they should remain non-promotable. No threshold tuning or synthetic fixture can repair that empirical result.

## Required conditions to reopen (all needed; no deadline extension for this research lane)

1. Independent authenticated rule text, market/exchange IDs and source/version signatures for the precise D/R seat pair; explicit treatment of third-party winner, recount, runoff, administrator override and one/both N/A or cancellation. Reviewed by #207, not inferred from titles or accepted SIG→PM crosswalk.
2. Prospectively causally verifiable native full-depth source including reconnect epochs, revisions, local receive and venue as-of clocks; 2,500 whole shares on **each** side at actual arrival with realistic competition/latency; meaningful non-overlapping opportunities, not repeated book buckets.
3. Official fee accounting and a worst-case **partial/cancel/timeout** fill recovery or venue-guaranteed atomic AON mechanism; defensible downside and capital-reservation accounting on both legs. A single unbounded orphan loss invalidates conservative >50 evidence.
4. A *fresh* authenticated signed account giving lawful gross headroom without changing the existing cap or execution controls; and a tested opportunity with **>=50 SUSQies conservative net per 5,000 shares** after all economic stresses, certified by independent #207. Stop if any gate missing.

**Action now:** archive this result and close this lane's programme allocation. Reallocate research capacity toward #204 timestamped election-result advantage and the independent capital/recycling investigation. A future distinct, documented qualifying observation can open a new narrowly scoped proposal; no continuing Alpha-2 scanner exploration, synthetic stress proliferation, or threshold refitting is justified now.

**CI:** No new tests were run for the archived reproduction-only snapshot, and no new code execution gates are claimed. Pre-existing SHADOW certificate CI success on #210 is **not** an economic or operational pass. No LIVE orders, cancellations, API requests, deployment, service restart, env/cap modifications, or shared maker/risk/execution changes by this review.
