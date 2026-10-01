# Issue #86 — Tournament correlated-loss budget

issue: #86

starting_main_sha: `4a040a7d309df26098af5e81e96c7f1108d03117`

current_status: `FIX_IMPLEMENTED_READY_FOR_REGRADING`

evidence: LIVE configuration now requires an explicit positive `risk_max_tournament_exposure` and rejects a tournament cap larger than the global gross cap. LIVE EXPLORATORY additionally requires an explicit exploratory tournament cap and rejects it above exploratory gross exposure.

scope: Existing exposure-group registry and central risk consumption are retained; no #71 redesign or parallel grouping infrastructure was introduced.

tests: validator regressions cover missing tournament cap, cap above gross, and missing exploratory tournament cap.

merge_policy: keep open until independent re-grading / merge.
