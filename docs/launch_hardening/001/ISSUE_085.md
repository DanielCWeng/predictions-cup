# Issue #85 — RISK-002 fail-closed refresh

issue: #85

starting_main_sha: `4a040a7d309df26098af5e81e96c7f1108d03117`

current_status: `FIXED_ON_BRANCH_READY_TO_CLOSE_AFTER_MERGE`

evidence: Known authoritative read/reconciliation failures publish a blocked capital state instead of crash-looping the service. Incomplete strategy/group attribution blocks fresh admission. Unknown programming/invariant failures remain loud. Risk-profile version migration is explicit and preserves durable financial/halt state.

tests: RISK-002 suite and benchmark gates in CI.

merge_policy: close only after merge / independent confirmation.
