# SIG CUP COMMAND — operational handoff (15 October 2026)

> **Start here when a new agent/chat takes over.** This document + [master issue #201](https://github.com/DanielCWeng/predictions-cup/issues/201) + [safety issue #207](https://github.com/DanielCWeng/predictions-cup/issues/207) are the current command record. The root `README.md`, `ORCHESTRATOR.md`, and `CURRENT_STATE.md` contain older September status claims; **GitHub current source and independently timestamped host evidence outrank those claims**. Never infer that code merged to `main` is deployed on the LIVE host.

## Mission, clock and operating principle

- **Competition:** maximize final settled SIG SUSQies account value and chances of top 10 / podium, not research volume or short-run mark-to-market.
- **Trading cutoff:** Wednesday **2026-11-04 17:00 UTC**; existing proposed safety cutoff 16:59:55 UTC. Verify implemented cutoff and deployment before use. November 3–4 is the crescendo **not** the planned start of operations.
- **LIVE maker already exists and is active**, subject to safety and capital constraints. Never describe the project as having no live capability.
- **Autonomy:** execute clearly safe, reversible, read-only and source-only work without repeated debate. Economic and safety gates remain binding. Broad permission to manage the project is not evidence that an untested order, liquidation, risk cap increase, restart, or market-data poll is safe.

## East storage and capture — verified 15 Oct 2026 ~08:49 UTC

- **East root expanded successfully from 30 GiB to 80 GiB**: `lsblk` reports 80G NVMe device and `/dev/nvme0n1p1`, mounted XFS root `df -hT /` reports total 80G, used 30G, available **51G**, 37% used. Both East SIG and Polymarket capture services observed `active`. The user approved 30→80 GB EBS expansion and stated completed; expansion was verified, NOT performed by this chat. Do not re-execute resize.
- **East capture still malformed-retry looping on production checkout `4556f5e`**: five-minute read-only sample ~08:45 UTC showed 95 `SigMalformedResponseError` and 3 rate-limit indications. Its deployed `sig/capture.py` sleeps 1s after every `SigApiError`; server continues to run. Malformed 200-response root field/schema not proven; `main` DTO tolerates extra fields but cannot claim this is the only cause.
- **Source-only [PR #237](https://github.com/DanielCWeng/predictions-cup/pull/237) MERGED into main** at `7d3dc753` after exact-head [CI 38710029473](https://github.com/DanielCWeng/predictions-cup/actions/runs/38710029473) SUCCESS. Adds bounded 1–60s error backoff, stop-aware wait, and market DTO forward-compatible fields regression. **NOT DEPLOYED** on East `4556f5e`, so the recovery loop remains unchanged in production. Precise live malformed-response field is not established.
- **Division of responsibility:** East = research and canonical historical SIG/PM capture; West = LIVE strategy/risk/execution plus independently necessary fresh local market-data feeds. Do not disable West capture or supervisor merely by changing ownership labels; verify dependencies, replay and #207 gates before any process changes.

## Active BBO recovery — 15 Oct 2026 (source-only, LIVE unchanged)

- West deployed checkout `fb4f6c66` serial Realtime consumer awaits REST dirty-book reconciliation, causing queue backlog. Two 4096-slot queue overflows in 20 minutes; one 15-minute log window recorded only 3 maker bulk-price GET completions versus 216 individual book GETs. Merged [#198](https://github.com/DanielCWeng/predictions-cup/pull/198) addresses prequeue filtering and async/coalesced refetch but is NOT deployed.
- [DRAFT PR #236](https://github.com/DanielCWeng/predictions-cup/pull/236) head `42a0c912` now also includes a maker-only opt-out from HIGH-priority untracked dirty-book REST requests; the research-capture default remains enabled and tracked-depth recovery continues. BBO refresh timing mismatch also fixed. Exact-head [CI 38710410471](https://github.com/DanielCWeng/predictions-cup/actions/runs/38710410471) pending at edit-time; **do not cite previous `19bc256f` green CI as validation of this new head**. NOT DEPLOYED or promoted; #207 safety remains.
- Deploying #236 at configured 10-second refresh would change nominal maker bulk GET from 4/min to 6/min; combined #198+#236 needs #207 independent host/REST-budget safety assessment and owner production release. NO automatic merge/deployment or service restart. Feed repair may immediately create actual orders.
- Gross remains at 100,000 held shares versus observed 100,000 LIVE cap; no proved fresh economically justified reductions. Do not raise cap or sell from old books.
- Next allowed action: offline independent safety assessment of merged #198 against #207 and #236 exact diff; prepare narrowly bounded deployment acceptance, not another issue/strategy family.

## Hard facts (explicit timestamps)

- **West production checkout:** observed `fb4f6c66` on 2026-10-14; production **not** automatically updated when GitHub merges.
- **GitHub producer recorder:** source #230→#227→#226 merged as **default-OFF** on `main` `bd377fe5`; #232/#233 operational follow-ups are draft. Combined recorder OFF/ON synthetic A/B safety benchmark **NOT PASSED**; do not activate until #207 independent acceptance, approved input route, host safety and operator gate.
- **West authenticated read-only fresh account check, 2026-10-14 ~23:17 UTC:** 61 tournament positions; sum absolute held quantity 100,000, signed quantity −39,752; held mark 65,777.63, cost 65,573.93, unrealized 203.73 SUSQies. Authenticated tournament open orders: **0**, `has_more=false`, reported coverage complete. The previously observed 100,000 gross cap is dated 12 Oct, **not yet revalidated as current risk configuration**. Never infer current free risk headroom solely from these observations.
- **Same-window tournament PnL and leaderboard, 2026-10-14 23:17:47–23:17:54 UTC:** authenticated account value **100,235.65 SUSQies**; holdings **65,777.60** and implied cash **34,458.05** (= account value minus holdings; NOT independent cash ledger); official rank **475 of 1,590**. This refresh consumed 4 total sequential one-attempt authenticated GETs (positions, open orders, PnL, leaderboard), 6 seconds apart, with no API errors. It did **not** obtain a new current exposure-cap setting or live executable SIG books.
- **Prior dated tournament snapshot 12 Oct:** account equity ~100,373.50, cash ~34,458.045, gross 100,000/100,000, 61 positions. Historical 12 Oct books are **not** executable now.
- **Research after-cost alpha:** **0 independently certified positive executable candidates**, NOT proof none exists. Frozen 005I 67.26% movement prediction lacks SIG full-depth economic denominator (#234). Lane C mixed 568 venue fills ≠ 568 maker fills; only 189 fill-level matches and 379 weaker order associations (#223). Lane A 0/5 defensible exit actions (#225).
- **LIVE deployment/safety:** #207 STOP-SHIP for unreviewed changes. Known old main LIVE safety drafts include #202/#213 cutoff, #219 stale startup replay, #218 wire telemetry, #216 maker inventory guard; check exact current merge/deployment state before any action. Do not restart services or change LIVE configs to make tests pass.

## Decision order — act, verify, close

1. **P0 Capital truth & trading health, immediately:** Reconcile *fresh authoritative* account/positions/orders, current risk cap and reservations, current maker intent/ACK/wire behavior, and active health. Use least-intrusive approved read-only sources, stop on SIG 429/health deterioration; shared venue quota includes all existing workers. Identify whether maker is blocked by full gross, stale trusted BBO, or strategy edge. Existing LIVE trading may continue under its existing permits; do not create new orders as a side effect of diagnostics.
2. **P0 Cash/gross opportunity:** Use #225's corrected fail-closed validator and only fresh, legally acquired, causally available prices + settlement rules to rank up to five hold vs close/redeploy candidates. Include real limit fillability, fees, partials, correlated exposures and worst-case risk. No liquidation from stale 12 Oct orderbook.
3. **P1 One candidate with real economic edge:** First exhaust available authentic maker placements/ACK/fills/no-fills/fee/exit records (#223), and frozen 005I (#234) only if valid paired depth exists. Require conservative after-cost size/latency/fill & event-clustered downside vs NO_TRADE. Kill nonviable approaches quickly instead of opening new research families.
4. **P1 Safety/production path concurrently:** Independently adjudicate #207 execution STOP-SHIP and narrowly merge reviewed safety/source fixes. Re-run exact-head CI and host acceptance. Only deploy/promote when economics and independent operational gates are met.
5. **P2 Election readiness:** Prepare verifiable election-count/call-reversal and settlement-semantic EVENT/STRUCT decision protocols now; all actual orders remain bound by risk, cutoff, validated opportunity and venue limits.

## Continuous command and hard boundaries

- **ChatGPT scheduled command cadence:** hourly (platform minimum), only material changes trigger a user alert.
- **GitHub-only watcher:** [PR #235](https://github.com/DanielCWeng/predictions-cup/pull/235) merged into `main` at `92253cd6` after exact-head CI success. The workflow is scheduled every 10 minutes, **best-effort**: it reads only GitHub metadata, writes run summaries, and cannot poll the LIVE host, place trades, create ChatGPT chats, or autonomously modify repository state. First scheduled-run success is NOT YET VERIFIED. A GitHub schedule checks GitHub metadata; **it cannot create a fresh ChatGPT conversation, access private West host without delegated secure credentials, or autonomously certify trade profitability**. GitHub schedules can be delayed.
- **Record decisions in #201** as short dated facts: exact sources and time, executable decision, next owner/action, blocker and cutoff. Do not spam repeating NO-GO comments. For #207 safety use its own issue too.
- **Protected actions:** no unreviewed LIVE orders/cancels, risk/cap/strategy/env change, service deploy/restart, recorder activation, SIG REST polling expansion, or active DB/WAL manipulation. User has authorised limited new read-only account requests; always bound count and shared quota; separate live trading risk checks, economic evidence and independent #207 review still apply. **Never bypass safety to hit a leaderboard target.**
- **Source-only GitHub patches:** small reviewed diffs, fresh CI and repository approvals; preserve draft research branches where ownership not accepted.

## At a chat/context limit

Open a new ChatGPT conversation in this project and paste:

> Take over SIG Cup COMMAND from https://github.com/DanielCWeng/predictions-cup/blob/main/SIG_COMMAND.md and master https://github.com/DanielCWeng/predictions-cup/issues/201. Inspect latest GitHub evidence, exact merged vs LIVE deployed heads, #207 safety, current account/risk and maker economics. Choose the single highest-value currently permitted action, execute it now where safe, and record concise evidence. No research bureaucracy, no fictitious net alpha, no unsafe LIVE promotion. The active competition ends 2026-11-04 17:00 UTC.

**Technical limitation:** a GitHub workflow or ChatGPT scheduled task cannot reliably create/migrate a new user chat autonomously. This README provides the durable manual handoff, and the hourly task resumes work independently of one conversation's context.
