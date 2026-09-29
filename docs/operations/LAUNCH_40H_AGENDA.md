# FINAL 40-HOUR LAUNCH AGENDA

**Project:** 2026 Susquehanna Predictions Trading Cup  
**Launch:** 1 October 2026, 17:00 BST / 16:00 UTC / 12:00 ET  
**Agenda frozen:** 30 September 2026, ~00:10 BST  
**Time remaining at freeze:** ~40 hours 50 minutes

## Executive decision

The remaining pre-launch objective is **not** to force one more historical alpha discovery.

The project already has:

- accepted 237-market SIG mapping;
- deterministic replay / research harness;
- DATA-003 mapped-universe fills;
- DATA-004 ETS P0/P1 structural corpus;
- CAPTURE-001 launch capture;
- BUILD-009 / MAKE-001 execution and market-making foundation;
- PRED-006 future-confirmation candidates;
- 005F state-hazard evidence;
- active R3/ETF structural research.

The critical path is now:

> **instrument → shadow everything on one state → measure → control risk → rehearse → launch safely → learn faster than the field**

R3/ETF, maths challengers and other bounded research may continue in parallel, but they must **not block launch-control engineering**.

---

# Updated priority table

| Priority | Build now | Current state | Definition of done before launch | Deadline | Why it must exist before launch |
|---|---|---|---|---|---|
| **P0 HARD GATE** | **SHADOW-002 — champion/challenger bus** | Missing as a unified runtime | MAKE-0, PRED-006, 005F, direct PM residual, R3/ETF hooks, hard-structure signals and later Kalshi all consume the **same canonical observable state** and emit versioned candidate decisions without bespoke evaluation paths | **30 Sep 12:00 BST** | Without one bus, launch evidence is incomparable and every strategy becomes a separate experiment |
| **P0 HARD GATE** | **LIVE-LEARN-001 — rolling learning/control plane** | CAPTURE first-hours analytics exist, but not an always-on rolling controller | Automatic 5m / 15m / 1h reports for markouts, forecast error, fills, adverse selection, coverage, signal decay, strategy attribution, market ranking and health; no manual notebook required | **30 Sep 17:00 BST** | The Cup is an online experiment. We need to know what is working while it is still useful |
| **P0 HARD GATE** | **RISK-002 — real P&L / drawdown / capital control** | BUILD-009 has central exposure controls; proper strategy/session P&L gating is still incomplete | Realised + unrealised P&L, per-strategy attribution, gross/net exposure, open-order exposure, session-loss limit, drawdown stop, per-strategy disable, global kill, authoritative reconciliation, bounded exploratory-capital mode | **30 Sep 17:00 BST** | No meaningful live capital should be deployed without automatic loss containment and attribution |
| **P0 HARD GATE** | **FULLSTACK-001 — production launch rehearsal** | Components exist separately; full stack still needs one production-shaped gate | Intended host runs CAPTURE + SHADOW + MAKE + RISK + LIVE-LEARN together; survives feed loss, SIG reconnect, PM reconnect, process restart, SSH disconnect and operator reboot; journals/capture remain intact; one-command recovery/runbook verified | **1 Oct 05:00 BST** | Integration failure is now a larger launch risk than lack of another historical model |
| **P0** | **VENUE-001 — SIG mechanics / latency instrumentation** | Some read/capture/latency plumbing exists; true live engine behaviour remains unknown | Instrument request→dispatch→ACK/fill, cancel/requote latency, 429/503, uncertain states, quote lifetime, fill latency, partial fills, queue/replenishment proxies and external-impulse→SIG reaction time from the first live minute | **Instrumentation ready by 30 Sep 20:00 BST** | Actual SIG mechanics determine whether MM, lead/lag and TAKE strategies are economically viable |
| **P0** | **ETS-LIVE-001 — live DATA-004 P0/P1 capture + structural event stream** | Historical DATA-004 exists; live tournament ETS capture is not yet a canonical runtime surface | All 298 P0/P1 markets (596 tokens) are captured live with local observable timestamps and linked to affected SIG targets; structural update events can feed SHADOW/LIVE-LEARN | **30 Sep 20:00 BST** | Historical R3 can learn the maths, but only live capture can reveal ETF/aggregate → direct PM → SIG ordering |
| **P0** | **SIG-CONTEXT-001 — Super Signal + leaderboard/tournament snapshots** | Missing canonical runtime capture | Persist competition context that may be impossible to reconstruct later: leaderboard, Super Signal where available, tournament/account metadata and state changes with observable timestamps | **30 Sep 20:00 BST** | These variables may affect behaviour/crowding and cannot be reliably reconstructed after the fact |
| **P0** | **PRED006 future-confirmation runner** | Two frozen candidates survived FINAL; future confirmation not automated | Exact frozen C01/C02 specs score genuinely future CAPTURE-001 observations automatically; outputs feed SHADOW/LIVE-LEARN; no retuning | **1 Oct 09:00 BST** | This is the strongest current fresh predictive candidate and costs little to operationalise |
| **P0, AFTER HARD GATES** | **ECO-001 runtime — opponent reaction monitor** | Research/documentation exists only | Measure observable SIG response after external impulses, quote renewal/replenishment, crowding, response half-life and reaction-speed distribution; no invented participant identity | **1 Oct 09:00 BST** | Our competitors' behaviour may be a live feature, but we need evidence from minute one |
| **P0, AFTER HARD GATES** | **KALSHI-001 — read-only capture/FV** | Not built | Read-only normalized prices/books for relevant mapped markets, common timestamps and FV adapter into SHADOW; no trading integration required | **Target 1 Oct 09:00 BST; may slip to first 12 live hours if hard gates are not green** | Fills the remaining external-FV holes and provides an independent venue for price discovery |
| **P1** | **005F live transfer runner** | Historical/state-hazard evidence accepted; no live transfer automation | Frozen 005F state variables evaluated automatically on future SIG data and shown beside baseline in LIVE-LEARN | **Launch day / first 2 live hours** | Cheap way to determine whether the accepted hazard finding transfers to the actual Cup |
| **P1** | **Market-selection engine** | Maths/criteria exist; no live allocator | Rank markets by fill rate, expected edge, adverse selection, capital/time usage, spread/depth and signal quality; output top 20–50 attention/capital set | **First 4 live hours** | Capital and operator attention should move toward the markets actually producing opportunity |
| **P1** | **Portfolio / factor risk** | Single-market/inventory controls are stronger than correlated-election controls | At minimum, scenario/cluster caps by election family/chamber/state plus aggregate exposure reporting; sophisticated covariance/factor model may follow later | **Basic scenario caps by launch; richer model during first live day** | Many contracts are correlated and nominal per-market limits can hide a large common election bet |
| **P1** | **Hard structural scanner** | Identities/maths exist; no canonical live scanner | Monitor executable complement/partition/NegRisk/verified structural bounds using live bids/asks/depth; emit shadow certificates first | **First 6 live hours** | Exact structural violations are one of the few mechanisms that do not require predictive forecasting |
| **P1 RESEARCH** | **R3/ETF fair-value + maths challengers** | Active/corrected research lanes | Continue DATA-004 + DATA-003 TRAIN/DEV research; hand any valid model into SHADOW interface; do not block launch and do not open contaminated/off-scope evidence | **Parallel, non-blocking** | Potentially differentiated alpha, but not worth delaying the launch-control plane |
| **P2 / DEFER** | **New broad historical alpha searches** | Many families already tested | Do not commission unless a specific live observation creates a falsifiable new question | **After launch evidence** | The marginal value of another broad pre-launch search is now lower than instrumenting the live game |

