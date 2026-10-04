# ASTRA forensic red-team audit

Last checkpoint: 2026-10-01 — checkpoint 6, bounded audit synthesis
Current SHA (audited main): e310c2951e7f150c258105703bbd2c0347134354 (initial main ee78e6095fd8d4a8953e5ba990cd970292be8fe8)
Working branch: audit/astra-forensic-red-team
Areas completed: bounded source/fixture review and synthesis of A–M; scope limits below
Area in progress: none within this bounded audit; empirical/host validation remains outstanding
Material findings so far: 15 historical entries; 14 current findings; ASTRA-015 superseded for live production
Current Critical: 0 | High: 9 | Medium: 5 | Low: 0

This is a rolling adversarial audit, not launch acceptance. Unreviewed areas remain explicitly unreviewed. No real SIG orders may be sent. Preserve negative research and frozen hypotheses. Findings will be amended rather than erased.

## Coverage
- A. canonical contracts [COMPLETE] bounded source/history review; see final coverage limitations
- B. time semantics [COMPLETE] targeted adversarial paths and fixtures
- C. mapping [COMPLETE] bounded runtime-transform review; independent settlement adjudication NOT_YET_REVIEWED
- D. capture/observe [COMPLETE] schema/clock boundaries and selected tests; intended-host durability NOT_YET_REVIEWED
- E. shadow/live-learn [COMPLETE] targeted source, baseline tests and hostile fixtures
- F. MM replay [COMPLETE] existing branch only; no full historical empirical rerun
- G. live diagnostics [COMPLETE] source and helper fixtures; no live capture corpus
- H. maker/execution [COMPLETE] targeted economic/reconciliation boundaries
- I. risk/P&L [COMPLETE] independent arithmetic oracle and source contracts; live account reconciliation NOT_YET_REVIEWED
- J. structural scanner [COMPLETE] identity/payoff/depth counterexamples
- K. 005F [COMPLETE] frozen scripts/runtime/live ingestion; no artifact reconstruction or refit
- L. cross-module consistency [COMPLETE] targeted identity/time/economic boundaries
- M. statistical/economic conclusions [COMPLETE] source-level falsification and evidence classification; no empirical rerun

## Checkpoint trail
### Initialization
Cloned full Git history and all advertised branch heads. Canonical main pinned above. CURRENT_STATE.md claims no accepted execution capability; actual code and merge history will determine whether this prose remains accurate. Initial combined canonical-document read exceeded output limit; individual targeted reads are required before claiming coverage.

## Findings
Initialization status was: none confirmed yet. Superseded by the findings below.

### Checkpoint 1 — canonical state and LIVE-DIAG inspection
Pinned PR heads: #72 MM f959b0e152a4a8233d0b2b664d372539f44cb65e; #74 DIAG 6f46f5cc0a401f9285a4e10600ffb628493340dc; #75 STRUCT 32dd198c00cce77175e64a57a235116fd581383e; #76 005F 197038bb0338d6998445eccf6b2e7491f2c20716. Reviewed GitHub PR lists and review/comment endpoints for #50/#56/#62/#67/#69/#70/#72/#74/#75/#76. Only #50 returned formal review bodies; its original execution findings were corrected before acceptance and must not be re-reported as current defects.

### ASTRA-001 — obsolete canonical capability contract
Severity: MEDIUM | Status: CONFIRMED
Components: canonical control documents / execution rollout.
Files/functions: CURRENT_STATE.md (Trading capability, BUILD-009 sections); DATA_CONTRACTS.md (Strategy / execution boundary); ARCHITECTURE.md; actual execution/live.py, risk/capital.py, live_learn/runtime.py.
Claim: main has no order submission, maker, risk or autonomous shadow capability; #50 is still draft.
Actual: GitHub #50 merged 29 September; #56/#62/#67/#69/#70 are merged. Pinned main contains their implementations.
Counterexample: inspect main ee78e60 and the GitHub merge timestamps alongside CURRENT_STATE.md.
Economic consequence: operators/reviewers can approve deployment under a false understanding of enabled capability and miss required accounting/provider contracts.
Confidence: high.
Smallest remediation: reconcile control docs against exact main capabilities and distinguish implemented code, accepted tests and deployment/live authorization.
Tests/evidence: git history and GitHub PR state; no claim that mere presence of code enables live trading automatically.

### ASTRA-002 — economic-price deduplication revives stale depth/trust
Severity: HIGH | Status: CONFIRMED (static path; executable fixture pending)
Components: LIVE-DIAG lead/lag and snapback, PR #74.
Files/functions: src/predictions_cup/analysis/live_diag.py::_economic, _load_quotes, analyze_lead_lag.
Claim: executable opportunities use trustworthy observable-as-of quotes and depth.
Actual: _economic removes untrusted rows and all unchanged-price rows, including depth changes; as-of then searches this compressed history without an age limit. _load_quotes also removes invalid PM rows and does not retain trust invalidation events.
Counterexample: a trusted ask 0.51 with depth 10 is followed by an unchanged ask with zero depth or an invalidation. A PM impulse to 0.55 later still sees the old executable depth; a much later SIG change supplies the response needed to classify a candidate.
Economic consequence: stale/unavailable liquidity manufactures apparently executable PM leads.
Confidence: high.
Smallest remediation: retain full state transitions for eligibility/depth/freshness and compress only a separate price-innovation series; invalidate across feed gaps and require bounded age at trigger and dispatch.
Tests/evidence: counterexample fixture to follow; note the capture loader may return no depth in current production schema, which makes some paths fail closed rather than expose this latent helper defect.

