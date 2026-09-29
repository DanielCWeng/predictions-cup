# R3-MATH-001 — MATHS_LEDGER Gap Analysis

**Canonical ledger audited:** MATHS_LEDGER.md on current main at the branch realignment point.  
**Important:** this file proposes additions/clarifications only. R3-MATH-001 does not modify the canonical ledger directly.

## Summary

The existing ledger already captures the central static geometry:

- M-016 state-price representation;
- M-017 coherent probability polytope;
- M-020 bid/ask feasibility;
- M-021 weighted quadratic projection;
- M-023 interval-aware projection concept;
- M-025/M-026 Bernoulli-KL and spread-aware KL;
- M-027/M-028 indirect structural FV;
- M-029/M-030 seat modelling and reverse inference;
- M-035/M-036 latent factors and graphical models;
- M-128 uncertainty-aware FV;
- M-132/M-133 LOO-PRICE / LOO-FAMILY;
- M-137/M-138 one-factor logistic race/count model;
- M-139 joint-market implied correlation;
- M-140 leave-one-race inverse objective;
- M-141 structural residual.

The external literature therefore mostly **extends and sharpens** an already strong mathematical base.

---

## ALREADY_CAPTURED

### State-price / convex-hull representation

Ledger:
- M-016
- M-017

External literature confirms that this is the standard convex geometry used in combinatorial security markets.

No new ledger object required merely to cite the literature.

### Weighted coherent projection

Ledger:
- M-021
- M-023
- M-025
- M-026

The project already distinguishes quadratic, interval and KL geometries.

External forecast-reconciliation work provides a richer statistical interpretation but does not invalidate the existing equations.

### LOO structural inference

Ledger:
- M-027
- M-028
- M-132
- M-133
- M-140
- M-141

The research strongly supports retaining LOO-FAMILY as the clean test of indirect information rather than confusing reconstruction with independent signal.

### Factor/count modelling

Ledger:
- M-137
- M-138

A common latent election factor with conditional Bernoulli races is already present. External high-dimensional binary-factor and Poisson-binomial literature supplies a concrete computational implementation.

---

## CAPTURED_BUT_UNDERSPECIFIED

### M-029 Race → seat distribution

Current formula status was originally unspecified, later partly supplied by M-137/M-138.

Recommended specification:

\[
P(K=k)
=
\int
P_{\text{PB}}\left(
k;
p_1(f),\ldots,p_n(f)
\right)dP_F(f),
\]

where

\[
p_i(f)=\sigma(\alpha_i+\beta_i^\top f)
\]

or a probit equivalent.

This makes the computational object explicit.

### M-030 reverse inference

The ledger currently treats reverse inference mainly as a model/LOO objective.

It should distinguish an **exact identity layer** from the model-dependent layer.

Exact:

\[
E[K]=\sum_i p_i.
\]

If all p_i except p_j are known:

\[
p_j=E[K]-\sum_{i\ne j}p_i.
\]

With coarse count buckets:

\[
\sum_b l_bq_b
\le E[K]\le
\sum_bu_bq_b,
\]

which gives direct bounds for p_j.

Only after these exact/bound objects are exhausted should M-140’s model-dependent objective be applied.

### M-035 latent factors

External literature supports specifying:

- national factor;
- chamber factor;
- regional/state factor where supported;
- idiosyncratic race error.

Avoid a free full covariance matrix.

### M-036 graphical models

The ledger names factor/Bayesian graph factorisation but does not capture the key computational split:

- exact inference on low-treewidth/chordal components;
- local-polytope outer relaxation for loopy components;
- MILP-oracle projection for hard combinatorial components.

### M-128 uncertainty-aware FV

The ledger captures posterior uncertainty but not common-information provenance. If multiple FVs consume overlapping source markets, their uncertainty cannot be fused as independent.

---

# MATERIAL_EXTENSION

## Proposed entry: LP dual structural certificate

**ID placeholder:** M-NEW-LP-DUAL

**Mathematical object**

For primal lower bound

