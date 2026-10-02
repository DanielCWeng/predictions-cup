# Issue #81 — Independent verified flatten

issue: #81

starting_main_sha: `4a040a7d309df26098af5e81e96c7f1108d03117`

current_status: `PRIMITIVE_AND_STARTUP_RECOVERY_WIRING_UPDATED`

evidence: Canonical tournament-scoped flatten uses a bounded cancel-all identity and reports success only after an authoritative zero-open-order account snapshot. The CLI is a thin wrapper over that primitive.

remaining_boundary: Startup and graceful-stop recovery wiring is covered by the startup-recover lane. The canonical flatten CLI's separately verified zero-open-order guarantee remains unchanged.

merge_policy: keep issue open for launch composition.