### ASTRA-003 — latency test changes the trade after overshoot
Severity: HIGH | Status: CONFIRMED (static path; executable fixture pending)
Components: LIVE-DIAG PR #74.
Files/functions: analysis/live_diag.py::_active_edge, analyze_lead_lag.
Claim: original executable edge survives declared latency.
Actual: _active_edge independently chooses BUY/SELL at trigger and latency time. The delayed check discards delayed depth and reuses trigger depth; no fee input exists.
Counterexample: external FV 0.55, initial SIG 0.49/0.51 implies BUY with +0.04. After latency SIG 0.59/0.61 makes that BUY -0.06; helper switches to SELL and reports +0.04/MONETIZABLE_CANDIDATE. Similarly, disappearing delayed depth is ignored.
Economic consequence: TOO_FAST_TO_MONETIZE/adverse selection can be labeled monetizable.
Confidence: high.
Smallest remediation: freeze side and size at trigger, price that exact action against delayed full depth, apply fees/rounding, and distinguish post-latency newly discovered opposite-side opportunities.
Tests/evidence: deterministic overshoot fixture to follow.

### ASTRA-004 — uncaptured horizons are scored as observed snapback outcomes
Severity: MEDIUM | Status: CONFIRMED (static path; executable fixture pending)
Components: LIVE-DIAG snapback/statistics, PR #74.
Files/functions: analysis/live_diag.py::observe_snapback, summarize_snapback, _bootstrap_median.
Claim: horizon observations and half-life summarize independently observed gap episodes.
Actual: as-of holds the last quote indefinitely and does not require either source watermark to reach trigger+horizon. Half-life excludes all episodes that never halve; threshold recrossings are called independent without event/time-block clustering.
Counterexample: capture stops at trigger+1s after a gap closes; the same close is scored at 300s despite no 300s evidence. One fast-closing episode plus 99 censored/nonclosing episodes reports the one fast half-life.
Economic consequence: incomplete first-hour data can look mature and overstate convergence speed/support.
Confidence: high.
Smallest remediation: maturity/watermark and freshness gates, explicit censoring, episode/event clustering, and multiplicity disclosure across thresholds/markets/horizons.
Tests/evidence: fixture pending. Overshoot itself remains visible (not clipped), a positive control in existing tests.


