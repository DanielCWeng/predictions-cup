# Strategy Registry

The programme taxonomy is fixed unless MASTER explicitly changes it.

| Family | Purpose | Current implementation state | Accepted research inputs | Deployment state |
|---|---|---|---|---|
| FV-TAKE | Aggressive entry when robust executable fair value exceeds crossing costs/latency | BASELINE ENGINE NOT YET IMPLEMENTED | 004C negatives constrain naive internal/cross-venue lead-lag; 005F staleness may be a state feature | NO_TRADE |
| MAKE | Passive quoting / spread capture under positive expected fill economics | BASELINE ENGINE NOT YET IMPLEMENTED | DATA-002 role evidence; 005A broad role-aware edge negative; maker-fill model still required | NO_TRADE |
| STRUCT | Coherent constraints / relative-value relationships | BASELINE ENGINE NOT YET IMPLEMENTED | 004C-B narrow soft effect; 005D narrow LATE_COUNT structural convergence | NO_TRADE |
| PRED | Predictive models using observable market/event state | BASELINE ENGINE NOT YET IMPLEMENTED | 005C downgraded; 005E primary null; 005F state-hazard evidence; 005B blocked | NO_TRADE |
| EVENT | Event-time / result-state logic around election transitions | BASELINE ENGINE NOT YET IMPLEMENTED | 004A/004A.2 regime semantics; selected 005D/005F regime-specific evidence | NO_TRADE |
| NO_TRADE | Explicit abstention when evidence/trust/economics are insufficient | POLICY CONCEPT ONLY | Required across all families | DEFAULT |

**No strategy is registered for execution.**

Research findings do not become strategies merely because they pass an experiment. Future engines may
propose `OrderIntent`, but central Risk must mediate any execution path.