\[
L_A=\min_{\pi\ge0}c^\top\pi
\quad
\text{s.t. }A\pi=d,
\]

dual:

\[
L_A=\max_\lambda d^\top\lambda
\quad
\text{s.t. }A^\top\lambda\le c.
\]

Upper bound has the corresponding super-replication dual.

**Evidence type:** IDENTITY / MATHEMATICAL FACT under standard LP duality.

**Proposed status:** DERIVED / READY_FOR_IMPLEMENTATION_RESEARCH.

**Why new:** current ledger has primal feasibility/arbitrage LPs but not a first-class target-bound replication certificate.

**R3 use:** interpretable proof of why a target cannot be below/above a bound.

**Falsifier:** primal/dual mismatch beyond solver tolerance or semantic invalidity of a contributing constraint.

---

## Proposed entry: identified-set width / information value

**ID placeholder:** M-NEW-ID-VALUE

\[
W_A(\Pi)=U_A-L_A.
\]

For source j:

\[
IV_j=W_A(\Pi_{-j})-W_A(\Pi).
\]

For family G:

\[
IV_G=W_A(\Pi_{-G})-W_A(\Pi).
\]

**Evidence type:** ESTIMATOR / STRUCTURAL DIAGNOSTIC.

**Status:** READY_FOR_EXPERIMENT.

**Use:** identify contracts that genuinely tighten target knowledge; distinguish redundant echoes from independent constraints.

**Important:** IV is not predictive alpha and not economic value.

---

## Proposed entry: count tail-sum identity

**ID placeholder:** M-NEW-COUNT-TAIL

\[
E[K]=\sum_{k=1}^{n}P(K\ge k).
\]

Also

\[
E[K]=\sum_iP(X_i=1).
\]

**Evidence type:** IDENTITY / MATHEMATICAL FACT.

**Use:** exact reverse inference from complete threshold surfaces.

**Falsifier:** inconsistent count definitions/boundaries.

---

## Proposed entry: count factorial-moment constraints

**ID placeholder:** M-NEW-COUNT-MOMENTS

\[
E[(K)_r]
=
r!\sum_{|S|=r}
P\left(\bigcap_{i\in S}X_i=1\right).
\]

Special case:

\[
E[K(K-1)]
=
2\sum_{i<j}P(X_i=1,X_j=1).
\]

**Evidence type:** IDENTITY / MATHEMATICAL FACT.

**Use:** extract aggregate dependence information from count distributions before fitting a factor model.

---

## Proposed entry: exact conditional Poisson-binomial count

**ID placeholder:** M-NEW-PB-FACTOR

For conditional race probabilities p_i(F):

\[
G_{K|F}(z)=\prod_i(1-p_i(F)+p_i(F)z).
\]

Use FFT/convolution to compute the exact conditional PMF.

**Evidence type:** MODEL ASSUMPTION plus exact computation conditional on the model.

**Use:** efficient R3-S06 count likelihood and chamber probabilities.

**Falsifier:** residual dependence remains after conditioning or factor model miscalibration.

---

## Proposed entry: KL I-projection relative to a base measure

**ID placeholder:** M-NEW-IPROJ

\[
\pi^*
=
\arg\min_{\pi\in\Pi}
D_{\mathrm{KL}}(\pi\|r).
\]

**Evidence type:** PRINCIPLED COMPLETION RULE / MODEL ASSUMPTION.

**Use:** sharpen M-025/M-026/M-S05 distinction between market-vector KL projection and latent-state distribution completion.

**Falsifier:** point estimate exits hard identified interval, unstable prior sensitivity, or poor DEV calibration.

---

# NEW_OBJECT

## Proposed entry: probabilistic forecast reconciliation

**ID placeholder:** M-NEW-RECONCILE

Take base probabilistic estimates \(\hat p\) and reconcile them to known constraints:

\[
p^*
=
\arg\min_{p\in\mathcal C}
L(p,\hat p),
\]

with reconciliation weights/error structure estimated on training data.

**Evidence type:** STATISTICAL ESTIMATOR.

**Use:** named challenger to simple weighted projection.

