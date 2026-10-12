# Historical SIG research PR archive and negative-control index — 2026-10-12

**Status: evidence preservation only; NO LIVE promotion / NO executable E4-E5 claim.**
This index pins exact Git commits and selected file-object SHA identifiers for large, older research PRs, so evidence is not silently lost while cleaning the integration queue. These historical branches are **not merged into main by this index**. A GitHub file SHA below identifies the source object at the pinned branch head, not necessarily a cryptographic hash of its external raw data inputs. Unavailable or absent data hashes must remain explicitly unavailable. Original PR comments and external artifact provenance remain part of the scientific record.

## Reproduction and ownership boundary

- Use the **exact commit hash in each row**, not a moving branch reference. Fetch the listed file from that revision; inspect it *before* interpreting any derived metrics.
- Preserve sealed HOLDOUT data, frozen candidate list, timestamp semantics, input/output manifests, and every negative control. Never rerun a failed result with newly selected parameters.
- Replay estimates, public order-flow association, midpoint forecasts, or conditional all-leg certificates are **not** observed fills, net executable PnL, or proof of own passive-quote fill probability.
- The archive is a research catalogue only. Keep all maker/execution/runtime changes from old branches out of main unless separately extracted, reviewed, and run through normal CI and safety gates.
- Closing an old integration PR leaves its exact links as read-only historical evidence; it does **not** imply its code, results, or unresolved scientific question was integrated or validated.

