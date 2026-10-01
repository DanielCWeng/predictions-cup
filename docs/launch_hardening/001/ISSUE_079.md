# Issue #79 — Startup placement replay authority

issue: #79

starting_main_sha: `4a040a7d309df26098af5e81e96c7f1108d03117`

current_status: `FIX_IMPLEMENTED_READY_FOR_REGRADING`

evidence: Journal records UTC creation time for cross-process age checks. Startup recovery holds stale/unproven placement operations UNCERTAIN. Critically, `placement_replay_allowed=None` now means eligibility is not proven and therefore replay is denied; replay requires an explicit predicate returning true.

production_boundary: MakerService explicitly passes a deny predicate for startup placement replay. Cancellation recovery remains available.

tests: positive replay fixture now opts in explicitly; existing stale/startup recovery regressions remain in CI.

merge_policy: keep open until independent re-grading / merge.
