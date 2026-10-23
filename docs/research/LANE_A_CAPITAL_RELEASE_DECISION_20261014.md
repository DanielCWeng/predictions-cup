# Lane A — capital-release decision at 2026-10-14 20:04:23 UTC

**Verdict: INSUFFICIENT EVIDENCE — CAPITAL OPERATIONALLY BLOCKED / NO ACTION.**
This is a research gate, **not** an instruction to place, cancel or amend orders.
This report uses *no additional SIG REST requests* and modifies no LIVE service,
config, database or WAL. Master #201, PR #225, safety #207, depth #220.

## Code correctness

- P1 `close_slice()` repair in PR #225: validate both *entire* nonempty book
  ladders, strict bid < ask, finite on-grid prices, decreasing bids, increasing
  asks, positive integral level quantities, and whole-share requested/held
  quantities **before** calculating YES bid or NO complement (1-YES ask)
  proceeds. Invalid, crossed, locked, stale, one-sided or partial books
  never generate `net_cash_if_filled`.
- Targeted synthetic test: **53 passed** on isolated West checkout at exact
  code head `1ac74dbb9c8507de38f51af1f7cff1660ce4ffd3`.
  GitHub Actions run
  [38628553996](https://github.com/DanielCWeng/predictions-cup/actions/runs/38628553996)
  completed **SUCCESS** for that head. A new docs-only commit requires its own
  exact-head CI verification.
- Exit proceeds still mean `DEPTH_ONLY_NOT_RESERVED`, not fill guarantees.
  The research model applies an *assumed* one-tick adverse haircut and 10bp
  fees by default, **not independently confirmed charges or adverse selection**.

## Evidence clocks (never mix historical account with current executable quotes)

| Observation | Authenticated venue account | Active risk limit | Depth | Confidence |
|---|---|---|---|---|
| 12 Oct 21:22:16 UTC | 61 rows; cash 34,458.045; equity 100,373.50; marked 65,915.46; paid basis 65,573.93; absolute signed-YES shares 100,000; zero venue open orders | Not proven in that same read | 61 individual full-depth reads; only 26 under 8s at sweep end | Historical, **non-simultaneous**, not liquidatable today |
| 12 Oct 23:26:05–14 UTC | 61 rows; cash 34,458.045; equity 100,421.14; marked 65,963.09; abs signed shares 100,000; zero venue open orders | Live maker process gross/tournament limits 100,000; open-order cap 50,000 | No refreshed simultaneous books | Historical zero *new gross* headroom, not current proof; internal uncertain reservations unproven |
| 14 Oct 20:04:23 UTC | **No fresh authorised authenticated portfolio/fill/order dataset available** | **Current loaded risk/cap/uncertain reservations not re-attested** | 12 Oct books replayed with 8s TTL: **0/61 fresh** | **NOT CURRENT; BLOCKED** |

Read-only inspection verified the private Lane A snapshots and fill reconciliation on
West retain their 12–13 Oct timestamps; none are a 14 Oct venue account read.
The 14 Oct command was a **purely offline** replay of the 12 Oct
`snapshot.json` and `orderbooks.json` through the corrected source. Its
`as_of` is the date of *invalidating stale liquidity*, **not** an assertion
that positions or account figures were observed that day.

## All-position HOLD / CLOSE / ROTATE decision matrix

At the 14 Oct replay, **61/61** old position rows have:
- `NO_FRESH_COMPLETE_EXECUTABLE_DEPTH`;
- `NO_INDEPENDENT_FAIR_VALUE`;
- `MISSING_CANONICAL_SETTLEMENT_CERTIFICATE`.

**61/61** are `NOT_RANKABLE`. Therefore no position has defensible
current conditional proceeds, exit-limit range, incremental net settled EV,
positive redeployment opportunity, or conservative current cash/headroom
release. The private 61-row ledger and action matrix are at:
`/home/ec2-user/alpha_lane_a_outputs/asof_20261014_blocker/` including
`ledger.csv`, `ledger.json`, `economic_action_evaluations.json`,
`summary.json`, and SHA256 source manifest. Those private inputs and
position-level identifiers are not copied into public GitHub.

**Ranked trade queue: EMPTY (0/5)**. In particular:
- FL-22 / MT-01 / VA-01 / FL-16 NO/NO screening group: historical maximum
  paired *gross* release of 9,280 shares if both legs fill. The earlier
  140.34 SUSQies strict-two-party theoretical unwind disadvantage is dated,
  pre-additional-cost and depends on unverified exact settlement identity.
- FL-07 / AL-02 / TX-15 / IA-03 opposite-party directional doubles:
  historical maximum paired gross release 20,000; **not** hedges.
- Neither group's historical books form a currently defensible quoted limit
  or guarantee an executable paired unwind. No release or EV ranking is
  authorised, including a standalone partially filled leg.

## Historical before/after sensitivity — NOT a current execution proposal

| Scenario using the **12 Oct 100k-share starting risk state** | Gross occupied if *all* stated reductions fill | Cash recovered today | Reinvestable risk headroom today | Incremental settled EV vs hold |
|---|---:|---|---|---|
| HOLD / NO_ACTION | 100,000 historical | 0 from reductions | Unknown today (0 at 12 Oct documented cap/read) | Reference; expected value not independently calibrated |
| Hypothetical matched 9,280 NO/NO two-leg reduction | 90,720 shares, conditional | Unknown | Unknown; reservations and orphan risk remain | Unknown; dated theoretical unwind was disadvantageous before costs |
| Hypothetical 20,000 directional reduction | 80,000 shares, conditional | Unknown | Unknown; live headroom may differ | Unknown; correlated directional payoff may be sacrificed |

The hypothetical alternatives are **mutually non-validated sensitivities**, not
combining into one larger unwind. Partial/orphan fills change correlation and
worst-case portfolio settlement; without canonical two-party/void rules and
jointly fresh depth neither settled-tail downside nor reliable cash can be
quantified. No ACTION conserves the original optionality and does not
pretend to manufacture alpha from gross-cap space.

## Historical venue-fill and risk-accounting provenance

The 12 Oct complete 568-fill authenticated venue history (1–6 Oct) compared
read-only with the event journal gives: 76 exact unique authenticated fill IDs;
9 further venue fills with duplicate-identical authoritative event rows
(counted once each); 104 unique realtime economic/timestamp matches without a
shared native fill ID; **189 fill-level confirmations** in total. The
remaining 312 summary-linked, 66 other order-linked and 1 null-exchange
ACK-linked fills are **379 order-level only**. Zero venue order IDs are
entirely unreferenced in the full journal. This **does not** prove that the
379 are false/missing fills; NO-side identity/semantics and ingest/retention
coverage remain open under #207. No event repairs or WAL writes were made.

At the 12 Oct checkpoint, zero resting venue orders did not prove zero
internally uncertain/reserved risk. Neither cash alone nor a historical gross
cap proves current trading capacity.

## Decision and acceptance

**INSUFFICIENT EVIDENCE — NO ACTION, capital release not approved.**
A separate authorised *fresh* venue/account observation must reconcile cash,
holdings, fills/settlements, open orders and internal pending/reserved exposure
with process cap provenance. Before **any** proposed actual reduction, require
independently reviewed contract settlement/NO-close semantics, per-contract
jointly timely legal-depth and adverse fill costs, fair-value intervals,
correlated worst-case impact, and a separately evidenced positive net
redeployment EV. Independent #207 review and explicit trading authorisation
remain mandatory. Do not wait for #220 merely to analyse historical inventory;
do not mistake historical inventory analysis for current executable liquidity.
