# LANE A — Capital Recycling, 12 Oct 2026 (research-only)

**Source:** SIG REST read-only account snapshot 2026-10-12 21:22:16 UTC. *Not a trade recommendation.* Related #201 / #207.

| Parameter | Observed |
|---|---:|
| Total tournament equity | 100,373.50 SUSQies |
| Cash | 34,458.045 SUSQies |
| Marked position holdings | 65,915.46 SUSQies |
| Authoritative entry basis | 65,573.93 SUSQies |
| Unrealised mark PnL | +341.56 SUSQies |
| Position rows | 61 |
| Sum absolute signed YES shares | 100,000 |
| Net signed YES shares | −39,752 |
| Resting tournament orders | 0 |
| Authoritative fills fetched (complete pagination) | 568 |

The risk cap from the older RISK-002 snapshot was 100,000, so this is *consistent* with no capacity. The research environment does not currently expose an independently validated **current** gross risk cap setting; do not substitute a missing setting with zero or declare a positive free limit.

A full depth-200 read-only sweep found displayed book capacity for all 61 position sizes at their **individual read times**, but only **26** book observations remained within an eight-second read-receipt freshness bound at sweep completion. There was no exchange-native book timestamp, no atomic multi-leg reservation and no guarantee of simultaneous exit proceeds. Aggregating these quantities into executable cash would overstate available liquidity. One-tick adverse slippage and 10-bp fees are stress assumptions, not measured fees.

**NO entry-cost correction:** negative signed YES quantity is held NO; the SIG position `costBasis` is *actual cash paid for NO*, not (1−average price). To exit a YES holding, consume YES bids; to exit a NO holding via complement/cancellation, the equivalent executable NO exit proceeds are (1−YES ask) multiplied by filled shares. Apply full-depth quantities, spread, fees, non-atomic leg and drift penalties. Actual settlement/refund rules must be reviewed independently.

**Promising review groups** (not EV-ranked):
- FL-22, MT-01, VA-01, FL-16: matched NO/NO pairs may release 9,280 gross shares. At earlier 13:37–13:42 UTC snapshots, immediate unwind would sacrifice 140.34 SUSQies versus strict two-party settlement, before costs; this **is not** a current executable quote or unconditional relationship.
- FL-07, AL-02, TX-15, IA-03: doubled directional YES/NO opposite-party holdings, 20,000 gross shares. These are *not hedges*. Their current liquidation value may fall below held settlement EV.
- Other state and race correlated concentrations require election-factor and same-event stress, not an independent Bernoulli portfolio approximation.

**Authoritative venue fills versus journal:** 568 venue fills (dated 1–6 Oct) were returned. 483 API fill IDs were not present in the comparison to the current execution journal's `fill_id` entries. This is a **scope / historical reconciliation question**, not proof of lost fills; the journal has 94 AUTHORITATIVE_FILL and 301 REALTIME_FILL records among other event types. Investigate origins, identity translation and retention while keeping SQLite/WAL untouched.

**No ranking of the top 10 by incremental EV is defensible yet:** the required independent per-market probabilities, exact two-party/third-party settlement certificates, refreshed jointly executable depth, adverse-fill bounds, and validated redeployment economics are missing. Releasing gross inventory without a positive expected settled-value improvement is not alpha. No calibrated podium probability can be inferred merely from the leader's ~942k reported PnL; third-place competitor distributions must be specified explicitly.

**Risk controls:** keep LIVE maker/risk caps/env/journal unchanged, no orders or cancellations, use private inventory data outside this public repository, require #207 independent signoff. To estimate a potential action in a fresh replay, report gross shares freed, after-fee cash, paired orphan-tail risk, sacrificed settlement EV, prospective validated redeployment EV, and 5th-percentile correlated scenario effect. Unverified inputs mean **NOT_RANKABLE** and no promotion.

**Raw read-only data:** retained privately on West at `/home/ec2-user/alpha_lane_a_outputs/snapshot_20261012/` with account snapshot, position ledger JSON/CSV, individually bracketed book observations, review queue, and SHA256 manifest. These files are intentionally not checked into the public repository.
