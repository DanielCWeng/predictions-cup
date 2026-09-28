# Strategy Registry

The programme taxonomy is fixed unless MASTER explicitly changes it.

| Family | Purpose | Current implementation state | Accepted research inputs | Deployment state |
|---|---|---|---|---|
| FV-TAKE | Aggressive entry when robust executable fair value exceeds crossing costs/latency | BUILD-009 GENERIC INTERFACE IN REVIEW; NO APPROVED STRATEGY | 004C negatives constrain naive internal/cross-venue lead-lag; 005F staleness may be a state feature | NO_TRADE |
| MAKE | Passive quoting / spread capture under positive expected fill economics | BUILD-009 GENERIC INTERFACE IN REVIEW; NO APPROVED STRATEGY | DATA-002 role evidence; 005A broad role-aware edge negative; maker-fill model still required | NO_TRADE |
| STRUCT | Coherent constraints / relative-value relationships | BUILD-009 GENERIC INTERFACE IN REVIEW; NO APPROVED STRATEGY | 004C-B narrow soft effect; 005D narrow LATE_COUNT structural convergence | NO_TRADE |
| PRED | Predictive models using observable market/event state | BUILD-009 GENERIC INTERFACE IN REVIEW; NO APPROVED STRATEGY | 005C downgraded; 005E primary null; 005F state-hazard evidence; 005B blocked | NO_TRADE |
| EVENT | Event-time / result-state logic around election transitions | BUILD-009 GENERIC INTERFACE IN REVIEW; NO APPROVED STRATEGY | 004A/004A.2 regime semantics; selected 005D/005F regime-specific evidence | NO_TRADE |
| NO_TRADE | Explicit abstention when evidence/trust/economics are insufficient | BUILD-009 EXPLICIT NO_TRADE IN REVIEW | Required across all families | DEFAULT |

**No strategy is registered for execution.**

Research findings do not become strategies merely because they pass an experiment. Future engines may
propose `OrderIntent`, but central Risk must mediate any execution path.


BUILD-009 provides only the generic pure strategy contract and execution plumbing. No empirical
005 finding is registered as executable alpha by this build, and every family remains deployment
state `NO_TRADE` until separately promoted.
