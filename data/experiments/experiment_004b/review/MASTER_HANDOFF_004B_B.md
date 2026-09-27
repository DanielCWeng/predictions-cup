# 004B-B Dynamics/Stats — MASTER Handoff

- Kaggle v3: COMPLETE; discovery spec SHA `409512367f59fefa861df89c57b96586d30e28d205f60e306b20a5e64935b504`; sealed empirical loads `0`; downloaded artifact hashes match the run summary.
- Directed response matrix: 585,900 unique preregistered cells. Lead/lag audit passes: predictor at `t`, response strictly after `t`, PRE/ACTIVE separated, unsupported cells explicit, no sealed-event contribution.
- Available response cells: price→price 6,254/58,590; depth-imbalance→price 3,351/58,590; liquidity→price 3,351/58,590; BBO/depth activity→price 14,350/58,590 each; fill activity→price 9,350/58,590 each. The imbalance measure is `depth_imbalance_change`, not aggressor-signed OFI.
- Canonical 30s aggregate directed price response exceeds both realistic nulls and survives BH in HUN PRE, Peru PRE and Peru ACTIVE. HUN ACTIVE does not survive either circular-shift or block-permutation nulls.
- Only aggregate 30s `PRICE_STRUCTURE` has empirical null p/q values. Individual directed edges and imbalance/liquidity/activity families are descriptive only and must not be described as surviving FDR.
- HUN vs Peru R1: `INSUFFICIENT_COMPARABLE_TOPOLOGY`; retain `CROSS_DISCOVERY_EVENT_STRUCTURE`, not replication language.
- PRE vs ACTIVE: weak/inconsistent. Peru price→price sign agreement across 30/60/120/300s is 0.486–0.556; effect correlation is approximately -0.276 to +0.046. Hungary has only two comparable ordered edges, so its +/-1 correlations are not robust evidence.
- Tests: targeted pytest PASS; full pytest PASS; `ruff check .` PASS; strict mypy PASS (77 source files); `git diff --check` PASS.
- Review artifacts: `dynamics_stats_audit.json`, `dynamics_stats_findings.md`, `response_audit_summary.csv`, `null_audit_summary.csv`, `stability_audit_summary.csv`.

## MASTER blocker

Frozen design expects 1,000 null draws. Hungary ACTIVE `NULL_A_CIRCULAR_SHIFT` produced only 648/1,000 valid contemporaneous statistics and 833/1,000 valid directed-30s statistics. No rerun or design change was made. MASTER must explicitly disposition this deviation before scientific acceptance.

**BLOCKER: MASTER must disposition the Hungary ACTIVE circular-null valid-draw shortfall before 004B MASTER integration.**