---

# The 40-hour execution schedule

## T-40h to T-32h — 30 Sep 00:10–08:00 BST

### Objective: freeze runtime contracts and unblock parallel implementation

1. Freeze the canonical live-state / candidate-decision contracts for SHADOW-002.
2. Freeze RISK-002 accounting and kill-gate interfaces.
3. Start LIVE-LEARN-001 around the canonical event/journal surfaces rather than bespoke notebooks.
4. Add ETS P0/P1 identities to supervised live PM capture without changing CAPTURE schemas.
5. Add SIG context snapshot plumbing.
6. Keep R3 correction + maths challenger + other Kaggle work running independently.

### Must be true by 08:00

- no unresolved architecture question about how candidates enter SHADOW;
- no unresolved architecture question about where strategy P&L is calculated;
- launch collectors know the direct + ETS universes they must preserve.

---

## T-32h to T-24h — 30 Sep 08:00–17:00 BST

### Objective: get the control plane working end-to-end

1. SHADOW-002 receives real canonical snapshots.
2. Wire at least:
   - MAKE-0;
   - direct PM residual/reference;
   - PRED-006;
   - 005F state challenger;
   - generic R3/ETF provider hook.
3. LIVE-LEARN emits automatic 5m / 15m / 1h reports from a live/shadow soak.
4. RISK-002 produces per-strategy P&L/exposure and actually blocks intentionally bad synthetic scenarios.
5. Verify kill/disable/reconciliation behaviour with adversarial tests.

### Must be true by 17:00

- every serious candidate can be compared on the same data;
- every candidate decision is attributable after the fact;
- risk can automatically stop a strategy/session;
- rolling reports appear without operator intervention.

---

## T-24h to T-12h — 30 Sep 17:00 to 1 Oct 05:00 BST

### Objective: production-shaped rehearsal

Run the real intended stack on the intended host:

- SIG capture;
- direct PM capture;
- ETS P0/P1 capture;
- SHADOW-002;
- MAKE SHADOW;
- RISK-002;
- LIVE-LEARN-001;
- venue metrics;
- context snapshots;
- PRED006 future runner if ready;
- ECO runtime if ready;
- Kalshi read-only if core gates are already green.

Deliberately exercise:

- SIG feed disconnect/reconnect;
- PM disconnect/reconnect;
- stale sources;
- 429/503 paths where safely reproducible;
- unresolved/UNCERTAIN order fixtures;
- process restart;
- SSH disconnect;
- operator reboot;
- prior Parquet/journal readback;
- kill switch;
- strategy-specific disable.

### Must be true by 05:00

