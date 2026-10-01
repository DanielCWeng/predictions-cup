# Issue #83 — Launch liveness / REST budget / FV freshness

issue: #83

starting_main_sha: `4a040a7d309df26098af5e81e96c7f1108d03117`

current_status: `FIXED_ON_BRANCH_READY_TO_CLOSE_AFTER_MERGE`

evidence: LIVE MAKE requires an explicit tracked SIG universe capped at 20 exchanges, filters mapping/source/token fan-out to that set, and avoids full-universe launch pressure. Direct Polymarket fair-value provenance remains source-true while websocket receive liveness is carried separately for freshness admission.

tests: launch-universe and fair-value freshness/provenance regressions plus MAKE/CAPTURE CI.

merge_policy: close only after merge / independent confirmation.
