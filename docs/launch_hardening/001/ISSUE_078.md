# Issue #78 — Account trust / own-order reconciliation

issue: #78

starting_main_sha: `4a040a7d309df26098af5e81e96c7f1108d03117`

current_status: `CORE_SAFETY_FIXED_KEEP_OPEN_PERFORMANCE_BOUNDARY`

evidence: Refreshed current main now carries the accepted reservation-aware own-order recognition and conservative read-start reconciliation fence. Account distrust is a bounded HOLD only while the last accepted account observation is fresh; once the existing account-age budget expires, policy returns CANCEL and resting quotes are withdrawn.

integration_choice: The older lane-local duplicate local-order registry was dropped during rebase. Current main's ExecutionReservationBook remains the single own-order truth.

remaining_boundary: The grader's full-fill/per-strategy attribution rescan performance concern remains explicitly open. This lane does not call #78 fully closed on that performance dimension.

tests: current-main HOLD-to-stale-CANCEL regressions plus MAKE lifecycle suite.

merge_policy: keep issue open after this patch pending the stated performance boundary / independent grading.
