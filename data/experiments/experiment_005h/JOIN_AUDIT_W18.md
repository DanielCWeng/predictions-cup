# EXPERIMENT-005H — Corrected W18 Join Gate

**Disposition: PASS for the active transaction episode; FAIL for passive-row-level sequential inference.**

- Accepted W18 transaction-condition groups: **2,265**
- Exact transaction-hash + active-token links: **2,257 (99.65%)**
- Hash/token ambiguity: **0**
- Exact price+size signature within those links: **1,814**
- Unique hash/token links with price/size representation mismatch: **443**
- Canonical venue print minus Polygon block time: median **-2.228s**; canonical active links sit within a few seconds before block confirmation.

The canonical 005H unit is therefore the **accepted active transaction episode**, anchored by exact transaction hash and active token. Price/size agreement defines a higher-confidence economic stratum for size/depth tests, but does not define identity.

Passive on-chain rows are not promoted to independent observed fills: their observed-trade coverage and reuse are materially worse. Nearest price/size/time matching remains diagnostic only.

Pre-fill state is the last reconstructed V3 state strictly earlier in `(timestamp_received, sequence)` order. Recorder receive timestamps are explicitly normalized to nanoseconds.

B0 opened: **NO**

REAL SIG ORDERS SENT: NO