| PR | Frozen head | Disposition for code | Caution |
|---|---|---|---|
| [#72 MM-REPLAY-001](https://github.com/DanielCWeng/predictions-cup/pull/72) | `23580875f167db344d34a594768104f47d9842e5` | Preserve frozen artifacts; extract replay-only helpers/tests as separate patches if independently useful | 005F strict-as-of replay and PM complement proxy are not actual SIG maker fills; fee-complete net PnL unavailable |
| [#94 EXPERIMENT-005H](https://github.com/DanielCWeng/predictions-cup/pull/94) | `5d37a2f2a7b64685c4a012408fdca53e6bf75307` | Preserve locked protocols, evidence registry, source and outcomes; no bulk merge | Market fill-arrival/aggressor direction `C04/C05` is **not** own-queue fill profitability; historical registry and later commentary require chronology reconciliation |
| [#96 ASTRA forensic](https://github.com/DanielCWeng/predictions-cup/pull/96) | `8717ee581ca201d04e6348fae6406842b410e899` | Refresh 14 finding statuses before extracting historical tests/docs | Fixtures pinned to old branches may intentionally fail; label strict XFAIL vs PASS against latest main |
| [#138 FV-LIVE-001](https://github.com/DanielCWeng/predictions-cup/pull/138) | `8e93e31b8b92e6d4c7044e55809e7fd7cfd3c679` | Negative-control research baseline; no LIVE strategy registration | Assumed full queue priority and fill, excludes fee/latency/capital/settlement cost; quoted gross marks are not trading returns |
| [#175 aggressive replay](https://github.com/DanielCWeng/predictions-cup/pull/175) | `47e8a5346d06e760c362c76b14a6fca1f80f6ed4` | Historical failing calibration, simulator may be extracted separately; **never merge** stale residual-taker runtime edits | Prior chronological reviewer observed **487.4 predicted vs 149 actual fills** (~227.1% overestimate); no known PM-exit net economics |

## MM-REPLAY-001 (#72): exact artifact and attempt distinctions

At the pinned commit:

- [input_manifest.json](https://github.com/DanielCWeng/predictions-cup/blob/23580875f167db344d34a594768104f47d9842e5/data/experiments/mm_replay_001/input_manifest.json) — Git blob `5331dbf94814d115c4d4adbb994fab54e161ebc4`. This file names `timestamp_received` for as-of event time and the expected Polymarket acquisition universe; the manifest's `file_hashes` map is empty, so **do not claim fully verified raw input hashes** from this document.
- [attempt_registry.json](https://github.com/DanielCWeng/predictions-cup/blob/23580875f167db344d34a594768104f47d9842e5/data/experiments/mm_replay_001/attempt_registry.json) — Git blob `37866e49bd5aec3b6bc0580780735a667cf6fcbd`. Attempts 1–2: packaging/import errors; attempt 3: scientifically superseded for a 005F 15-second boundary issue; attempt 4: `CANONICAL_STRICT_ASOF`; attempt 5: `PERFORMANCE_EQUIVALENT_RETRY`. The registry explicitly marks `historical_pm_to_sig_latency_claim=false`, `fee_complete_net_pnl_available=false`, and `real_sig_orders_sent=false`.
- [MASTER_HANDOFF_MM_REPLAY_001.md](https://github.com/DanielCWeng/predictions-cup/blob/23580875f167db344d34a594768104f47d9842e5/data/experiments/mm_replay_001/MASTER_HANDOFF_MM_REPLAY_001.md), frozen protocol and [runner source](https://github.com/DanielCWeng/predictions-cup/blob/23580875f167db344d34a594768104f47d9842e5/scripts/mm_replay_001.py) remain historical research, not current replay authority.

## EXPERIMENT-005H (#94): versioned scientific interpretation

At the pinned commit:

- [BRANCH_SCOPE_FREEZE.json](https://github.com/DanielCWeng/predictions-cup/blob/5d37a2f2a7b64685c4a012408fdca53e6bf75307/data/experiments/experiment_005h/BRANCH_SCOPE_FREEZE.json), blob `2d555676fb428b599bf02f8b2d0a8f33034f9332`.
- [EVIDENCE_REGISTRY.json](https://github.com/DanielCWeng/predictions-cup/blob/5d37a2f2a7b64685c4a012408fdca53e6bf75307/data/experiments/experiment_005h/EVIDENCE_REGISTRY.json), blob `2ca9b148ca0d6d020485a5b291aa06841414edc4`.
- [EXECUTION_LEDGER.json](https://github.com/DanielCWeng/predictions-cup/blob/5d37a2f2a7b64685c4a012408fdca53e6bf75307/data/experiments/experiment_005h/EXECUTION_LEDGER.json), blob `e66f303c4770fc295602095f08a515ceef264400`.
- [HOLDOUT_EVALUATION_PROTOCOL.json](https://github.com/DanielCWeng/predictions-cup/blob/5d37a2f2a7b64685c4a012408fdca53e6bf75307/data/experiments/experiment_005h/HOLDOUT_EVALUATION_PROTOCOL.json), blob `b199f1592f32275fd65d0dacdce91b54a1932e6a`.

**Important apparent chronology gap:** the checked-in registry/ledger records an initial B0 technical error and a *running* resource-bounded technical retry without model scoring, while later PR discussion reports historical B0 C04/C05 survivors. These versions must be reconciled against later exact frozen artifacts and workflow results **before calling the original registry the final B0 result**. No additional scientific rerun or retrospective winner selection is authorised.

## FV-LIVE-001 (#138) and failed aggressive fill replay (#175)

- [FV-LIVE-001 README](https://github.com/DanielCWeng/predictions-cup/blob/8e93e31b8b92e6d4c7044e55809e7fd7cfd3c679/src/predictions_cup/research/fv_live/README.md), blob `b7930d178ec9f7ab15795d7180ec76c0ee507226`. It documents the pre-live calibration cutoff 2026-10-01 16:00 UTC and explicitly assumes SIG realtime prints at/through a resting quote are full fills with queue priority. Markouts exclude fees, rebates, latency, cancellation, capital, settlement.
- [aggressive_sim.py](https://github.com/DanielCWeng/predictions-cup/blob/47e8a5346d06e760c362c76b14a6fca1f80f6ed4/src/predictions_cup/analysis/aggressive_sim.py), blob `fa9298e3c816676a37bb23c00f0b19820e239558`; [#175 reviewer history](https://github.com/DanielCWeng/predictions-cup/pull/175) contains the calibration failure and missing exit marks. Preserve as a negative check against hypothetical queue priority or residual-taking aggression, **not** a strategy candidate.

## Integration status

This index preserves references without copying stale source over the current maker, journal, recovery, or replay implementations. New causal/economic studies should be separately reviewed and tested against `main` and require independently measured prospective quote survival, real depth, fills, fees, risk reservations and held-out day/event uncertainty. The valid economic result can be `NOT MEASURED`; it must never be silently filled by synthetic PnL.
