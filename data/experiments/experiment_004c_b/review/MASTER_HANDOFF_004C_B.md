# MASTER HANDOFF — EXPERIMENT-004C-B

## Verdict

**SOFT_COMPETITIVE_EFFECT_ONLY**

The hard Structural / Competitive-Family Redistribution hypothesis does **not** promote. No hard family satisfies the complete frozen promotion gate.

One preregistered soft family survives:
`COL_FIRST_ROUND_WINNER_34582 | colombia_first_round | PRE_ELECTION | 30s`

Primary statistics for that cell:

- controlled leave-target-out beta: **0.0528136**
- time-null p: **0.0009990**
- BH q: **0.0099900**
- matched-membership p: **0.0009990**
- matched-membership draws: **1000 / 1000**
- controlled rows: **20,550**

The same frozen signal also survives 60s and 120s robustness, but not 300s. Longer horizons do not rescue or redefine the 30s primary.

## What did not promote

The strongest hard PRE cell, the Colombia presidential-winner partial subset, reaches q=0.009990 but beta=0.008615, below the frozen 0.10 meaningful-effect threshold, and has membership p=1.0.

The Colombia runoff ACTIVE hard subset has beta=0.85119 and q=0.009990, but the matched-membership null is unavailable (0 valid draws), so it fails closed.

The Colombia runoff-pair ACTIVE hard subset has beta=0.55879 but q=0.044955 and membership p=0.406593.

There are **zero** complete historical exhaustive families under the frozen 004A.2 usability rule, so there are no valid hard `sum(p)=1` tests and no executable full-basket certificates.
## Scope caution

The surviving soft family is a challenge-family result. Comparable Peru discovery soft families do not survive the frozen 1% FDR plus membership-specificity gates. Member-level betas inside the surviving family are also heterogeneous (-0.00025, 0.12065, 0.05557).

Treat this as a narrow candidate mechanism for a separately preregistered follow-up, not a general cross-election law and not a hard probability-conservation result.

## Provenance

- canonical base: `4e094d13b06d9e558cbc404cc4d472316d7d5bbb`
- family registry SHA-256: `499b22e760b511d11f81707058ed92ce6218bd11428d7dd319436de22811c4dc`
- preregistration SHA-256: `d86df188d4f0dfdb0876b2a7b723750d27438f328f5c6a94100df714b5a80009`
- implementation tree: `44bff6069a9f84a6b3e8b2093ea7f6958a73b438`
- pre-results implementation remote head: `bd7e52dbe72d8d16bfd84f236400afc042e58133`
- Kaggle kernel version: **3**
- runner SHA-256: `608eceacc80050286ad0b182bff356e673cb631425a92aa9b68044e962273349`
- challenge label: `004B_SEALED_PRIOR_EXPERIMENT_EXPOSED`

The remote branch had a malformed sparse-tree runner commit during implementation. That state was preserved on `backup/004c-b-remote-ea385d2`, then repaired append-only. The repaired branch tree was verified equal to the clean EC2 implementation tree before Kaggle execution.

## Review order

1. `FINAL_REPORT_004C_B.md`
2. `../results/kaggle/results_summary.json`
3. `../results/kaggle/family_results.csv`
4. `../results/kaggle/fdr_results.csv`
5. `null_audit_summary.csv`
6. `../results/kaggle/constraint_projection_diagnostics.csv`
7. `../results/kaggle/executable_basket_diagnostics.csv`
8. `../results/kaggle/challenge_provenance.json`
9. `kaggle_external_artifacts.json`

The raw 77,606-row `null_results.csv` is retained in Kaggle version 3 and identified by SHA-256 `97cb22e79eba3efb0181d5f1b05fd8e50d9033c146b8d142ec3ad841cfea37b7`.

Do not merge or self-accept from this handoff. Independent review is still required.
