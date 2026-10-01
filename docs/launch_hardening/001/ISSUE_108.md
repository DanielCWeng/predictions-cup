# Issue #108 — Resting quote capital health

issue: #108

starting_main_sha: `4a040a7d309df26098af5e81e96c7f1108d03117`

current_status: `CORE_POLICY_FIXED_KEEP_OPEN_COMPOSED_ACCEPTANCE`

evidence: Invalid/stale capital marks and matching durable halts force withdrawal of resting MAKE risk even when desired quote price/size is unchanged. A fresh account reconciliation HOLD does not cause cancellation churn; account staleness still forces CANCEL.

remaining_boundary: The issue's composed multi-market rehearsal remains an integration-lane acceptance check and is not claimed here.

merge_policy: keep issue open for composed acceptance.
