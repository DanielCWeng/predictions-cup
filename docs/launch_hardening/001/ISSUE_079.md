# Issue #79 — Startup placement replay authority

issue: #79

starting_main_sha: `4a040a7d309df26098af5e81e96c7f1108d03117`

current_status: `UPDATED_FOR_STARTUP_AUTO_RECOVERY`

evidence: Startup recovery first uses any durable ACK order IDs for authoritative order/fill reconciliation and cancels an order still resting under that journal ownership. A placement without an ACK can only be retried through the original persisted payload and idempotency key. Transient SIG failures receive three attempts with 1s and 2s backoff; unresolved operation IDs and exchange scopes are returned to the maker runtime.

production_boundary: MakerService no longer exits when startup recovery remains unresolved. It blocks only the affected exchanges, represents reconstructable unresolved placement size in the reservation book, and retries the startup blocker set from later account reconciliations. Unacknowledged placements never receive a new idempotency key.

tests: startup OPEN placement recovery/cancel, 503 list fallback, bounded 429 retry, per-exchange runtime blocking, and acknowledged placement recovery-tool fallback are covered by unit tests.

merge_policy: keep open until independent re-grading / merge.
