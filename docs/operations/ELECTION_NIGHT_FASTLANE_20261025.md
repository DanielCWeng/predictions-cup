# Election-night execution fast lane — 25 October 2026

## Deployment scope and operating boundary

Code branch: lane4/api-fastlane-read-write-20261025. This branch does not place orders,
change capital limits, or touch the live runtime until separately deployed. Intended
deployment deadline: before 3 November 2026, followed by a strategy freeze.

This release separates SIG API admissions:
- Read GETs use their own priority queue and host-shared file budget at no more than
  1.6 requests/second with two-token burst (up to 98 in a 60-second window).
- Trade writes and realtime-token POSTs use a separate queue/file budget at no more
  than 0.45 requests/second with two-token burst (up to 29 in 60 seconds).
- HIGH: cancellations, realtime-token recovery, qualified residual taker placements
  and retry with the original execution identity. NORMAL: ordinary maker placements
  and fresh price fallback. BACKGROUND: optional market-depth maintenance.
- Low-priority queue admission leaves capacity for urgent operations. Existing
  risk checks, stale-data guards, durable journal and reconciliation are untouched.

**Live prerequisite:** set PREDICTIONS_CUP_SIG_REST_ACCOUNT_BUDGET_PATH to a path
shared by all SIG API processes on West, e.g. a permissions-restricted local
budget file. Both the .writes sibling and the read budget are created by the
governor. Verify no independently active SIG calls on East or other hosts exceed
the remaining *account-wide* headroom. Flock state is host-local and by itself
does NOT enforce a cross-host aggregate quota. Keep a safety margin below
the venue's published 100 reads / 30 writes per-minute ceilings.

## Portfolio recycling trigger

West's authenticated SIG position/book read at 2026-10-25 21:38 UTC found:

| SIG exchange | Holding | Qty | Account mark | Depth-swept proceeds | Mark minus proceeds |
|---|---|---:|---:|---:|---:|
| 932 / TX-15 Democratic YES | YES | 2,500 | 2,078.58 | 2,063.78 | 14.80 |
| 933 / TX-15 Republican YES | NO | 2,500 | 2,056.03 | 2,050.00 | 6.03 |
| **TX-15 combined** | **Democrat-favoured** | **5,000** | **4,134.61** | **4,113.78** | **20.83** |

These are snapshot estimates from displayed depth, *not executable guarantees*,
true fair values, or recommendations to liquidate at once. The two different
contracts represent correlated exposure to the TX-15 event and must not be
counted as independent diversifying bets. The original portfolio snapshot and
the risk engine have account reconciliation and cap requirements that outrank
this report.

**Decision rule:** stage TX-15 as the first candidate for freeing 5,000 shares
*only when* (a) a vetted replacement has positive conservative incremental EV
greater than the value lost by selling TX-15 plus ~20.83 SUSQies observed
liquidation friction and an execution/safety allowance; (b) refreshed books
still fully cover the proposed sell size; (c) current authoritative signed
holdings and central RISK-002 approve every *strictly inventory-reducing* order;
and (d) there are no unresolved/colliding orders. Reduce 500 shares per order
at most, respecting the live per-order setting. Repeat only after acknowledgement
and reconciliation. Do not assume capital is released by *resting* orders.

The repository central risk module already has
`_verified_strict_inventory_reduction`: when holdings are correctly
reconciled and no orders are pending, a guaranteed reducing order may avoid
double-counting gross while still counting order exposure. **No risk-cap bypass
or numeric limit change is proposed here.** If it fails its checks, no
reduce-only privilege is available.

Alternate low-friction positions from the 21:38 read: NJ-07 Democratic YES
2,500 shares at 2,318.51 proceeds vs 2,314.76 mark; TX-28 Republican NO 1,479
shares at 1,353.29 proceeds vs 1,346.69 mark. These are alternative inventory
candidates, not a reason to sell favourable holds without an opportunity.

## Validation / handoff

Required before LIVE deployment:
1. Exact branch test pass for governor, execution and taker; CI on merged head.
2. Operator inventory verification and refresh of book levels.
3. Confirm East is not competing for more API allowance than reserved.
4. No uncertain or duplicate order operations on startup and shutdown.
5. Deploy once, verify quote/taker health, then freeze ahead of election night.

No live trades or risk-limit changes occurred during this analysis.

CI integration note (25 October): PR #354 restored upstream taker lint and
strict typing before review of this fast-lane PR. The final combined merge
must pass GitHub CI; a prior rerun on the old merge SHA was not sufficient.
