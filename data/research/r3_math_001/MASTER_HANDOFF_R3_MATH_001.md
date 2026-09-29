# MASTER HANDOFF — R3-MATH-001 External Mathematical Research

## Status

External literature research has been persisted on:

research/r3-math-001-external-literature

The branch was realigned to current canonical main before these documentation commits.

No DATA-004 empirical performance tests were run.

R3 FINAL was not opened.

The active empirical branch research/r3-fv-001-structural-fair-value was read for method-registry context but not modified.

---

# What R3 already planned

The active registry already contains:

- S01 LP bounds;
- S02 LOO-PRICE QP;
- S03 LOO-FAMILY QP;
- S04 LOO-FAMILY Bernoulli-KL;
- S05 MaxEnt;
- S06 latent election factor;
- S07 dynamic state-space filter later.

Do not tell MASTER these were invented by R3-MATH-001.

---

# What the literature materially improves

## S01

Upgrade LP bounds to return:

- lower/upper target interval;
- dual sub/super-replication certificates;
- identified-set width;
- leave-one-contract/family interval-shrinkage information value.

## S02/S03/S04

Treat them as coherent forecast reconciliation/projection.

Add:

- interval/spread-aware observation uncertainty;
- probabilistic forecast reconciliation as a named challenger;
- MILP-oracle projection when explicit state enumeration is too large.

## S05

Use explicit KL I-projection:

\[
\min_{\pi\in\Pi}D_{\mathrm{KL}}(\pi\|r).
\]

Freeze r. Always report hard LP interval beside the completion point.

## S06

Concrete recommended model:

- low-dimensional national/chamber/region probit or logistic factors;
- conditional Bernoulli independence;
- exact conditional Poisson-binomial count evaluation;
- joint/count markets calibrate factor dependence.

---

# Genuinely new R3 objects

1. LP dual target-bound certificate.
2. Identified-set information value.
3. Count tail-sum identity for expected seats.
4. Count factorial moments for aggregate dependence.
5. Ecological-inference aggregate-to-race bounds.
6. Probabilistic forecast reconciliation.
7. Frank-Wolfe + election-state MILP oracle.
8. Junction-tree exact component solver.
9. Local-polytope conservative outer bounds.
10. Common-information/source-ancestry fusion control.
11. Hasbrouck price discovery restricted to common-payoff/direct-equivalent markets.

---

# Most important conceptual rule

\[
\boxed{
\text{identification first}
\rightarrow
\text{completion second}
\rightarrow
\text{dynamics third}.
}
\]

A factor model, MaxEnt solution or dynamic filter must never erase a wide assumption-free LP interval.

---

# ETF verdict

Do not call the programme ETF arbitrage.

ETF creation/redemption creates true convertibility between share and basket. Generic SIG/Polymarket structural relationships do not.

Use:

- coherent state-price reconstruction;
- structural probability relative value;
- combinatorial probability arbitrage only with an executable state-by-state certificate.

---

# Immediate R3 TRAIN/DEV recommendations

1. S01+ LP bounds with duals + information value.
2. Exact count identity layer.
3. Existing QP/KL plus forecast-reconciliation challenger.
4. S05 I-projection with explicit reference measure.
5. S06 low-dimensional factor + exact Poisson-binomial count engine.
6. Frank-Wolfe/MILP oracle only if explicit states become the bottleneck.
7. Junction-tree/local-polytope solver selected per graph component.

Do not open dynamic filtering merely as a rescue.

---

# Do not use as primary methods

- free Gaussian copula;
- pairwise-correlation stitching;
- noisy Poisson-binomial polynomial-root inversion;
- giant explicit 2^N state LP;
- ETF-law-of-one-price language without convertibility;
- MaxEnt presented as assumption-free;
- Hasbrouck/VECM across nonlinear contract relationships.

---

# Key literature

## Combinatorial convex geometry

Abernethy, Chen & Wortman Vaughan (2011)  
https://doi.org/10.1145/1993574.1993621

Kroer, Dudik, Lahaie & Balakrishnan (2016)  
https://doi.org/10.1145/2940716.2940767

## Marginal polytopes / graphical models

Wainwright & Jordan (2008)  
https://doi.org/10.1561/2200000001

## Probability bounds

Boros & Lee (2025)  
https://doi.org/10.1287/moor.2023.0019

Boros (2014)  
https://doi.org/10.1287/moor.2014.0657

## Forecast reconciliation

Panagiotelis et al. (2023)  
https://doi.org/10.1016/j.ejor.2022.07.040

Athanasopoulos et al. (2024)  
https://doi.org/10.1016/j.ijforecast.2023.10.010

## Entropy

Darroch & Ratcliff (1972)  
https://doi.org/10.1214/aoms/1177692379

Frittelli (2000)  
https://doi.org/10.1111/1467-9965.00079

## Aggregate-to-micro / counts

Moon (2025/26)  
https://doi.org/10.1080/07474938.2025.2604682

Hong (2013)  
https://doi.org/10.1016/j.csda.2012.10.006

Biscarri, Zhao & Brunner (2018)  
https://doi.org/10.1016/j.csda.2018.01.007

Shi et al. (2026)  
https://doi.org/10.1016/j.jmva.2025.105554

## Sensor fusion

Julier & Uhlmann (1997)  
https://doi.org/10.1109/ACC.1997.609105

## Price discovery

Hasbrouck (1995)  
https://doi.org/10.1111/j.1540-6261.1995.tb04054.x

## Prediction-market price interpretation

He & Treich (2017)  
https://doi.org/10.1016/j.jmateco.2017.02.005

---

# Files for agents

Read in this order:

1. docs/research/R3_FV_001_EXTERNAL_MATH_RESEARCH.md
2. data/research/r3_math_001/R3_RECOMMENDATIONS.md
3. data/research/r3_math_001/DERIVATIONS.md
4. data/research/r3_math_001/METHOD_TRANSFER_MATRIX.csv
5. data/research/r3_math_001/MATHS_LEDGER_GAP_ANALYSIS.md
6. data/research/r3_math_001/PAPER_REGISTRY.csv
7. data/research/r3_math_001/ETF_ANALOGY.md

This is a research handoff, not an empirical verdict.