**Difference from current QP/KL rows:** the literature explicitly models base-forecast error/reconciliation and multivariate scoring, not only distance-to-coherent-set geometry.

---

## Proposed entry: Frank-Wolfe + election-state MILP oracle

**ID placeholder:** M-NEW-FW-MILP

For smooth convex projection objective f(p), conditional-gradient step solves

\[
v_t
=
\arg\min_{v\in\mathcal C}
\nabla f(p_t)^\top v.
\]

Because vertices of C correspond to feasible election states:

\[
v_t=Y(\omega_t),
\quad
\omega_t
=
\arg\min_{\omega\in\Omega}
\nabla f(p_t)^\top Y(\omega).
\]

Solve the latter as MILP.

**Evidence type:** OPTIMISATION METHOD.

**Use:** global coherent projection without explicit state enumeration.

---

## Proposed entry: junction-tree exact component solver

**ID placeholder:** M-NEW-JTREE

For decomposable factor graph, infer globally consistent joint distribution from clique and separator marginals.

**Evidence type:** COMPUTATIONAL METHOD / GRAPHICAL-MODEL FACT.

**Use:** exact inference where treewidth is manageable.

---

## Proposed entry: local-polytope conservative outer bound

**ID placeholder:** M-NEW-LOCAL-POLY

If exact marginal set M satisfies

\[
\mathcal M\subseteq\mathcal L,
\]

then target optimisation over L gives conservative outer bounds.

**Evidence type:** MATHEMATICAL RELAXATION.

**Use:** keep validity when exact global marginal-polytope optimisation is intractable.

---

## Proposed entry: common-information source ancestry

**ID placeholder:** M-NEW-ANCESTRY

Each estimator e carries

\[
S_e=\{\text{raw observations / contracts consumed}\}.
\]

Estimator combination must account for intersections S_e ∩ S_f.

**Evidence type:** ESTIMATOR GOVERNANCE / FUSION ASSUMPTION.

**Use:** prevent artificial certainty when direct, count and joint estimators recycle the same market information.

---

# CONTRADICTS_OR_WEAKENS_EXISTING_ASSUMPTION

No canonical ledger row is directly contradicted, but the literature weakens several tempting interpretations.

## Market price = physical probability

He & Treich show this is not generic under heterogeneous beliefs and risk aversion.

Therefore p=Y^T pi should be interpreted first as coherent state pricing, not automatic physical truth.

## Pairwise correlations define a joint model

False in general. Bernoulli correlation feasibility is constrained by correlation/cut-polytopal geometry.

## Aggregate count identifies labelled races

False without extra information. Under independence a complete exact Poisson-binomial PMF can identify the probability multiset, but labels are permutation-invariant.

## ETF-style law of one price

Weak analogy only. The creation/redemption mechanism is missing from generic SIG/Polymarket relationships.

---

# NOT_RELEVANT / DO NOT PROMOTE

## Generic unconstrained copula fit

Useful as a sensitivity model but not a priority structural engine.

## Literal ETF creation/redemption replication

No institutional mapping.

## Full-state brute force for 100–200 races

Use only as a correctness oracle on tiny components.

## MaxEnt without a declared reference measure

Not mathematically neutral enough to promote.

## Hasbrouck information shares across nonlinear contracts

Use only after securities have been transformed onto the same latent payoff/value.

---

# Recommended ledger maintenance action

Do not edit the canonical ledger automatically from this research branch.

After R3 review, consider adding the following objects:

1. LP dual sub/super-replication certificate.
2. Identified-set information value.
3. Count tail-sum identity.
4. Count factorial moments.
5. Conditional Poisson-binomial count engine.
6. Latent-state KL I-projection.
7. Probabilistic forecast reconciliation.
8. Frank-Wolfe + MILP oracle.
9. Junction-tree / local-polytope solver split.
10. Common-information source ancestry.

The ledger should preserve the taxonomy distinction:

- identities;
- sharp bounds;
- completion rules;
- economic/statistical models;
- dynamic inference;
- execution models.
