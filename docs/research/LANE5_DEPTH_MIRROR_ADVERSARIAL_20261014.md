# SIG Lane-5 — Independent depth-mirror safety/performance audit
**Date:** 14 October 2026. **Safety issue:** #207. **Master:** #201.
**Original PR:** #226 at `34ff27d0ae98850621b0a951afbd3d9a34e04b5b` (exact-head CI success, [run 38486369077](https://github.com/DanielCWeng/predictions-cup/actions/runs/38486369077)).
**Corrective PR:** #227 based directly on the original exact head. DRAFT / no deploy / no self-approval.

## Independent disposition on ORIGINAL PR #226

**A — merge disabled-by-default research code: FAIL (as-is).** New opt-in writer has verified source-level loss-accounting and startup/cleanup isolation gaps. The disabled config is inert, but known defects should be corrected and the corrected head's exact CI independently reviewed before merging. A correction is offered by #227 and is not self-approved.

**B — activate inside LIVE maker: BLOCKED / STOP-SHIP.** No real maker loop, cancellation/ACK, West host election-burst or service-restart acceptance has been executed independently. No signed operator change control; the non-native source clock/sequence and research loss mean no causal PAPER readiness. GitHub runner simulations cannot lift this gate.

## Reproducible synthetic experiment

**Benchmark head:** [`5079791c`](https://github.com/DanielCWeng/predictions-cup/commit/5079791c61946ffdf5c61f73b14d943c199633d4).
**Runner:** GitHub-hosted Linux, Python 3.12.15; [successful performance run 38488078505](https://github.com/DanielCWeng/predictions-cup/actions/runs/38488078505) with machine-readable `lane5_baseline.jsonl` and `lane5_burst.jsonl` workflow artifacts.
**Command:**
```bash
python scripts/research/lane5_depth_mirror_perf.py --iterations 3000
python scripts/research/lane5_depth_mirror_perf.py --iterations 1000 --burst
```
No SIG/Polymarket/network/order-sink calls or credentials. Immutable 20 or 200 depth levels are prebuilt. The only maker-like behavior is an **artificial asyncio decision and synthetic scheduled CANCEL/ACK dispatch proxy**; it does not exercise actual MakerRuntimeLoop, SigLiveSink, exchange replies, restarts or real cancels. Book production is unpaced except `paced_*` scenarios (2 ms between events, 500/sec); the burst flag generates ten times as many unpaced events at 200 levels/side. CPU/GC pressure is injected by a concurrent Python thread. Timings include `tracemalloc` instrumentation, and sequential case order is fixed rather than randomized. Results are host/scheduler sensitive. Loop-timer p99.9 is especially unreliable when only tens/hundreds of timers fire. No hard performance limit is justified from these samples.

## Measured results

| Book levels | Case | N | Recorder p50 µs | p95 | p99 | p99.9 | max µs | Loop max ms | Cancel max ms | ACK max ms | Producer rejected | Writer lost | Written |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 20 | disabled | 3000 | 1.6 | 1.711 | 1.87 | 8.293 | 15.757 | 0.966 | 0.214 | 0.217 | — | — | — |
| 20 | enabled | 3000 | 13.805 | 14.808 | 23.275 | 32.675 | 101.88 | 44.576 | 1.216 | 1.220 | 2869 | 0 | 131 |
| 20 | paced_disabled | 3000 | 2.071 | 2.332 | 2.66 | 18.093 | 23.536 | 1.629 | 0.170 | 0.175 | — | — | — |
| 20 | paced_enabled | 3000 | 22.602 | 34.534 | 46.607 | 71.588 | 419.681 | 0.887 | 0.701 | 0.706 | 0 | 0 | 3000 |
| 20 | queue_only | 3000 | 16.415 | 17.203 | 24.881 | 43.111 | 125.426 | 0.949 | 0.196 | 0.203 | — | — | — |
| 20 | saturated | 3000 | 13.513 | 14.249 | 21.63 | 36.929 | 102.054 | 0.958 | 0.134 | 0.136 | 3000 | 0 | 129 |
| 20 | blocked_writer | 3000 | 13.504 | 14.225 | 21.237 | 32.99 | 101.818 | 0.980 | 0.138 | 0.142 | 3000 | 0 | 129 |
| 20 | disk_failure | 3000 | 2.737 | 13.472 | 14.422 | 32.367 | 33.791 | 1.091 | 1.021 | 1.026 | 2872 | 128 | 0 |
| 20 | disabled_pressure | 3000 | 1.615 | 1.812 | 2.106 | 9.967 | 14.5 | 18.024 | 13.598 | 13.606 | — | — | — |
| 20 | queue_pressure | 3000 | 18.355 | 19.451 | 31.478 | 56.533 | 121.357 | 11.435 | 11.914 | 11.919 | — | — | — |
| 20 | blocked_pressure | 3000 | 15.429 | 16.368 | 23.642 | 39.556 | 114.367 | 11.504 | 5.154 | 5.158 | 3000 | 0 | 129 |
| 200 | disabled | 10000 | 1.663 | 2.098 | 3.326 | 7.384 | 12.684 | 0.766 | 0.812 | 0.817 | — | — | — |
| 200 | enabled | 10000 | 14.138 | 15.17 | 21.483 | 29.527 | 645.204 | 7.668 | 9.208 | 9.216 | 9863 | 0 | 137 |
| 200 | paced_disabled | 1000 | 1.996 | 2.192 | 2.376 | 3.004 | 3.4 | 0.533 | 0.100 | 0.103 | — | — | — |
| 200 | paced_enabled | 1000 | 21.948 | 33.899 | 43.095 | 65.391 | 109.536 | 7.261 | 0.170 | 0.175 | 0 | 0 | 1000 |
| 200 | queue_only | 10000 | 16.343 | 17.1 | 22.3 | 31.088 | 710.702 | 2.222 | 0.750 | 0.753 | — | — | — |
| 200 | saturated | 10000 | 13.736 | 14.306 | 19.904 | 30.187 | 398.325 | 2.467 | 0.436 | 0.439 | 10000 | 0 | 129 |
| 200 | blocked_writer | 10000 | 13.678 | 14.185 | 19.121 | 26.283 | 403.035 | 3.720 | 0.443 | 0.446 | 10000 | 0 | 129 |
| 200 | disk_failure | 10000 | 2.763 | 14.521 | 15.477 | 24.654 | 124.023 | 2.280 | 0.902 | 0.908 | 9871 | 129 | 0 |
| 200 | disabled_pressure | 10000 | 1.622 | 1.717 | 1.882 | 8.397 | 10.723 | 13.564 | 13.783 | 13.791 | — | — | — |
| 200 | queue_pressure | 10000 | 18.438 | 19.376 | 26.469 | 38.803 | 418.108 | 18.860 | 15.836 | 15.845 | — | — | — |
| 200 | blocked_pressure | 10000 | 15.592 | 16.719 | 23.659 | 33.244 | 468.791 | 18.408 | 13.277 | 13.282 | 10000 | 0 | 129 |

**Observations from the measurements:**

- At 20 levels/side and 500 events/second, **paced enabled wrote all 3,000** without loss. Recorder callback median was **22.602 µs** versus paced-disabled **2.071 µs**; p99.9 was **71.588 µs** vs **18.093 µs**. At 200 levels/side the paced scenario wrote all 1,000, but is a shorter 1,000-event observation.
- With 20-level **unpaced** recorder, **2,869 of 3,000 producer events were rejected**; with 200-level 10x burst, **9,863 of 10,000 rejected**. Nonblocking puts preserve bounded memory at the expense of research completeness. Label these as *burst*, NOT 'uncongested writer' runs.
- The 20-level unpaced enabled scenario registered **44.576 ms worst event-loop timer lateness** versus **0.966 ms disabled** in that single sequential pass. Separately, the recorder-free CPU/GC pressure control exhibited **18.024 ms** event-loop timer lateness and **13.606 ms** synthetic ACK delay. This shows a generic runtime/host source of tails is plausible, **not** that recorder-specific contention has been excluded. The 200-level CPU/GC controls showed double-digit millisecond worst synthetic delays across disabled, queue-only and blocked-writer variants.
- Exact maker **decision and real cancellation/ACK processing latency are NOT measured**. A non-LIVE maker harness using actual runtime callbacks is still mandatory, with randomized paired repetitions and hard predeclared release thresholds.
- Process CPU and peak RSS are emitted in full JSONL artifacts per case. RSS is process high-water, not isolated net mirror consumption. No West-host baseline/production inference is claimed.

## Verified defects and proposed narrow corrections (#227)

1. **HIGH, evidence integrity:** `_write_loop` dequeued an item, then a `_book_row` or `stream.write` exception escaped to the outer handler without adding that item to `dropped`. Original state could end as accepted=1, written=0, dropped=0, pending=0. Corrective branch counts lost in-flight writes, splits producer rejects and writer losses, and adds deterministic ENOSPC-on-write and serialization regressions.
2. **HIGH, research isolation:** Optional `Thread.start()` could raise and propagate through `MakerService.run`. Corrective branch treats thread allocation/start failure as a research error/disabled state, without failing maker startup.
3. **MEDIUM, lifecycle:** The author's `34ff27d` moved startup inside `try/finally`, but cleanup called `depth_mirror.close()` *after* other possibly failing cleanup awaits. An unrelated exception could skip the research close. Corrective branch protects that close in a nested independent `finally`.
4. **HIGH, operational acceptance gap:** Up to 0.2 seconds of synchronous `depth_mirror.close()` join on the maker event loop, daemon writer alive if blocked; no measured actual kill-switch/cancel/ACK impact or bounded-resource host acceptance. **Unresolved activation blocker**, not a demonstrated production delay.
5. **MEDIUM, research completeness:** 128-slot bounded queue and 32 MiB per-process cap can discard most burst observations; publishing accurate denominators and declaring gaps invalid is necessary. No data is trusted for causal cross-venue PAPER.
6. **MEDIUM, path policy:** Leaf symlink/existing-file exclusive creation is enforced; absolute path and `cwd/data` guards exist. No claim of attacker-resistant parent-directory-symlink handling or TOCTOU safety. Restrict deployment to an operator-controlled, isolated research directory; do not enable until explicitly reviewed.
7. **LOW, provenance:** REST snapshots lack source-native exchange timestamps/revisions. Fields remain null/false and `trusted_for_paired_paper=false`; correct conservatism but it prevents making fill, lead-lag or execution claims.

## Remaining tests / promotion gates

- Re-run exact-head Ruff, mypy and full CI on #227 and inspect all failures; passing smoke tests alone cannot override this assessment.
- Observe actual non-LIVE MakerRuntimeLoop, cancel and ACK processing and watchdog latency with mirror disabled/on, stable writer and full queue, blocked I/O/ENOSPC, CPU/GC and 10x bursts; randomize paired replicates and report p50/p95/p99/p99.9/worst + uncertainty with exact setup.
- Verify lifecycle forced teardown under injected startup failure, signal cancellation, restarts, symlinks and destination changes, disk quota, crash/truncation, and repair objective.
- Test source trust against stale generation and identity mismatch, confirm same REST read count, and reconcile exact attempt/accepted/written/rejected/lost denominators.
- Require independent #207 review of corrective PR, and separate explicit operator ticket/change window/rollback plan before enabling any LIVE runtime setting. No service restart, deployment, live order or production data mutation was performed by Lane-5.

**Do not merge or activate on the strength of this report alone.**