### Checkpoint 2 — reproduced diagnostic, structural and 005F failures
Tests run against the pinned sources: test_astra_diag.py (6 expected failures), test_astra_struct.py (2), test_astra_005f.py (5). Strict xfail marks desired invariants that current code violates; these are NOT successful correctness tests. Run with --runxfail to expose ordinary failing assertions. All 13 reproduced on Python 3.13.5, installed pydantic 2.11.7/numpy 2.2.6/pyarrow and isolated pydantic-settings 2.10.1 (not the repository's exact lock versions). No product implementation was modified.
ASTRA-002/003/004: CONFIRMED by executable fixtures, superseding pending-test qualifier above.

### ASTRA-005 — recommendation helper grants permissive states without economics
Severity: MEDIUM | Status: CONFIRMED
Components: LIVE-DIAG PR #74, recommend_market.
Files/functions: analysis/live_diag.py:581-610.
Claim: thin/missing/negative evidence cannot produce a confident recommendation.
Actual: 10 counts plus all economic metrics None returns QUOTE_NORMAL; negative edge with positive adverse_selection returns WIDEN before the PAUSE condition. QUOTE_MORE needs only positive point estimates and five supplied event counts, without interval, fees, health or concentration gates.
Counterexample: expected_edge=-0.03/adverse_selection=0.01 yields WIDEN; all None yields QUOTE_NORMAL.
Economic consequence: consuming this helper as guidance can encourage continued quoting under missing or negative evidence.
Confidence: high; scope is helper-only: analyze_live_diagnostics currently does not call recommend_market or implement a market-ranking output.
Smallest remediation: gate missing/nonfinite evidence and net-negative economics first; define support and confidence gates before wiring to an operator recommendation.
Tests/evidence: test_astra_diag.py last two tests. No current automatic order path from this helper was found.

### ASTRA-006 — false hard certificate from duplicate/wrong instrument identity
Severity: HIGH | Status: CONFIRMED
Components: STRUCT-SCAN-001 PR #75.
Files/functions: analysis/structural_certificates.py::StructuralRelationship, complement_relationship, exhaustive_partition_relationship, evaluate_relationship, _economics.
Claim: certificate reconstructs a feasible terminal-payoff portfolio with executable depth.
Actual: complement constructor accepts identical yes/no instrument IDs, assigns them contradictory payoff vectors and marks semantics_verified=True. Evaluation accepts dictionary key/book.instrument_id mismatch. Repeated legs also consume the same depth independently.
Counterexample: complement(x,x), single x book ask=.40 size=10 yields size=10/cost=8/guaranteed payoff=10/net=2. Actual holding is 20 units of x; in x=0 state payoff=0 and loss=8. It also requires 20 units despite only 10 displayed. A book with instrument_id='wrong' under key='yes' passes.
Economic consequence: false risk-free certificates and phantom executable size; currently SHADOW_ONLY, not evidence of live orders.
Confidence: high.
Smallest remediation: bind canonical token/venue/contract identity to immutable semantic proof, reject contradictory repeated IDs, aggregate same-instrument depth demand and validate book identities.
Tests/evidence: both test_astra_struct.py invariants reproduce; independent worst-case payoff above.

### ASTRA-007 — original frozen 005F predictors contain future-bin information
Severity: HIGH | Status: CONFIRMED
Components: original 005F research TRAIN/DEV, fit/freeze and HOLDOUT; runtime parity claims.
Files/functions: scripts/kaggle/experiment_005f_{train_dev,fit_freeze,holdout}/run.py::load_book_state, rolling_counts, build_clock. TRAIN/DEV lines 446-453, 495-504, 680-701; same functions retained in all three scripts.
Claim: genuine_15/genuine_60 and other rolling activity features at time t use observations available by t.
Actual: capture rows use observed_at.floor('5s'); rolling_counts includes bin_time <= query time. Full-bin sums therefore include later observations in [t,t+5s) when t aligns with a five-second bin.
Counterexample: genuine change observed at 19s is placed in the 15s bin and counted in genuine_15 at 15s. Source-extracted original rolling_counts returns 1 where observable-at-15s count is 0, in all three frozen scripts.
Economic consequence: historical forecast/hazard evidence uses contaminated inputs. This does NOT prove the reported incremental age effect disappears; it requires a frozen-spec falsification rerun and demotion of clean-causal/parity claims until checked. No holdout was rerun or tuned here.
Confidence: high on leakage; effect-size impact unquantified without raw corpus/artifacts.
Smallest remediation: preserve original results; run POST_HOC_FALSIFICATION_ONLY with raw event-time windows or completed-bin availability; retain original models/specs and expose changes to baseline and augmented features separately.
Tests/evidence: test_astra_005f.py parametrized source-extracted tests. Live streaming cannot exactly reproduce full offline bins at their left-edge timestamps without future data.

### ASTRA-008 — 005F state survives explicit invalidation and unlimited silence
Severity: MEDIUM | Status: CONFIRMED
Components: main shadow/frozen_runtime.py::IncrementalHazard005FState; PR #76 live wrapper/state analysis; MM adapter.
Files/functions: observe, feature_vector, _asof (roughly lines 808-971).
Claim: exact valid/contiguous state is reconstructed before hazard or state-transfer evaluation.
Actual: invalid rows reset carry metadata but append no invalid state/tombstone. feature_vector as-of searches retained valid states and has no current-validity or stale-age gate. After changes at 10/20s and invalidation at 25s, a query at 30s returns the prior vector. Querying at 3600s with no more data also returns a vector.
Counterexample: fixtures above; historical retained-state implementation also needs explicit treatment, not an unreviewed silent parity fix.
Economic consequence: stale/disconnected markets can enter state buckets as extreme economic-BBO age; apparent hazard transfer may measure outage/capture behavior. Missing model artifacts still correctly prevent default frozen scoring.
Confidence: high.
Smallest remediation: separate economic age from capture-health eligibility and preserve validity intervals/tombstones; define how this correction changes exact historical parity.
Tests/evidence: last two test_astra_005f.py tests; no hazard-to-direction conversion found in frozen evaluator (direction remains None).


### Checkpoint 3 — LIVE-LEARN, MM replay, and resting-risk gap
20 strict expected failures now reproduce (6 DIAG + 2 STRUCT + 5 005F + 4 MM + 3 LIVE). Existing targeted tests: 116 passed, 1 failed. The existing source-blob provenance failure is Windows CRLF checkout hashing, NOT silent source drift: git object hashes equal both frozen golden hashes (PRED a146521068b0091b9096b5b94a535f4b8c9fd20c; 005F b31606371c528775c355807a6ccfd147cd422ecc). Do not reclassify that environment failure as research contamination.

### ASTRA-009 — real maker fill evidence is disconnected from learner attribution
Severity: HIGH | Status: CONFIRMED
Components: main LIVE-LEARN, account runtime, execution/recovery, MAKE.
Files/functions: live_learn/evidence.py::JournalExecutionEvidenceProvider._read; sig/account_runtime.py::_record_execution_events; execution/recovery.py::_recover_single_cancel; execution/live.py placement branches; maker/service.py::_refresh_capital_control.
Claim: quote economics are backed by actual BUILD-009 fills.
Actual: ordinary account fills are journaled as REALTIME_FILL, which learner ignores; normal authoritative risk reconciliation fetches fills optionally for attribution but does not journal AUTHORITATIVE_FILL. That event is produced only in cancel startup recovery, under the cancellation operation ID, whereas learner searches placement operation IDs. Empty eligible rows still produce supported=True/zero fill rate. Two-sided maker batches also explicitly return unsupported because no per-intent fill-side join exists; batch immediate fill summaries are not emitted by the single-placement-only FILL_SUMMARY path.
Counterexample: matching submission plus REALTIME_FILL quantity 10 returns supported=True/fills=(); moving a genuine AUTHORITATIVE_FILL to its cancel operation ID makes it disappear from the original quote's economics.
Economic consequence: fill rate/selection and toxicity are biased toward a tiny immediate-single-placement subset; RISK can hold inventory while LIVE-DIAG shows no maker fills.
Confidence: high.
Smallest remediation: canonical fill ledger from complete authoritative paginated fills, keyed by fill/order/intent; link cancellation and placement through exchange order ID; unresolved coverage must be unavailable, never zero; join each batch leg and preserve outcome orientation.
Tests/evidence: test_astra_live.py first two invariants; repo-wide search shows only one AUTHORITATIVE_FILL producer. Realtime-only evidence must remain provisional rather than blindly treated as financial truth.

### ASTRA-010 — 'conservative' replay assumes first queue priority
Severity: HIGH | Status: CONFIRMED
Components: MM-REPLAY-001 PR #72, ConservativeTradeFillModel.
Files/functions: mm_replay_001.py:567-611.
Claim: conservative passive fills do not invent queue priority.
Actual: any observed aggressive trade at or through the quote fills min(quote_size,trade_size), even when explicit queue_ahead is present and exceeds the whole trade. QueueAwareFillModel exists separately, but cannot make the 'conservative' branch conservative.
Counterexample: 100 shares ahead, one-share trade exactly at our bid, one-share own quote: model reports full own fill though queue ahead remains 99.
Economic consequence: false fill rates and spread P&L; selective policies can win through unrealistically favorable fill selection. Trade-through variant is properly named sensitivity, not guaranteed execution.
Confidence: high; no empirical replay result has yet been generated (handoff says WAITING_FOR_DATA/NOT_RUN).
Smallest remediation: label zero-queue model optimistic upper bound; use validated queue/volume constraints for defensible fills and stress queue/partial/impact assumptions equally across policies.
Tests/evidence: test_astra_mm.py::test_trade_at_quote_does_not_jump_known_queue.

### ASTRA-011 — revised pending quotes inherit old latency deadline
Severity: HIGH | Status: CONFIRMED
Components: MM replay reaction/cancel policy comparison.
Files/functions: mm_replay_001.py::replay_market (pending_quote replacement and pending_effective_ns, roughly 1149-1238).
Claim: all policy revisions face declared reaction_delay_ms.
Actual: later events replace pending_quote without advancing its previously scheduled activation time.
Counterexample: update at 1.000s schedules activation at 1.100s for 100ms latency. Another price arrives at 1.090s; its new quote becomes executable at 1.100s (10ms), confirmed by fill timestamp minus quote timestamp.
Economic consequence: faster-changing FV/toxicity policies get artificially fast revisions/withdrawals and favorable adverse-selection protection.
Confidence: high.
Smallest remediation: explicit queue of immutable actions with per-action decision/dispatch/effective times, or defined coalescing before dispatch followed by full latency; do not overwrite in-flight price with later information.
Tests/evidence: test_astra_mm.py::test_every_quote_revision_receives_declared_latency.

### ASTRA-012 — replay short-horizon markouts skip indefinitely into future
Severity: HIGH | Status: CONFIRMED
Components: MM replay outcome construction.
Files/functions: mm_replay_001.py::_future_fv, fair_value_convergence.
Claim: 1s/5s/... markouts refer to the stated horizon.
Actual: bisect_left finds the first row at/after the horizon; _future_fv then skips arbitrarily many missing FVs with no maximum delay, validity, or source-age gate.
Counterexample: observations at 0s and 3600s with FVs .50/.90 make the '1s future FV' .90. The same hour-later observation can populate many horizons.
Economic consequence: mislabeled future returns and survivorship hide gaps and inflate apparently timely monetization; not direct feature leakage, but invalid outcome timing.
Confidence: high.
Smallest remediation: predeclare as-of or bounded-near-horizon labeling and require source freshness; report censored/unavailable outcomes separately and maintain matched support.
Tests/evidence: test_astra_mm.py::test_missing_horizon_cannot_jump_one_hour_forward.

### ASTRA-013 — central Risk denial does not withdraw unchanged resting quotes
Severity: HIGH | Status: CONFIRMED
Components: main MAKE coordinator / RISK-002.
Files/functions: maker/coordinator.py::on_state_change (capital_force_cancel through place_actions); risk/core.py::evaluate_risk (risk_marks_untrusted); risk/service.py::RiskContextSource.refresh.
Claim: loss of trustworthy risk valuation fails closed for economic exposure.
Actual: capital_force_cancel checks only latched global/strategy halts. With marks_trusted=False, fresh placements would be denied centrally, but unchanged resting quotes are KEEP and never pass through evaluate_risk.
Counterexample: seed resting quotes exactly equal to engine's desired quotes; trusted market/account snapshot but capital.marks_trusted=False; coordinator sends no cancels and leaves both quotes resting. Initial test with unrelated old prices cancelled for repricing and therefore did not isolate the defect; corrected fixture holds prices/sizes identical.
Economic consequence: positions in another stale-mark market can render portfolio risk untrustworthy while quotes in healthy markets keep filling. Current market's own freshness checks do not cover missing marks elsewhere.
Confidence: high for coordinator contract; end-to-end multi-market service timing still needs a production-shaped rehearsal.
Smallest remediation: central risk-health/valuation gate must govern retention as well as new admission; cancel on stale/untrusted capital state with bounded retries and preserve uncertain exposure.
Tests/evidence: test_astra_live.py::test_untrusted_risk_marks_withdraw_existing_quotes; synthetic adapter only, no live orders.

ASTRA-007 addendum: CONFIRMED also in existing MM Kaggle path. build_005f_transfer preloads all grouped BBO observations into Frozen005FTransferAdapter before querying historical grids; its 15s feature includes a 19s change. test_astra_mm.py reproduces this using the actual adapter.


### Checkpoint 4 — independent accounting and live 005F ingestion
Independent cash/terminal-payoff oracle: 100 deterministic sequences of 30 mixed YES/NO buys/sells, including position reversals, fees and duplicate fill evidence, checked at marks 0, .37 and 1. All agree with risk.capital.replay_fills to 1e-20. This is a positive control of the pure average-cost helper, not proof of authoritative runtime fill completeness. Runtime SIG surviving lots use a separate FIFO reconstruction.

### ASTRA-014 — adverse-selection metric has different economic meaning from its name
Severity: MEDIUM | Status: CONFIRMED
Components: LIVE-LEARN scorer, LIVE-DIAG consumption.
Files/functions: live_learn/scoring.py::QuoteEconomicsScorer.score; live_learn/contracts.py::FillEvidence; analysis/live_diag.py maker-economics extraction.
Claim: spread capture, post-fill markout and adverse selection separate maker economics.
Actual: spread_capture uses decision-time midpoint, not pre-fill midpoint; post_fill_markout and realised_spread are identical signed future-midpoint-minus-fill-price values. adverse_selection is the negative part of that full markout, not the adverse information component. Horizons originate at decision, not individual fill. No fee or outcome-side field exists in FillEvidence.
Counterexample: buy at .49, initial midpoint .50, future midpoint .495: gross capture .01, full markout .005, adverse_selection 0 although .005 of adverse midpoint movement consumed half the spread.
Economic consequence: an operator can mistake profitable fills for absence of adverse selection, or add capture to a full markout and double count spread. No existing additive portfolio-P&L double count was found; this is a metric contract defect, not evidence that RISK equity is wrong. Missing NO orientation is a scope risk, not a demonstrated current maker NO trade.
Confidence: high for arithmetic/labels; no empirical impact magnitude established.
Smallest remediation: name full markout distinctly; add signed information move and explicit pre-fill mark/time, side normalization and fees. Document horizon origin; retain diagnostic-versus-accounting separation.
Tests/evidence: test_astra_accounting.py: one passing independent accounting oracle and one strict expected failure for adverse-selection semantics.

### ASTRA-015 — live exact-005F claim crosses a lossy snapshot boundary
Severity: HIGH | Status: CONFIRMED
Components: PR76 live state provider, MAKE runtime loop, SHADOW bus.
Files/functions: maker/runtime_loop.py::notify_polymarket/_drain_once; shadow/bus.py ingress/candidate coalescing; shadow/live.py::build_live_shadow_runtime (provider construction); shadow/live_005f.py::Live005FStateProvider.feature_vector.
Claim: exact competition-period genuine BBO state matches original event-history semantics.
Actual: provider ingests only the latest external quote when a candidate is evaluated. MAKE notifications contain affected identities and are coalesced before snapshot construction; SHADOW may coalesce again. Neither retains every intermediate BBO transition for this state provider.
Counterexample: .42 -> .44 -> .42 between worker evaluations produces two genuine changes in the full observation stream but none in latest-state sampling. Fixture full stream at 0/10/20/21/30 seconds versus sample omitting 20 yields different genuine_60 and genuine_age_s, with both providers otherwise identical.
Economic consequence: live hazard-state buckets and frozen-input parity depend on scheduler/load, especially during bursts when hazard matters. This does not turn hazard into direction and does not create missing model artifacts.
Confidence: high for path and deterministic lost-transition counterexample; real deployment loss rate not measured.
Smallest remediation: feed the exact state accumulator at the ordered BBO ingestion boundary before coalescing; hand immutable feature snapshots to candidates, or explicitly label the sampled-state approximation and quantify loss.
Tests/evidence: test_astra_005f_live.py run from pinned PR76 checkout, strict expected failure. Existing provider tests supply every event and therefore cannot catch this boundary loss.

### Checkpoint 5 — moving-head revalidation and amendments
Remote heads advanced while this audit ran. Main advanced from ee78e6095fd8d4a8953e5ba990cd970292be8fe8 to e310c29 (merges #71 exposure groups and #73 read-only Kalshi). These additive changes were merged into the local audit branch at bd67007; no production fixes were authored by this audit. Final checked branch heads: #72 348358fc4ccf914b2266a399d245a08de2d856c5; #74 e8c6dcc7e7e15bb8b362213012371e95feb860d0; #75 4aa9ba3d4e47558ce45cbd17881032ac0f573950; #76 dbd0b84497efff026a5594fae7a4ae1d3b54913d. #68 remains draft at 720db226d4701a743a216a60ead274a7b4954814, with intended-host rehearsal absent. Issue-conversation review threads were read in addition to earlier inline/formal reviews. Old #68 simulation/real and capability-ownership blockers are documented upstream as corrected; do not report them anew.

ASTRA-015: SUPERSEDED for the updated live production path by PR76 commits a5e3434/506c097/2350bb1 and subsequent fixes. Live service now calls observe_bbo before coalescing, including seed and price-change paths. The original sampled-only fixture still fails on the explicitly retained SHADOW-history fallback, but is no longer proof of a current live defect. Added test_updated_precoalescing_path_preserves_roundtrip_changes: PASS on dbd0b84. Keep historical reproduction and remaining offline-history limitation; remove ASTRA-015 from current launch-blocker counts. This does not resolve ASTRA-007's historical future-bin leakage or ASTRA-008's state invalidation behavior.

ASTRA-002: CONFIRMED on updated #74. The new loader joins DEPTH_SNAPSHOT as-of BBO with a 30s default depth-to-BBO age bound, resolving the original no-depth schema gap, but price deduplication/trust loss still reproduces. Further, _depth_from_payload sums ALL price levels, then _active_edge prices that depth at best bid/ask. One share at ask .51 plus 100 at .99 becomes ask_depth=101 paired with .51; only one share has positive edge to FV .55. New loader-level parquet fixture reproduces this price/quantity mismatch. The bound is evaluated at BBO time, not eventual impulse time, so old compressed state can still outlive it.

ASTRA-003/004: CONFIRMED unchanged on updated #74. Canonical .005 SIG tick is now fixed upstream (do not report .01 as current). Counting supported trigger IDs improves row accounting but does not supply watermark maturity or event independence; the 300s counterexample still fails.

ASTRA-005: earlier helper-only scope is SUPERSEDED. Updated #74 now calls recommend_market from analyze_market_selection and emits market_selection.parquet. Both missing-economics and negative-edge counterexamples still fail. MMEV and capital_time_efficiency correctly remain null when costs are absent, and current wiring passes capital_time_efficiency=None so QUOTE_MORE is not reached through that path. QUOTE_NORMAL/WIDEN remain possible under the bad inputs described above. This remains observational guidance, with no automatic execution route found.

ASTRA-006: both identity counterexamples still reproduce on updated #75. ASTRA-007/008/009/013 still reproduce on updated main. #72 changed only the waiting dataset manifest; it still declares WAITING_FOR_DATA / SCIENTIFIC_RESULT=NOT_RUN. No empirical MM edge exists to audit yet.

Validation checkpoint: updated #74 baseline 9 passed plus 7 hostile expected failures; updated #75 baseline 9 passed plus 2 hostile expected failures; updated #76 baseline 8 passed plus the new pre-coalescing positive control passed (historical fallback counterexample still expected-fails). New main Kalshi/exposure baseline 9 passed, alongside 8 expected failures for 005F/live contracts. Earlier MM baseline 19 passed; replay implementation did not change in the dataset-manifest update.

Broader main test selection ran 136 cases: 130 passed, 5 failed, 1 expected failure. Four failures share CAPTURE's fsync on a read-only descriptor on this Windows/Python environment; the health-status failure is directory fsync/open on Windows. Focused rerun reproduced these platform errors. They are not evidence of Linux deployment failure. No monkeypatch was used to manufacture passing durability tests. Prior golden-hash failure was Windows CRLF checkout bytes; Git blob hashes match the frozen expected values. Full locked-environment CI and intended-host durability rehearsal remain outside this local test result.

## Final bounded coverage and evidence contract

[COMPLETE] means the stated source/fixture review is finished, not that every path is correct or launch is accepted. No real SIG order, collector deployment, model refit, full historical run, or trading-account query was performed. The audit changes are documents and synthetic tests only. Main changed during the run; the additive #71/#73 merges were incorporated, and relevant open-head deltas rechecked. The early checkpoint text is intentionally retained; later explicit amendments govern current disposition.

Canonical review covered ORCHESTRATOR, CURRENT_STATE, RESEARCH_LEDGER, EXPERIMENT_REGISTRY, BUILD_LEDGER, MATHS_LEDGER, ARCHITECTURE, DATA_CONTRACTS, OPERATIONS, COMPETITION_PLAYBOOK and LAUNCH_40H_AGENDA. Large ledgers/runbooks received targeted sections and cross-references, not a claim that every operational instruction was exercised. Relevant merge/review history was inspected for execution, maker, shadow, learner, frozen candidates, observation and risk. #68 was assessed through its current scope and review trail, not an intended-host rehearsal.

### Boundary trace and remaining limits

| Boundary | What was established | Remaining limitation |
|---|---|---|
| External source -> capture | PM handlers preserve observable receive time separately from source fields; SIG capture records state/trust events. Kalshi addition is GET-only and preserves observed/source fields. | No measured live clock offset or collector-latency distribution. Kalshi has no demonstrated executable-lead result in the inspected lane. |
| Capture -> diagnostics | Production schema and BBO/depth join inspected; latest join now has depth-to-BBO age bound. | ASTRA-002: state compression loses depth/trust, and sums depth at different prices. |
| Mapping -> FV | VERIFIED, market/exchange/tournament identity gates; SAME uses mapped-token probability, COMPLEMENT uses 1-p; NO_TRADE/MODEL_ONLY fail closed. DERIVED sum is restricted by free-text partition wording, component availability and [0,1] bounds. | NEAR is accepted by direct provider; identity checks do not prove settlement equivalence. All 237 resolution texts/dates/outcome rules were not independently adjudicated. No actual accepted mapping is declared wrong by this audit. Free-text partition eligibility is weaker than a typed payoff proof. |
| FV -> SHADOW/MAKE | Immutable snapshots, explicit quote timing and finite-value gates; source wall age converted to process monotonic age; future source timestamps become negative ages for eligibility rejection. | Clock changes between source and snapshot need intended-host evidence; no measured synchronization proof here. |
| MAKE -> execution/RISK | Central admission gates, open/UNCERTAIN exposure, reservation/recovery source, and prior corrected execution reviews inspected. | ASTRA-013: retention of unchanged quotes does not use the same risk-health gate. A synthetic coordinator counterexample is not a complete production fault-injection rehearsal. |
| Fill -> learner | Journal operation, strategy and timing join traced. | ASTRA-009: realtime evidence omitted and cancel-recovery authoritative fills orphaned from original placement. A zero fill rate is not proof that no fill occurred. |
| Learner -> outcomes | Deterministic outcome identities and restart/maturity tests; matched forecast comparison keys are identical snapshot+horizon. Explicit unsupported/abstained states retained. | Economics are decision-relative, not a complete per-fill accounting ledger; aggregation outside matched forecast support can compare differing cohorts. |
| Outcomes -> diagnostics | Explicit diagnostic/accounting distinction; updated branch emits inventory/selection outputs. | Missing cost terms correctly leave MMEV and capital efficiency null, but recommendation helper can still be permissive. |

The new exposure registry covers 237 markets at tournament level and 225 with explicit PM-event grouping; 12 remain tournament-only. Hash/coverage tests passed. Event grouping is not proof of an exhaustive partition or absence of correlation across separate event IDs. Loader reads memberships/version; independent semantic re-adjudication and production limit configuration remain outside this check.

### Economics and inventory: what was and was not disproved

For a signed fill s, diagnostic full markout is s*(future_mid-fill_price). Its decomposition is s*(initial_mid-fill_price) + s*(future_mid-initial_mid). Spread capture is already inside full markout. ASTRA-014 concerns labels and missing pre-fill timing; the inspected code does not add capture plus full markout into portfolio equity.

MM replay separately accumulates signed cash flow and final inventory value, then subtracts explicit fees and terminal unwind cost. Missing fee/unwind assumptions produce null net metrics. This is a sound accounting identity conditional on correct fills and marks; ASTRA-010/011/012 attack those inputs. Terminal external FV is a valuation, not cash liquidation. Per-fill 5m diagnostics and terminal portfolio P&L must remain separate and are not additive. A mean per-fill dollar diagnostic is not interchangeable with quantity-weighted edge per share.

RISK's pure average-cost helper passed the independent cash/payoff oracle. SIG runtime uses authoritative surviving FIFO lots and equity; its realised-P&L residual is derived from session equity less unrealised change, so recombining that identity is not an independent audit of realised fills. Missing fill evidence can therefore coexist with internally consistent total account equity. The audit did not prove authoritative equity or cost basis incorrect.

Updated LIVE-DIAG inventory integration is a left-step integral of absolute inventory and conservative gross exposure between observed SHADOW snapshots: shares-seconds/currency-seconds, not a rate. It does not observe unseen intrainterval round trips, establish account trust for each point, or extend the final point to an explicit observation watermark. Half-life/time-to-flat summaries describe observed completed transitions and can be censored; direct flat transitions do not enter the half-life list. Treat these as descriptive sampled history, not complete risk-accounting history or independent episode counts. Capital efficiency remains null in current wiring; no tiny-denominator ranking exploit was found in that path.

Updated market selection uses median fill rate times median conditional markout as gross descriptive MMEV proxy. That product is not an independently established expected P&L for varying sizes, probabilities or correlated fills. Risk/ops costs remain null; there is no reconciled net attributed P&L in this output. Do not promote that gross proxy to spendable profit.

### 005F parity conclusions

Existing boundary/golden tests cover explicit grid origin, nanosecond 300-second contiguity, ambiguous BBOs, genuine counts/age, return/volatility features and separate PRE/ACTIVE hazard coordinates. The targeted baseline passed except for raw-file blob hashing on Windows CRLF; Git blob identities match the frozen source. No directional conversion was found: hazard direction remains None, missing model artifacts fail closed, and state-only transfer declares that it is not a frozen model score.

Exact reproduction of a frozen implementation is insufficient when that implementation leaks: ASTRA-007 independently attacks availability at the five-second bin boundary in train/dev, fit/freeze, holdout and preloaded MM transfer. ASTRA-008 independently attacks invalidation and silent periods. The new live ingestion repair closes ASTRA-015's coalescing mechanism but does not falsify those separate issues. Historical effect size and genuine_age_s incremental support have NOT been shown to disappear; that requires a frozen, post-hoc falsification rerun, with no retuning or replacement winner.

### Statistical red-team disposition

| Claim/lane | Current defensible classification | Why |
|---|---|---|
| First-hour PM -> SIG lead | DESCRIPTIVE_ONLY / INSUFFICIENT_EVIDENCE; some fixtures become TOO_FAST_TO_MONETIZE | No live latency/clock calibration; ASTRA-002/003 invalidate advertised executability. |
| Passive replay edge | INSUFFICIENT_EVIDENCE; fixtures show FILL_MODEL_ARTEFACT | Scientific run still NOT_RUN; queue priority and pending-revision latency are optimistic. |
| Structural hard certificate | MAPPING_ARTEFACT for reproduced malformed relationships | Identical or wrong instruments receive inconsistent payoff semantics. No actual real-world arbitrage established. |
| 005F state hazard | Qualified historical evidence with a new leakage concern; current transfer DESCRIPTIVE_ONLY | Preserve historical status; no directional alpha or model-artifact substitution. |
| Learner maker economics | INSUFFICIENT_EVIDENCE / ACCOUNTING_ARTEFACT in reproduced attribution cases | Incomplete fill joins and metric semantics; not proof RISK account equity is wrong. |
| Prior 004C-C / 005B / 005C | Preserve negative/nonpromoted dispositions | Wrong-contract controls and executable economics failed 004C-C promotion; 005B transfer 0/7; 005C familywise null not rejected. |

No REAL_EXECUTABLE_MECHANISM was established in this audit. Distinct decision IDs remove duplicate horizon rows but do not make repeated decisions around one news impulse independent. PR76's decision-cluster bootstrap is better than row bootstrap yet still does not address time/news/event dependence. Across 237 markets, several thresholds, six horizons and state buckets, an isolated winning cell is discovery, not confirmatory support. Require predeclared primary contrasts, event/time-block clusters, horizon maturity, full coverage/abstention counts and untouched forward observations. Do not rescue failed holdout evidence by selecting a replacement cell.

## Final synthesis, ranked by potential loss × likelihood × difficulty of detection

Ranking is qualitative and conditional on enabling the relevant lane; no invented probabilities or dollar-loss forecasts. Production-main retention/attribution issues rank ahead of unmerged research helpers.

### A. Things that can make us lose money directly

1. **ASTRA-013 (HIGH): existing quotes survive untrusted capital marks.** Highest immediate priority because fresh-order rejection is not enough when resting orders can still fill.
2. **ASTRA-009 (HIGH): execution and learner disagree about fills.** The account may be correct while feedback says zero fills or ignores cancel-recovery fills; operating decisions can then use a false strategy history.
3. **ASTRA-006 (HIGH, branch-only): false structural certificates.** An x/x 'complement' can promise +2 while actually losing 8 in one terminal state. Keep SHADOW_ONLY until unique identity, payoff proof and shared-depth constraints hold.
4. **ASTRA-002/003 (HIGH, branch-only): false executable cross-venue guidance.** Missing liquidity and a latency-induced side switch can manufacture positive edge. No automatic trading route from these diagnostics was found; loss is conditional on acting on them.

### B. Things that can make us believe fake alpha

- **ASTRA-007:** a 19-second event can influence a 15-second frozen predictor.
- **ASTRA-010/011:** queue-jumping fills and short inherited reaction deadlines favor replay policies unrealistically.
- **ASTRA-012/004:** short or uncaptured horizons receive later/stale values and look mature.
- **ASTRA-005/014:** permissive guidance despite missing/negative economics; adverse-selection labels hide information loss inside spread.
- Repeated decisions from one impulse and many scanned cells can create apparent confidence without independent evidence.

### C. Things that corrupt or weaken evidence

- ASTRA-009 fill attribution, ASTRA-008 stale/invalid 005F state, ASTRA-002 discarded trust/depth transitions.
- Canonical docs lag actual code (ASTRA-001); deployment authorization and code capability need separate records.
- Sampled inventory is not an authoritative continuous inventory ledger; missing costs and accounting residuals must remain visible.
- ASTRA-015 is preserved as history, superseded for current live ingestion; SHADOW-only fallback remains lossy.

### D. Things that are technically wrong but can wait

- Rename/decompose diagnostic metrics (ASTRA-014) before using them to rank or size; this is less urgent than quote withdrawal and fill truth.
- Windows durability and raw-file hash portability can wait if the supported production platform is Linux, but local tests must not be represented as green.
- Complete semantic review of free-text DERIVED/NEAR assumptions before expanding beyond reviewed contracts. No broad mapping-error allegation is justified by current evidence.

### E. Highest-value fixes before launch

1. Make risk-health failure govern quote retention as well as admission; test another held market losing a mark while this market's price remains unchanged. Preserve uncertain exposure until authoritative removal.
2. Build one canonical per-order/per-fill identity chain across placement, acknowledgement, realtime provisional fill, authoritative reconciliation and cancellation. Deduplicate by stable fill identity; unresolved is not zero.
3. Freeze original trade side/size through latency; use full as-of trusted depth at the actual price, with trigger/dispatch freshness, fees and size limits. Keep invalidations in the state history.
4. Require distinct instrument identities and independently verified state payoffs for structural certificates, with shared depth, tick/lot and short/collateral feasibility.
5. Correct five-second availability without rewriting negative history; rerun only frozen falsification contrasts. Fix runtime invalidation; preserve missing models and nondirectional hazard semantics.
6. Make simulator pending revisions retain their own causal dispatch time; label queue assumptions honestly; bound markout observation gaps.
7. Gate recommendation states on evidence completeness/net economics; disclose event-level support and censoring. Reconcile operator docs to pinned code and complete the intended-host rehearsal.

### F. Highest-value tests in the first three live hours

These are proposed checks of observations, paper/replay fixtures and existing authorized activity; this audit does not authorize real orders or disruptive production exercises.

- **Hour 1 — evidence and risk truth:** pin code/config/mapping/provider hashes; reconcile every observed placement/order/fill identifier to authoritative account changes; explicitly count unmatched/provisional fills and compare learner fill totals. Check clock offset and measured source-to-decision/dispatch latency. In a non-economic rehearsal, verify stale marks cause withdrawal and unresolved cancellation retains exposure.
- **Hour 2 — executable falsification:** recompute every apparent lead with original side, contemporaneous full depth, actual latency distribution and known fees. Record outcomes for missing depth, overshoot, stale legs and unavailable horizons instead of dropping them. Independently reconstruct payoff states for any structural candidate. Compare queue-aware and optimistic replay sensitivity on the same events.
- **Hour 3 — independent support and accounting:** reconcile cash plus marked inventory minus fees against account equity, leaving residuals explicit. Report mature matched cohorts, censored holdings and per-event/time-block counts. Re-evaluate predeclared signals on the next untouched block; retain all markets/buckets and negative outcomes. Never interpret a three-hour sample as proving small or rare effects.

### Five reasons to distrust first-hour analytics claiming an edge

1. **The fill ledger may be incomplete:** realtime and cancel-recovery fills can be absent from learner attribution.
2. **The trade may not be executable:** stale/aggregated depth, unmeasured latency, fees, or a side switch can turn a reported positive edge into a loss.
3. **The features or labels may have the wrong time:** five-second future-bin leakage and invalid/uncaptured horizon values can manufacture predictability.
4. **The fill/payoff assumptions may be false:** queue priority, pending-cancel timing, or incorrect complement identity can create profit that no feasible portfolio earns.
5. **The sample may be one event counted repeatedly:** correlated decisions, censored outcomes and selection across markets/horizons can make chance look reliable.

REAL SIG ORDERS SENT: NO.
