# Reproducing the ASTRA audit

Audit scope: source review and synthetic fixtures. No real orders, live account queries, production changes, or historical model refits.

The report preserves checkpoint history. Its checkpoint 5 amendments and final synthesis govern current findings: 14 active findings (9 HIGH, 5 MEDIUM), plus ASTRA-015 superseded for updated live ingestion. Expected failures are evidence of violated desired invariants, not passing correctness tests.

## Sources

Repository: https://github.com/DanielCWeng/predictions-cup

| Checkout | Audited revision | Audit tests |
|---|---|---|
| main | e310c2951e7f150c258105703bbd2c0347134354 | test_astra_005f.py, test_astra_live.py, test_astra_accounting.py |
| PR72 MM replay | 348358fc4ccf914b2266a399d245a08de2d856c5 | test_astra_mm.py |
| PR74 live diagnostics | e8c6dcc7e7e15bb8b362213012371e95feb860d0 | test_astra_diag.py |
| PR75 structural certificates | 4aa9ba3d4e47558ce45cbd17881032ac0f573950 | test_astra_struct.py |
| PR76 005F live | dbd0b84497efff026a5594fae7a4ae1d3b54913d | test_astra_005f_live.py |

Initial heads and superseded results are recorded in the report. Main's execution/learner/frozen logic did not change in the #71/#73 additive merges.

## Running

Use isolated worktrees at the pinned revisions. Keep the audit files in one complete audit checkout at `docs/red_team/`: several fixtures intentionally extract original frozen source or reuse repository test builders from that checkout. `test_astra_005f_live.py` additionally loads the PR76 test builders from its current working directory.

Install the repository's declared dependencies using its usual environment workflow. From each target checkout, set `PYTHONPATH` to that checkout's `src`, then run pytest on the corresponding audit file by absolute path. Example in PowerShell:

```powershell
$env:PYTHONPATH='src'
python -m pytest 'C:/path/to/audit-checkout/docs/red_team/test_astra_diag.py' -rx
```

Run with `--runxfail --tb=short` to display the assertions as ordinary failures. Do not collect all seven audit modules against one source checkout: the branch-only APIs are intentionally different. Do not remove xfail markers and then interpret a red suite as a new regression from the audit documents.

Expected audit results across the pinned checkouts: 22 current invariant failures, 1 historical sampled-fallback failure, and 2 passing positive controls (cash/payoff accounting and updated pre-coalescing ingestion). These counts include parameterized train/dev, fit/freeze and holdout cases. ASTRA-001 is established by source/history comparison, not a fixture.

Local environment used Windows, Python 3.13.5, pydantic 2.11.7, numpy 2.2.6, existing pytest/pyarrow and isolated pydantic-settings 2.10.1. This was not an exact locked-environment CI run. Windows-specific fsync failures and CRLF source-hash behavior are documented without claiming Linux failure.

## Package contents and Git history

The deliverable ZIP includes the report, seven audit test files, this README, a patch of audit-only changes relative to audited main, and a Git bundle preserving local checkpoint commits. No production implementation fix was made. No branch was pushed by this audit.

To inspect the bundle in an existing repository containing the audited main history:

```text
git bundle verify ASTRA-audit.bundle
git fetch ASTRA-audit.bundle audit/astra-forensic-red-team:review/astra-forensic-red-team
```

Alternatively apply `ASTRA-audit.patch` to an appropriate clean checkout after reviewing it. Do not apply both the bundle branch and patch to the same checkout.
