# Issue #77 — Routine venue outcomes

issue: #77

starting_main_sha: `4a040a7d309df26098af5e81e96c7f1108d03117`

current_status: `FIX_IMPLEMENTED_READY_FOR_REGRADING`

evidence: MAKE preserves BUILD-009's distinction between terminal venue errors and uncertain outcomes. Placement/cancel uncertainty keeps economic state conservative; one same-identity retry is bounded. After a second uncertain cancellation, the adapter now immediately invokes the existing account reconciliation path instead of waiting for unrelated account traffic or restart.

safety_boundary: The direct trigger reuses `account_resync()` and `recover_in_session_cancellations()`; no second recovery subsystem was added. If authoritative recovery still cannot resolve the cancellation, UNCERTAIN propagates and the quote remains risk-bearing.

tests: deterministic same-key placement retry plus a two-uncertain-cancel regression proving the reconciliation resolver is invoked exactly once and the journal terminal state is authoritative.

merge_policy: keep issue open until independent re-grading / merge.
