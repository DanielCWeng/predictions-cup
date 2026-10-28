# West staged code — passive capital recycling (28 October 2026)

**Status: source on West ONLY; not tested on West, not deployed, not enabled.**
No shell commands, service restarts, execution orders or risk-limit changes were made by this session.

## Changes staged in shared checkout
- `maker/engine.py`: expiring (5s) and bounded (max 8) dynamic reduction targets.
  Uses ordinary maker quote decisions and the existing independent risk approval.
  YES holdings quote ASK ONLY; NO holdings quote BID ONLY; zero or ambiguous
  signed holdings quote neither side. Immutable static recycle settings remain.
- `maker/residual_live.py`: publish at most the top 3 allocator-approved
  exit exchange IDs through the existing 500ms candidate ranking pass.
  Clear targets when evidence becomes untrusted or candidates disappear.
- `maker/service.py`: connect the allocator and maker engine only if
  `PREDICTIONS_CUP_ROTATION_PASSIVE_RECYCLING_ENABLED=true`.
- `config/settings.py`: default flag **false**.
- `execution/journal.py`: crash-persistent, unique rotation operation claim
  table and `claim_rotation_operation` method, usable by future two-leg adapter.
- `maker/rotation_recovery.py`: controller requires durable claim and exact,
  final attributed fill before authorising a replacement order.
- Tests: `test_make001.py`, `test_rotation_recovery.py`,
  `test_rotation_fail_closed_replay.py`.

## Manual validation (no restart, no orders)
```bash
cd /home/ec2-user/predictions-cup
.venv/bin/python -m pytest -q tests/test_rotation_recovery.py tests/test_rotation_fail_closed_replay.py tests/test_capital_rotation.py tests/test_liquidity_allocator.py tests/test_make001.py
.venv/bin/ruff check --ignore E501,I001 src/predictions_cup/maker/engine.py src/predictions_cup/maker/residual_live.py src/predictions_cup/maker/service.py src/predictions_cup/maker/rotation_recovery.py src/predictions_cup/execution/journal.py src/predictions_cup/config/settings.py tests/test_rotation_recovery.py tests/test_rotation_fail_closed_replay.py tests/test_make001.py
.venv/bin/python -m mypy
```
Do not restart if tests or type-check fail.

## Optional activation (ONLY after clean tests and trusted risk state)
The current unit is `predictions-cup-maker-live.service`. Add
`PREDICTIONS_CUP_ROTATION_PASSIVE_RECYCLING_ENABLED=true` to its **actual**
runtime EnvironmentFile, or use an authorised systemd override with that
Environment setting. Do not assume `deploy/live/live_band.env` is the live
EnvironmentFile. Then the operator may perform a guarded restart themselves,
after fresh authoritative trust and zero open/uncertain orders. Inspect the
normal MAKE gates for `capital_recycle_reduce_only` after loading.

To roll back only the feature, set the flag to false and restart at a safe
account-reconciliation boundary. Static recycling settings remain unaffected.

## Deliberate boundary
This is **passive** exit-side recycling, not automatic aggressive exit and
replacement. `TwoPhaseRotation` still has no live production sender adapter,
and its `live_enabled` default remains false. Its standalone ceiling is
500 shares / 500 SUSQies; central RISK may be stricter. Do not claim completed rotations,
improved realised P&L, or first-leg releases from these changes alone.


## 28 October follow-up after operator resets — mapping safety correction
- Source still present on West checkout branch `deploy/west-market-fifo-20261023`,
  branch commit `aed2f34655c3c269dbb292809495dd845978354d`,
  with separate local edits. Live maker was observed as PID 369603.
- Rotation shortlist previously passed every direct PM token to an allocator whose
  fair-value math requires the PM token's YES to match SIG's YES. Corrected
  `maker/residual_live.py` so only VERIFIED + EXACT + SAME direction mappings
  qualify; COMPLEMENT, NEAR, unverified and missing records cannot nominate
  a passive exit target. Added regression coverage in `tests/test_make001.py`.
- This is a code-only change; no order, command execution, restart, environment
  update, GitHub merge, or live-test claim. Current risk/portfolio cannot be
  established from source inspection. Run local CI/host validation before loading.
- Automatic two-phase rotation still lacks live production adapters. Passive
  feature remains opt-in and is dependent on live residual taker coordinator.
- GitHub PR #358 is independent, draft and does not represent the dirty West
  checkout. CI for that PR is not proof of these West edits.