- zero silent capture drops;
- prior evidence survives restart/reboot;
- SHADOW automatically resumes safely;
- risk state reconciles;
- first-hours + rolling reports work on the resulting data;
- operator can explain how to stop, restart and recover the entire system.

If this gate fails, **fix this before adding features**.

---

## T-12h to T-4h — 1 Oct 05:00–13:00 BST

### Objective: freeze and simplify

1. Core runtime enters feature freeze.
2. Only Sev-1 launch blockers may modify SHADOW/RISK/CAPTURE/MAKE critical paths.
3. Run final CI/smoke/benchmark/recovery gate on exact candidate SHAs.
4. Verify environment separation:
   - read-only collectors do not load trade credentials;
   - LIVE remains explicitly operator gated.
5. Define initial **bounded exploratory-risk envelope**, not a full-capital strategy:
   - very small session loss cap;
   - small per-market cap;
   - small gross correlated-event cap;
   - strict stale-data kill;
   - strategy-level disable.
6. Precompute all candidate mappings/configs so launch does not require manual reconstruction.

R3/Kaggle research may still finish, but a late model joins only through the frozen provider interface. It must not force a core runtime redesign.

---

## T-4h to T-0 — 1 Oct 13:00–17:00 BST

### Objective: no cleverness; protect launch

No new features unless a hard operational blocker is discovered.

Run final operator checklist:

- exact deployment SHA;
- services active;
- SIG universe = expected tournament universe;
- direct PM supervised universe present;
- ETS P0/P1 universe present;
- clocks/NTP healthy;
- disk/memory/queue health normal;
- rolling learner writing;
- execution journal writable;
- RISK limits loaded;
- kill switch tested and reset by controlled restart;
- rollback command known;
- context snapshotter active;
- no unresolved recovery state;
- credentials in correct processes only.

---

# Launch sequence — 1 Oct 17:00 BST

Do **not** interpret the opening bell as a requirement to immediately deploy capital.

Use:

```
OBSERVE
↓
SHADOW
↓
BOUNDED EXPLORATORY RISK
↓
CHAMPION LIVE
↓
SCALE ONLY AFTER ATTRIBUTED EVIDENCE
```

## First 5–15 minutes

Primarily observe:

- SIG spreads/depth;
- update/trade frequency;
- PM→SIG lag;
- ETS→direct PM lag;
- ETS→SIG lag;
- quote renewal;
- 429/503/error behaviour;
- competitor reaction/crowding;
- leaderboard/context behaviour.

## 15–60 minutes

SHADOW all serious candidates.

LIVE-LEARN should make it possible to answer:

- which markets are active?
- which strategies are directionally right?
- which strategies have acceptable markout?
- where is MAKE adversely selected?
- is direct PM actually leading?
- is ETS moving before direct constituents?
- does PRED-006 future-confirm?
- does 005F transfer?
- what are actual signal half-lives versus our latency?

## Bounded exploratory capital

Only after:

- RISK is healthy;
- account/reconciliation state is trusted;
- feed/capture health is green;
- a candidate has observable executable rationale.

Use tiny risk first. Increase only from **attributed live evidence**, not because a strategy was historically interesting.

---

# Launch win conditions

At 17:00 BST we do **not** need to possess a perfectly proven final alpha.

We do need the following:

1. **We see the game truthfully.** No silent data loss; direct PM + ETS + SIG + context are preserved.
2. **Every candidate sees the same game.** One shadow/control plane.
3. **Every decision is attributable.** State, model/version, FV/signal, desired action and outcome can be replayed.
4. **Losses are bounded automatically.** Strategy, market, correlated-event, session and global controls exist.
5. **We can learn within minutes.** 5m/15m/1h evidence arrives automatically.
6. **We can identify the real venue mechanics.** Latency, fills, cancels, response times and stale windows are measured.
7. **We can promote/demote rapidly.** New R3/Kalshi/lead-lag discoveries plug into the same interface.
8. **The stack survives failure.** Restart/reconnect/reboot/reconciliation are rehearsed.
9. **We do not mistake activity for alpha.** Historical or live candidates earn capital only through forward evidence.
10. **We preserve optionality.** If the field crowds direct PM, we can pivot toward ETS structure, selective MAKE, hard constraints, Kalshi, event response or the markets where ecology is favourable.

---

# Explicit non-goals before launch

Do **not** spend the remaining window on:

- a broad new historical alpha atlas;
- a C++ rewrite;
- perfecting an enormous global election model;
- P2/P3 DATA-004 expansion;
- high-complexity opponent simulation;
- full Kalshi trading/execution;
- sophisticated covariance modelling before basic scenario caps;
- cosmetic dashboards that do not improve live decisions;
- rescuing failed research by changing holdouts or scopes.

---

# Operator priority rule

When tasks compete for time, use this ordering:

```
1. Can we capture and reconstruct reality?
2. Can we stop losses / reconcile orders safely?
3. Can all strategies be compared on the same state?
4. Can we learn from live evidence automatically?
5. Can we measure SIG mechanics?
6. Can we add an additional independent information source?
7. Can we improve the model?
```

A model improvement never outranks a broken item above it during the final 40 hours.
