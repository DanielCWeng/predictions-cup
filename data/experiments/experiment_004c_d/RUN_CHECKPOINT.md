# EXPERIMENT-004C-D — Run Checkpoint

## Scientific freeze

- Branch: `experiment/004c-d-conditional-response-renewal`
- Astra source commit: `5b274cdc487fa5aceb184689c1e6c776aa9f2df5`
- Astra source SHA-256: `95613edc6acfedd9b614143198366b1b2d62b9f10cbbcb5bd2425588760b6bff`
- Canonical preregistration SHA-256: `9eafa50476ffa3e3d793081ead48e1536c02bed5a8571ca92c981158773b2688`
- Canonical frozen registry SHA-256: `de93ccddafd12729ef7c77875bf8c8bfc6a255cc44efb1bfee5d201c69100e52`
- Frozen preregistration commit retained in branch history; later commits are implementation/provenance corrections only.
- Challenge status remains `004B_SEALED_PRIOR_EXPERIMENT_EXPOSED`; no D hypothesis/result was rewritten after challenge access.

## Validation completed before empirical execution

- Targeted D/replay regression suite passed.
- D-specific test suite reached 15 passing tests after the indexing regression guard.
- Ruff/compile/diff checks passed.
- Real order placement remains disabled.
- D3 remains dormant under the frozen graph-support gate.
- PRE and ACTIVE remain separate; primary horizon remains 30 seconds; multiplicity remains C01-C04 x PRE/ACTIVE with Holm FWER.

## Kaggle execution history

Namespace: `004c_d_conditional_response`

Kernel: `polyleviathan/004c-d-conditional-response-renewal`

1. Early attempt failed at startup with `ModuleNotFoundError: predictions_cup`. This occurred before any empirical event data was loaded. The runner was corrected to load/verify the frozen code bundle before importing the bundled module.
2. Corrected v3 ran for ~18 minutes and then failed before the challenge-evaluation loop while constructing the **discovery-only D1 residual pool**.

Exact v3 failure:

```text
IndexError: index 50015 is out of bounds for axis 0 with size 49505
  discovery_residual_blocks
  values = residual[np.asarray(list(idx), dtype=int)]
```

Cause: `finite_model_frame` retained non-contiguous Pandas row labels; those labels were incorrectly used as NumPy positional indices.

Important scientific-status note: challenge assets had been loaded into memory by v3, but **no challenge hypothesis was evaluated and no challenge p-value/result was computed before this exception**. The exception occurred while constructing discovery residual pools immediately before the challenge evaluation loop.

## Published implementation fix

The remote D branch now contains:

- `fa5efda3a6a4177007ddef3cde4f5e23f37f0954` — reset the filtered discovery residual-pool DataFrame index before NumPy positional indexing.
- `e7f680b994d872c6ee305a5aa372be4c7f9cb7bd` — add a regression guard for that indexing invariant.

No scientific parameter, hypothesis, horizon, graph, null draw count, support gate, promotion rule, or challenge outcome was changed.

## Next deterministic steps

1. Restore EC2 connectivity.
2. Sync the D worktree to the remote D branch tip.
3. Rebuild the frozen Kaggle code bundle with the new implementation commit and runner SHA; keep preregistration/registry/Astra bytes unchanged.
4. Update the private code dataset.
5. Rerun the same Kaggle kernel with the full 9,999-draw null workload.
6. Pull the exact completed kernel version.
7. Verify `kaggle_run_summary.json` and every SHA-256 in `artifact_hashes.json`.
8. Audit D1 conservatively: its implemented common/asynchronous N2 is an outcome-residual block bootstrap rather than a full latent-price replay, so it must not be overstated as causal transmission even if nominally significant.
9. Commit only compact reproducible evidence plus `MASTER_HANDOFF_004C_D.md`; do not merge A/B/C/main.

## Current blocker

At the time of this checkpoint the Remote Desktop EC2 device became offline, preventing Kaggle bundle rebuild/relaunch. Repository-side fixes are already preserved remotely.
