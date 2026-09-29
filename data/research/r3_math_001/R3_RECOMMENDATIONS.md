# R3-MATH-001 — Recommendations to the R3 Empirical Lane

**Boundary:** recommendations only. R3-MATH-001 ran no DATA-004 performance tests and did not open FINAL.

## Current active R3 methods observed

The active R3 registry already includes:

- R3-S01 LP bounds;
- R3-S02 LOO-PRICE QP;
- R3-S03 LOO-FAMILY QP;
- R3-S04 LOO-FAMILY Bernoulli-KL;
- R3-S05 MaxEnt;
- R3-S06 latent election factor;
- R3-S07 dynamic coherent/state-space filter, deferred until core support.

Therefore the external-maths lane recommends **upgrades**, not a restart.

---

# Immediate TRAIN/DEV additions

## 1. Upgrade R3-S01 into an identified-set engine

Current objective: assumption-free structural LP bounds.

Add four outputs per target/component:

1. lower bound L;
2. upper bound U;
3. dual sub/super-replication certificate;
4. identified-set width W=U-L.

Then add source/family ablation:

\[
IV_j=W(\Pi_{-j})-W(\Pi)
\]

and

\[
IV_G=W(\Pi_{-G})-W(\Pi).
\]

### Why

This tells the team:

- whether a target is identified at all;
- which contracts actually tighten the interval;
- which contracts are redundant;
- which dual combination proves the bound.

### Gate

Do not allow a later point estimator to be described as exact if it lies inside a nontrivial interval.

---

## 2. Add exact count identities before any inverse model

For any count component:

### Mean

\[
E[K]=\sum_i p_i.
\]

### Threshold ladder

\[
E[K]=\sum_{k=1}^{n}P(K\ge k).
\]

### Coarse bucket mean bounds

\[
\sum_b l_bq_b
\le E[K]\le
\sum_bu_bq_b.
\]

### Dependence mass

\[
E[K(K-1)]
=
2\sum_{i<j}P(X_i=1,X_j=1).
\]

Use these as exact/partial-identification constraints.

### Why

They are cheaper and less assumption-heavy than immediately fitting the latent model.

---

## 3. Keep QP/KL projection, but benchmark a reconciliation formulation

R3-S02/S03/S04 already cover core coherent projection.

Add a named challenger based on probabilistic forecast reconciliation:

- raw market estimates = base forecasts;
- semantic identities = reconciliation constraints;
- training-only error/weight estimate;
- coherent reconciled target.

### Required separation

LP bounds:
- identification.

Reconciliation:
- point estimation from noisy inputs.

Do not compare them as if they answer the same question.

---

## 4. Specify R3-S05 as I-projection

Freeze:

- feasible hard set Pi;
- base/reference measure r;
- objective:

\[
\min_{\pi\in\Pi}D_{\mathrm{KL}}(\pi\|r).
\]

Report:

- hard interval [L,U];
- I-projection point;
- sensitivity to reasonable alternate r choices.

### Stop condition

If point conclusions are highly prior-sensitive, do not call MaxEnt a stable structural FV.

---

## 5. Specify R3-S06 as low-dimensional factor + exact count engine

Suggested first formulation:

\[
P(X_i=1\mid F)
=
\Phi(\alpha_i+\beta_i^\top F)
\]

or logistic equivalent.

Use:

- national factor;
- chamber factor;
- region/state factor only where support exists.

Conditional on F:

\[
K\mid F
\sim
\mathrm{PoissonBinomial}(p_1(F),\ldots,p_n(F)).
\]

Compute conditional count probabilities exactly using FFT/convolution.

### Avoid

- free pairwise correlation matrix;
- generic unconstrained copula;
- one latent parameter per graph edge.

### Required reporting

- factor dimension;
- loadings restrictions;
- target-family exclusions;
- which aggregate and joint markets identify which parameters;
- sensitivity against LP bounds.

---

# High-value computational challenger

## 6. Frank-Wolfe + MILP state oracle

Use only if the coherent state space becomes too large to enumerate.

Represent one feasible election state as a MILP.

Then solve the conditional-gradient oracle:

\[
\arg\min_{\omega\in\Omega}
g^\top Y(\omega).
\]

This allows QP/KL/Bregman-style coherent projection over an implicit convex hull.

### TRAIN/DEV question

Does the oracle formulation reproduce exact small-component solutions and scale to the larger structural graph with acceptable optimisation tolerance/runtime?

This is a computational validation, not a predictive result.

---

# Component-specific challenger

## 7. Junction-tree exact / local-polytope relaxed solver

Compile each structural component.

If low treewidth:
- exact junction-tree inference/bounds.

If loopy/dense:
- valid local-polytope outer interval before attempting a stronger approximation.

### Required output

- component;
- variable count;
- factor count;
- treewidth/clique estimate;
- exact vs relaxation mode;
- bound gap against exact tiny-component oracle where possible.

---

# Defer

## Dynamic filtering

Keep R3-S07 deferred until a static structural estimator demonstrates support.

A filter cannot repair a wrong observation/state model.

## Common-information fusion

Implement source ancestry now if easy, but defer formal fusion until there are multiple viable estimators.

## Hasbrouck information shares

Run only for:

- direct-equivalent cross-venue series;
- exact synthetics transformed onto the same payoff.

Do not use it as a universal graph-edge lead/lag model.

## Distributionally robust / Wasserstein layer

Useful only after the base structural model and uncertainty representation are credible.

---

# Methods to exclude from immediate R3 search

1. generic Gaussian copula with free pairwise correlations;
2. noisy polynomial-root inversion of seat-count market probabilities;
3. brute-force 2^N state enumeration outside tiny validation components;
4. ETF creation/redemption “arbitrage” framing;
5. pairwise correlation stitching without global Bernoulli feasibility;
6. MaxEnt without a declared base measure;
7. dynamic filter as a rescue for static DEV failure;
8. nonlinear lead/lag tests pretending a count market and race market are the same security.

---

# Suggested empirical-lane order

\[
\boxed{
\begin{array}{l}
\text{S01 hard bounds + duals}\\
\rightarrow
\text{exact count identities}\\
\rightarrow
\text{S02/S03 QP + reconciliation challenger}\\
\rightarrow
\text{S04 KL}\\
\rightarrow
\text{S05 I-projection}\\
\rightarrow
\text{S06 factor + Poisson-binomial}\\
\rightarrow
\text{oracle/junction-tree computational challengers if needed}\\
\rightarrow
\text{S07 dynamic only after static support}
\end{array}
}
\]

FINAL remains governed by the active R3 protocol. This research lane does not alter that gate.
