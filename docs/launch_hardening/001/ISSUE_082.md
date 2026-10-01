# Issue #82 — Duplicate LIVE MAKE instances

issue: #82

starting_main_sha: `4a040a7d309df26098af5e81e96c7f1108d03117`

current_status: `FIXED_ON_BRANCH_READY_TO_CLOSE_AFTER_MERGE`

evidence: LIVE MAKE acquires a non-blocking host-local kernel flock beside the execution journal. A second process is rejected; kernel release on exit/crash makes stale lock-file contents harmless. SHADOW is unaffected.

tests: duplicate acquire is rejected and post-release reacquisition succeeds.

merge_policy: close only after merge / independent confirmation.
