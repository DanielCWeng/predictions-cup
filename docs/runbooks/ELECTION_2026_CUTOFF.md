# SIG 2026 Cup — hard order-submission cutoff

**Scope:** Competition-specific fail-closed placement control. Not an alpha strategy.

The [official SIG competition rules](https://predictionscup.com/rules/) state that **all trades must be received before noon US Eastern on Wednesday 4 November 2026**. US Eastern is on standard time that day. The absolute deadline is **2026-11-04 17:00:00 UTC**.

- The code stops **all new placement wire attempts from 16:59:55 UTC**, five seconds before the official deadline. This is a local safety buffer, **not** a guarantee that an earlier request reaches SIG.
- The LIVE sink checks before creating a new journaled placement; the SIG write client checks again **after the REST-governor wait, immediately before each network attempt**, including best-effort batches, atomic multi-leg orders, original-key retries, and recovery re-dispatch.
- If any earlier request may already have reached SIG, crossing the cutoff raises **UNCERTAIN**; retain the original idempotency key and reconcile. It must never turn into a new-key retry.
- **Read-only reconciliation and cancellation remain available.** The cut-off check exempts the cancellation endpoints.
- System clock and NTP/chrony synchronisation must be validated before deployment; timezone-naive clocks fail closed. A clock reporting a falsely early time cannot be detected solely by this guard.

## Human gates

This PR changes code only. It neither deploys, restarts a process, modifies RISK-002, nor enables LIVE trading. Before any deployment, review the source/server clock health, the exact tournament identity, existing live tests, open order recovery paths, and the SIG rulebook.

This 2026 deadline is intentionally **fixed**. Future competitions require a reviewed change to their deadline before this write client can place orders. Do not remove the guard to restore trading.

## Validation

`pytest -q tests/test_competition_deadline.py` should cover cutoff boundaries, timezone errors, REST-governor wait crossing cutoff with zero transmissions, blocked same-key retries classified as UNCERTAIN, and continuing cancellation capability. Run the full CI suite and LIVE-sink regression tests before promotion.
