# Production-only research capture mode — 25 October 2026

SIG Cup ends 4 November 2026. The live trading process must not compete with
historical alpha research collectors. This change implements a narrow, reversible
code-level switch, **not a data-retention or backup operation**.

## Control

`PREDICTIONS_CUP_RESEARCH_COLLECTION_ENABLED=false` (default) disables
historical research recording. Explicitly set `true` only for offline/research
capture runs.

### When disabled

* The dedicated `python -m predictions_cup.sig.capture` and
  `python -m predictions_cup.external.polymarket.recorder` processes return
  successfully **before opening their capture databases, Parquet stores, REST
  connections or WebSockets**. Systemd `Restart=on-failure` should not revive
  successfully exited services. SIG `--list-tournaments` and Polymarket
  `--smoke-test` remain explicit non-capture operator tools.
* The maker retains **its own** live SIG state engine and Polymarket CLOB
  order books, account/position reconciliation, RISK-002 storage, BUILD-009
  execution journal and market-making/taker logic. Its operational SIG recorder
  was already a no-op. It now avoids allocating the separate Parquet capture
  recorder, OBSERVE-001 background thread and queue, periodic competition
  context HTTP sampler, optional depth research mirror and shadow research
  runtime. The disabled observation emitter returns without buffering.
* The supervisor still monitors **maker, risk, execution, system and clock**.
  It omits research-only capture/shadow/OBSERVE source probes and removes SIG
  and Polymarket capture units from *both* explicitly configured and default
  expected-service/restart allowlists. It has no historical Parquet roots to
  prune when production-only mode is active.

### When explicitly enabled

Research capture and supervisor's research expectations operate as before
(subject to each existing service's own configuration). This restores offline
collection without removing any historical artifacts.

## Host rollout requirements (not performed by this code PR)

**The already-running Python processes do not change behavior until restarted.**
Unit `After=` is only ordering, not a maker dependency. Do not restart the
maker just to disable research without a coordinated LIVE order/account recovery
window. Do not bypass host service-policy restrictions.

When authorised:

1. Confirm clean source revision, no unresolved BUILD-009 orders, authoritative
   account/risk state and rollback plan. Preserve existing historical data,
   snapshots, cold archives and credentials.
2. Deploy **supervisor and both capture entrypoints** as one coordinated
   release. Set the master switch false explicitly in each process environment
   to avoid configuration ambiguity. Ensure supervisor will not revive the two
   former research services, then gracefully stop/disable them via authorised
   service control. Do **not** touch maker, taker, live feeds or risk units here.
3. Deploy the maker's separate optional research suppression only in an
   approved trading-safe update. This requires normal recovery interlocks and
   ordinary load and venue acknowledgement checks before resuming quotes.
4. Verify service process exits, no new research files/SQL writes, CPU PSI and
   available RAM before/after, SIG/PM live maker feed freshness, risk and account
   trust, cancellation/ack/fill ordering and journal continuity. If a gate
   degrades, roll back the code/config, not historical storage.
5. East research collectors also exit on startup under the master flag.
   Stopping its instance or changing its volumes is **out of scope** of this PR.

## Offline tests

Run using a disposable checkout/worktree and existing dependencies:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q -p no:cacheprovider \
  tests/test_production_only_research_policy.py \
  tests/test_sig_depth_mirror_constructor_integration.py \
  tests/test_supervisor001.py
```

The tests use synthetic temporary files, no live exchange requests, and no
operational historical databases. Current production services remain unchanged
until a separate authorised deployment.
