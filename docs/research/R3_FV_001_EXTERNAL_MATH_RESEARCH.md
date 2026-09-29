# R3-FV-001 External Mathematical Literature & Structural Pricing Research

**Lane:** R3-MATH-001  
**Repository:** DanielCWeng/predictions-cup  
**Branch:** research/r3-math-001-external-literature  
**Research boundary:** documentation and mathematical literature only. No DATA-004 empirical performance testing was run in this lane. R3 FINAL was not opened.

## Executive conclusion

No single literature solves the entire SIG Cup / Polymarket structural-fair-value problem. Several mature literatures solve large pieces of it, and the strongest architecture is therefore layered rather than monolithic:

1. exact event semantics and payoff identities;
2. sharp partial identification of target probabilities;
3. coherent reconciliation/projection of noisy market estimates;
4. an explicitly labelled completion rule when the target is not point identified;
5. a scalable low-dimensional dependence model when aggregate-to-constituent inversion requires assumptions;
6. dynamic filtering and price-discovery methods only after the static object is well specified.

The closest mathematical fields are:

- combinatorial prediction markets and convex market design;
- incomplete-market/state-price theory;
- Boole/Hailperin probability bounds and partial identification;
- marginal-polytopal and graphical-model inference;
- probabilistic forecast reconciliation;
- maximum-entropy / information projection;
- ecological inference from aggregate data;
- Poisson-binomial and correlated Bernoulli count models;
- sensor fusion with unknown common information;
- market-microstructure price discovery.

The project already contains important foundations in MATHS_LEDGER.md, especially M-016 through M-030 and M-132 through M-142. The main contribution of this research is therefore not to rename those ideas, but to supply better formulations, computational machinery, and several missing objects.

The most important new or materially extended objects are:

- LP dual sub-replication / super-replication certificates for target probability bounds;
- identified-set width and contract/group information value;
- probabilistic forecast reconciliation under known linear constraints;
- Frank-Wolfe / Bregman projection with a mixed-integer election-state oracle;
- ecological-inference framing for aggregate-to-race partial identification;
- exact count moment identities, including tail-sum and factorial-moment constraints;
- exact Poisson-binomial computation inside low-dimensional latent-factor models;
- junction-tree exact inference and local-polytope outer relaxations;
- source-ancestry / common-information-aware estimator fusion;
- a sharper separation between market state prices and physical probabilities.

The strongest immediate TRAIN/DEV candidates are therefore not a wholesale replacement of the active R3 registry. They are targeted upgrades to methods already active:

- R3-S01 LP bounds plus dual certificates and interval-width information values;
- R3-S02/S03/S04 coherent projection with interval-aware losses and an oracle formulation where explicit states explode;
- R3-S05 MaxEnt recast as KL/I-projection relative to a stated prior, strictly inside the hard feasible set;
- R3-S06 specified as a low-dimensional probit/logit factor model with exact conditional Poisson-binomial count evaluation;
- a new forecast-reconciliation challenger for components whose constraints are linear and whose raw market estimates are noisy/incoherent;
- cheap exact inverse-count identities before any model-dependent inversion.

Dynamic filtering remains later-stage. Generic free-form copulas, brute-force global state enumeration, naïve ETF-law-of-one-price language, and root inversion of noisy seat-count prices should not be promoted as first-line R3 methods.

---

# Part I — Mathematical statement of the problem

Let the complete terminal election state be

\[
\omega \in \Omega.
\]

Each Polymarket or SIG event security j has terminal payoff

\[
Y_j(\omega) \in \{0,1\}
\]

or, for a general grouped payoff, a bounded deterministic function of the state.

Let Y be the state-by-security payoff matrix. Let

\[
\pi \in \Delta(\Omega),
\qquad
\pi_s \ge 0,
\qquad
\mathbf 1^\top \pi = 1
\]

be a latent pricing/state distribution. A coherent vector of security values satisfies

\[
p = Y^\top \pi.
\]

Hence the feasible price set is

\[
\mathcal C
=
\{Y^\top \pi : \pi \in \Delta(\Omega)\}
=
\operatorname{conv}\{Y(\omega): \omega\in\Omega\}.
\]

This is exactly the convex-hull representation developed in combinatorial prediction-market theory.

For a SIG target event A, define

\[
c_s = \mathbf 1\{A \text{ holds in state }s\}.
\]

If observed related markets provide exact values, intervals, or moment constraints, the logically admissible state distributions form

\[
\Pi
=
\left\{
\pi:
\pi\ge0,\;
\mathbf 1^\top\pi=1,\;
l\le Y^\top\pi\le u,\;
H\pi=h,\;
G\pi\le g
\right\}.
\]

The first question is identification:

\[
L_A=\min_{\pi\in\Pi}c^\top\pi,
\qquad
U_A=\max_{\pi\in\Pi}c^\top\pi.
\]

Only if L_A = U_A is the target point identified by the constraints. If L_A < U_A, any single fair-value point requires an additional completion/model assumption.

This leads to the central architecture:

\[
\text{semantics}
\rightarrow
\text{feasible set}
\rightarrow
\text{sharp bounds}
\rightarrow
\text{optional completion}
\rightarrow
\text{dynamic/statistical update}
\rightarrow
\text{execution}.
\]

The important scientific control is that a later point estimator must never erase the prior identified interval.

---

# Part II — Closest existing literatures

## 1. Combinatorial prediction markets

Abernethy, Chen & Wortman Vaughan (2011) formulate automated market making over large outcome spaces through convex optimisation and convex conjugacy. The central object is the convex hull of feasible security payoff vectors rather than explicit enumeration of every possible terminal state.

Reference:
- Jacob Abernethy, Yiling Chen, Jennifer Wortman Vaughan, “An Optimization-Based Framework for Automated Market-Making,” EC 2011.
- DOI: https://doi.org/10.1145/1993574.1993621
- Public manuscript: https://dash.harvard.edu/entities/publication/73120378-a24d-6bd4-e053-0100007fdf3b

Kroer, Dudík, Lahaie & Balakrishnan (2016) then show that an arbitrage-free Bregman projection can be performed with Frank-Wolfe while an integer-programming solver supplies the required linear-optimisation oracle. Their demonstration handles an outcome space of size 2^63 without enumerating every outcome.

Reference:
- Christian Kroer, Miroslav Dudík, Sébastien Lahaie, Sivaraman Balakrishnan, “Arbitrage-Free Combinatorial Market Making via Integer Programming.”
- DOI: https://doi.org/10.1145/2940716.2940767
- Preprint: https://arxiv.org/abs/1606.02825

### R3 transfer

Instead of materialising all election states, define an election-state MILP and solve

\[
\arg\min_{\omega\in\Omega} g^\top Y(\omega)
\]

as the linear oracle inside a conditional-gradient projection algorithm.

The oracle can encode:

- race binaries;
- party-win exclusivity;
- seat counts;
- seat buckets;
- chamber-control thresholds;
- joint cells;
- explicit Other states;
- semantic support restrictions.

This is a material computational extension of M-016/M-017/M-021/M-025, not a replacement for them.

### Failure modes

- The IP oracle is only as correct as the event semantics.
- Worst-case combinatorial hardness remains.
- Soft predictive relations must not be encoded as hard feasibility constraints.
- A coherent projection is not automatically a physical-probability estimator.

---

## 2. Incomplete markets and state prices

The representation p = Y^T pi is the finite-state Arrow-Debreu/state-price view. When Y is rank deficient, traded securities do not span all states and the pricing measure is not unique.

For an untraded target payoff c, no-arbitrage gives an interval rather than a single value.

The lower-bound primal problem is

\[
L_A = \min_{\pi\ge0} c^\top\pi
\quad
\text{s.t. }
A\pi=d.
\]

Its dual is

\[
L_A = \max_{\lambda} d^\top\lambda
\quad
\text{s.t. }
A^\top\lambda\le c.
\]

The dual vector lambda constructs a portfolio of observed claims whose payoff is no greater than the target payoff in every state: a sub-replicating certificate.

Likewise, the upper-bound dual is a super-replicating certificate.

Therefore:

\[
\text{sharp target probability bounds}
\equiv
\text{sub/super-replication bounds}.
\]

This is useful operationally because the solver can return not only a bound but an auditable set of contracts proving it.

For point selection inside an incomplete set, the minimal-entropy literature is a principled precedent, but it is a completion rule, not an identity.

Reference:
- Marco Frittelli, “The Minimal Entropy Martingale Measure and the Valuation Problem in Incomplete Markets,” Mathematical Finance 10(1), 2000.
- DOI: https://doi.org/10.1111/1467-9965.00079

### R3 transfer

Add dual certificates to R3-S01. For every reported bound, record:

- target;
- active source constraints;
- primal optimum;
- dual optimum;
- dual contract weights;
- gap/tolerance;
- semantic assumptions.

This makes the partial-identification layer more interpretable and useful for debugging.

---

# Part III — Is this actually ETF arbitrage?

## Institutional analogy

ETF arbitrage relies on an institutional creation/redemption mechanism. Authorized participants can exchange ETF shares for the underlying basket (or a specified creation/redemption basket). This links the secondary-market ETF price to the primary-market basket value.

Useful references:

- Ananth Madhavan, Exchange-Traded Funds and the New Dynamics of Investing, Oxford University Press, 2016. Structure/mechanics chapter: https://doi.org/10.1093/acprof:oso/9780190279394.003.0002
- Itzhak Ben-David, Francesco Franzoni, Rabih Moussawi, “Exchange-Traded Funds,” Annual Review of Financial Economics 9, 2017. DOI: https://doi.org/10.1146/annurev-financial-110716-032538
- Ben-David, Franzoni & Moussawi, “Do ETFs Increase Volatility?”, Journal of Finance 73, 2018. DOI: https://doi.org/10.1111/jofi.12727
- Claudio Raddatz, “Authorized participants’ regulatory constraints and limits to ETF arbitrage during market turmoil,” Journal of Banking & Finance, 2025. DOI: https://doi.org/10.1016/j.jbankfin.2025.107499

## Mathematical analogy

Some mathematical objects transfer:

| ETF object | R3 analogue |
| --- | --- |
| ETF market price | target event-security price |
| basket NAV | structural latent target value |
| constituents | overlapping event securities |
| tracking error / premium | structural residual |
| stale NAV components | stale event-market inputs |
| lead/lag | asynchronous information arrival |
| sampling / partial replication | incomplete event-security coverage |
| price discovery | which market/component first impounds information |

## Where the analogy fails

The decisive break is convertibility.

Prediction-market securities generally lack:

- authorized-participant creation/redemption;
- fungible conversion of a target share into a basket;
- a legally/mechanically fixed basket ratio;
- an institutional NAV process;
- guaranteed ability to short or create every leg;
- identical settlement domains.

Therefore a SIG-vs-Polymarket structural discrepancy is usually not ETF arbitrage.

The better terminology is:

- **coherent state-price reconstruction** for the static inference problem;
- **structural probability relative value** for discrepancies used as a trading signal;
- **combinatorial probability arbitrage** only when a state-by-state executable payoff proof exists.

ETF research remains useful for stale-component adjustment, price discovery, inventory/friction limits and asynchronous basket dynamics.

---

# Part IV — Exact/coherent pricing mathematics

## Marginal polytope

Wainwright & Jordan provide a unifying graphical-model treatment of exponential families, marginal polytopes and variational inference.

Reference:
- Martin J. Wainwright, Michael I. Jordan, “Graphical Models, Exponential Families, and Variational Inference,” Foundations and Trends in Machine Learning 1, 2008.
- DOI: https://doi.org/10.1561/2200000001

The exact marginal polytope is generally difficult. For arbitrary Boolean event systems, the problem connects to correlation/cut polytopes and probabilistic satisfiability.

### R3 implication

Do not design one solver for every graph component. Use structure:

- direct linear identities: closed form;
- small components: exact LP;
- low-treewidth components: junction tree;
- large combinatorial components: MILP oracle / cutting plane / Frank-Wolfe;
- very hard loopy components: conservative outer relaxation.

---

## Boole/Hailperin probability bounds

The probability-bounds literature studies sharp min/max probabilities compatible with specified marginal/intersection information.

References:
- Hailperin’s classical LP formulation is reviewed and extended by Endre Boros & Joonhee Lee, “Boole’s Probability Bounding Problem, Linear Programming Aggregations, and Nonnegative Quadratic Pseudo-Boolean Functions,” Mathematics of Operations Research, published online 2025.
- DOI: https://doi.org/10.1287/moor.2023.0019
- Endre Boros, “Polynomially Computable Bounds for the Probability of the Union of Events,” Mathematics of Operations Research, 2014.
- DOI: https://doi.org/10.1287/moor.2014.0657

Exact atom-based LPs have exponential size in the number of events. The literature therefore develops lower-dimensional aggregations and relaxations that preserve valid bounds.

### R3 transfer

R3-S01 should be treated as a partial-identification engine, not merely a consistency check.

For each target:

\[
W_A=U_A-L_A
\]

is the current identified-set width.

Define a contract information-value diagnostic

\[
IV_j
=
W_A(\Pi_{-j})-W_A(\Pi),
\]

and for a source family G,

\[
IV_G
=
W_A(\Pi_{-G})-W_A(\Pi).
\]

This measures how much a market/family shrinks the assumption-free target interval. It is structural information value, not realised predictive alpha.

This is a new diagnostic suitable for TRAIN/DEV research without claiming profitability.

---

# Part V — Coherent projection and probabilistic forecast reconciliation

The project already plans weighted quadratic and Bernoulli-KL projection. A neighbouring field provides a more developed framing: forecast reconciliation.

Forecast reconciliation begins with a collection of forecasts that violate known aggregation/linear constraints and maps them into a coherent space.

References:
- Anastasios Panagiotelis, Puwasala Gamakumara, George Athanasopoulos, Rob J. Hyndman, “Probabilistic forecast reconciliation: properties, evaluation and score optimisation,” European Journal of Operational Research 306(2), 2023.
- DOI: https://doi.org/10.1016/j.ejor.2022.07.040
- George Athanasopoulos, Rob J. Hyndman, Nikolaos Kourentzes, Anastasios Panagiotelis, “Forecast reconciliation: A review,” International Journal of Forecasting 40(2), 2024.
- DOI: https://doi.org/10.1016/j.ijforecast.2023.10.010

The generic R3 form is

\[
\hat p = \text{raw market estimates},
\qquad
p^* = \arg\min_{p\in\mathcal C} L(p,\hat p).
\]

This is close to M-021/M-025, but forecast reconciliation contributes:

- explicit theory for coherent multivariate forecast distributions;
- estimation of reconciliation weights;
- evaluation under proper multivariate scoring rules;
- a vocabulary separating base forecasts from reconciled forecasts.

### Immediate R3 challenger

Add a **probabilistic reconciliation** method for components with linear event constraints.

It should remain distinct from hard LP bounds:

- LP bounds answer what is identified;
- reconciliation answers how to combine noisy base estimates into one coherent forecast.

---

# Part VI — Maximum entropy and information projection

If hard constraints leave many feasible state distributions, one principled completion is maximum entropy:

\[
\pi^*
=
\arg\max_{\pi\in\Pi}
-\sum_s\pi_s\log\pi_s.
\]

More generally, relative to a prior r:

\[
\pi^*
=
\arg\min_{\pi\in\Pi}
D_{\mathrm{KL}}(\pi\|r).
\]

The Lagrangian produces an exponential-family solution:

\[
\pi_s^*
\propto
r_s\exp(\lambda^\top f_s).
\]

Reference:
- J. N. Darroch, D. Ratcliff, “Generalized Iterative Scaling for Log-Linear Models,” Annals of Mathematical Statistics 43(5), 1972.
- DOI: https://doi.org/10.1214/aoms/1177692379

### R3 formulation upgrade

R3-S05 should explicitly state the prior/base measure r.

Uniform r is not “assumption free”; it encodes a particular reference measure over the chosen state representation. A more defensible workflow is:

1. compute hard bounds;
2. state the base measure r;
3. perform KL/I-projection within the feasible set;
4. report both the point estimate and the original hard interval.

The output should look conceptually like:

\[
[L_A,U_A]_{\text{hard}}
\quad+\quad
p_A^{\text{I-projection}}.
\]

Never replace the first with the second.

---

# Part VII — Graphical models, junction trees and relaxations

If the event graph is sparse, graphical-model factorisation can avoid global 2^N state enumeration.

For a decomposable/junction-tree structure, a joint distribution may be represented from clique and separator marginals. Computational cost is exponential in treewidth rather than total variable count.

This suggests component-specific compilation:

- deterministic factors for exact contract semantics;
- probabilistic factors only for explicit dependence assumptions;
- exact sum-product/junction-tree inference for low-treewidth components.

For loopy graphs, a local marginal polytope is an outer relaxation of the exact marginal polytope.

If

\[
\mathcal M \subseteq \mathcal L,
\]

then minimizing/maximizing over the outer set gives conservative target bounds:

\[
\min_{\mu\in\mathcal L}c^\top\mu
\le
\min_{\mu\in\mathcal M}c^\top\mu,
\]

\[
\max_{\mu\in\mathcal L}c^\top\mu
\ge
\max_{\mu\in\mathcal M}c^\top\mu.
\]

This loses sharpness but does not create false point identification.

### R3 transfer

Before invoking a global solver:

1. decompose the semantic graph into connected/factor components;
2. estimate/compute treewidth or clique burden;
3. solve tree/chordal pieces exactly;
4. use an oracle or valid outer relaxation only where necessary.

---

# Part VIII — Aggregate counts, inverse inference and ecological inference

## Exact count identities

Let

\[
K=\sum_{i=1}^n X_i,
\qquad X_i\in\{0,1\}.
\]

Without any independence assumption:

\[
E[K]=\sum_i P(X_i=1).
\]

Hence if E[K] and all but one marginal are known,

\[
P(X_j=1)=E[K]-\sum_{i\ne j}P(X_i=1).
\]

This is an exact identity and should be separated from M-140’s model-dependent inverse objective.

For a nonnegative integer count:

\[
E[K]
=
\sum_{k=1}^n P(K\ge k).
\]

Therefore a complete threshold ladder identifies the expected seat count directly.

If only coarse buckets B_b=[l_b,u_b] with probabilities q_b are known,

\[
\sum_b l_b q_b
\le
E[K]
\le
\sum_b u_b q_b.
\]

That immediately yields assumption-free bounds for a missing constituent marginal when the other marginals are known.

## Aggregate dependence moments

Also:

\[
E[K(K-1)]
=
2\sum_{i<j}P(X_i=1,X_j=1).
\]

Thus:

\[
\operatorname{Var}(K)
=
\sum_i p_i(1-p_i)
+
2\sum_{i<j}\operatorname{Cov}(X_i,X_j).
\]

A count distribution can therefore identify total pairwise dependence mass even when it cannot allocate dependence to specific race pairs.

Higher factorial moments satisfy

\[
E[(K)_r]
=
r!\sum_{|S|=r}
P\left(\bigcap_{i\in S}\{X_i=1\}\right).
\]

These are cheap structural constraints worth extracting before any latent-factor fitting.

## Ecological inference

Ecological inference studies recovery of individual-level quantities from aggregate data. The key lesson is fundamental indeterminacy: aggregate observations generally imply an identified region rather than a point.

A recent modern formulation:
- Sarah Moon, “Partial identification of individual-level parameters using aggregate data in a nonparametric model,” 2025/2026.
- DOI: https://doi.org/10.1080/07474938.2025.2604682

This literature is a strong conceptual match for seat-distribution-to-race inversion.

### R3 transfer

Add an “aggregate-to-micro partial identification” sublayer before latent factors. It may use:

- exact count moments;
- known race marginals;
- joint-cell probabilities;
- monotonicity/support restrictions;
- polyhedral assumptions.

Any remaining point estimate then belongs to the model-dependent layer.

---

# Part IX — Poisson-binomial and latent Bernoulli factor models

## Independent benchmark

Under conditional or unconditional independence,

\[
X_i\sim \mathrm{Bernoulli}(p_i)
\]

and

\[
K=\sum_iX_i
\]

has a Poisson-binomial distribution.

Its probability-generating function is

\[
G_K(z)
=
\prod_{i=1}^n(1-p_i+p_i z).
\]

Efficient exact algorithms exist.

References:
- Yili Hong, “On computing the distribution function for the Poisson binomial distribution,” Computational Statistics & Data Analysis 59, 2013.
- DOI: https://doi.org/10.1016/j.csda.2012.10.006
- William Biscarri, Sihai Dave Zhao, Robert J. Brunner, “A simple and fast method for computing the Poisson binomial distribution function,” Computational Statistics & Data Analysis 122, 2018.
- DOI: https://doi.org/10.1016/j.csda.2018.01.007

For n around 100–200, exact conditional count evaluation is not the principal bottleneck.

## Algebraic inverse result

If the complete exact Poisson-binomial PMF is known, the generating polynomial identifies the multiset of probabilities up to permutation, because its roots encode the factors.

A 2026 BFI working paper formalises this aggregate-identification result for binary outcomes:
- Al-Najjar & Uhlig, BFI Working Paper 2026-15.
- Public PDF: https://bfi.uchicago.edu/wp-content/uploads/2026/01/BFI_WP_2026-15.pdf

However:

- labels are not recovered;
- the result assumes independence;
- exact algebraic identifiability does not imply stable inversion from noisy market bucket prices.

Therefore direct polynomial root inversion of market-implied count probabilities should not be an immediate R3 production method.

## Scalable correlated Bernoulli model

A low-dimensional probit factor specification is:

\[
Z_i
=
\alpha_i
+
\beta_{N,i}F_N
+
\beta_{C,i}F_C
+
\beta_{R,i}F_R
+
\epsilon_i,
\]

\[
X_i=\mathbf1\{Z_i>0\}.
\]

Conditional on factors F, races are independent:

\[
P(X_i=1\mid F)=\Phi(\eta_i(F)).
\]

Then:

\[
K\mid F
\sim
\mathrm{PoissonBinomial}
(p_1(F),\ldots,p_n(F)),
\]

and

\[
P(K=k)
=
\int
P(K=k\mid F=f)\,dP_F(f).
\]

This is the cleanest concrete formulation of R3-S06 found in the literature search.

Reference:
- Jiaxin Shi, Yuan Gao, Rui Pan, Hansheng Wang, “A latent factor model for high-dimensional binary data,” Journal of Multivariate Analysis 212, 2026.
- DOI: https://doi.org/10.1016/j.jmva.2025.105554

### R3 recommendation

Use a deliberately low-dimensional factor structure. Do not attempt to estimate an unconstrained pairwise dependence matrix.

---

# Part X — Copulas and Bernoulli correlation feasibility

Pairwise Bernoulli correlations cannot be chosen arbitrarily. The feasible set is related to cut/correlation polytopes.

Reference:
- Mark Huber, Nevena Marić, “Bernoulli Correlations and Cut Polytopes,” 2017 preprint.
- https://arxiv.org/abs/1706.06182

### R3 implication

A generic Gaussian-copula approach is lower priority than a structured factor model because:

- pairwise fitted latent correlations may fail global positive-semidefinite consistency;
- pairwise Bernoulli moments do not uniquely specify a global joint;
- a free correlation matrix introduces O(n^2) parameters;
- market support is unlikely to identify all of them;
- copula assumptions can create false precision.

Use copulas only as a labelled sensitivity model, not as the primary structural engine.

---

# Part XI — Dynamic filtering and common-information-aware fusion

The active R3 registry already defers a dynamic coherent/state-space layer until the static core is justified. The literature supports that sequencing.

A generic dynamic formulation is:

\[
x_t=Fx_{t-1}+\eta_t
\]

for latent election factors, with asynchronous market observations

\[
z_{j,t}=g_j(x_t)+\epsilon_{j,t}.
\]

Possible filters include EKF/UKF, particle filters, dynamic generalized linear models, and information filters.

The more novel issue is **double counting**.

If direct-market, seat-model and joint-model estimates consume overlapping underlying contracts, their errors are correlated. Naïve averaging can create artificial confidence.

Covariance Intersection was developed for fusion when cross-correlations are unknown:

\[
P^{-1}
=
\omega P_1^{-1}
+
(1-\omega)P_2^{-1},
\]

\[
\mu
=
P[
\omega P_1^{-1}\mu_1
+
(1-\omega)P_2^{-1}\mu_2
].
\]

Reference:
- Simon J. Julier, Jeffrey K. Uhlmann, “A non-divergent estimation algorithm in the presence of unknown correlations,” American Control Conference, 1997.
- DOI: https://doi.org/10.1109/ACC.1997.609105

### R3 transfer

Do not apply Gaussian CI mechanically to probabilities. Transfer the principle:

**every derived FV should carry source ancestry.**

For estimator e define

\[
S_e=\{\text{raw contracts/observations consumed by estimator }e\}.
\]

Then combination logic can detect shared evidence rather than treating related structural estimates as independent votes.

This is especially relevant when multiple estimators ultimately depend on the same direct or redundant-echo markets.

---

# Part XII — Price discovery and lead/lag

Hasbrouck’s information-share framework models related prices around a common latent efficient price and attributes innovations to different markets.

Reference:
- Joel Hasbrouck, “One Security, Many Markets: Determining the Contributions to Price Discovery,” Journal of Finance 50(4), 1995.
- DOI: https://doi.org/10.1111/j.1540-6261.1995.tb04054.x

### What transfers

For genuine direct-equivalent or extremely tightly linked event securities:

- cointegration;
- vector error correction;
- common efficient-price estimation;
- information shares;
- permanent/transitory decomposition.

### What does not transfer directly

A seat-count contract and an individual race are not two noisy prices of the same linear security. Nonlinear structural mappings must be applied before price-discovery analysis.

Therefore Hasbrouck/VECM should be confined to:

- direct-equivalent venue pairs;
- exact synthetics after transformation onto the same payoff;
- carefully defined common latent quantities.

It should not be run blindly across every graph edge.

---

# Part XIII — Market price is not automatically physical probability

Prediction-market equilibrium prices are state prices generated by traders, wealth, risk preferences, information and market design. They need not equal an unbiased physical probability.

Reference:
- Xue-Zhong He, Nicolas Treich, “Prediction market prices under risk aversion and heterogeneous beliefs,” Journal of Mathematical Economics 70, 2017.
- DOI: https://doi.org/10.1016/j.jmateco.2017.02.005

He & Treich show that equality between equilibrium state price and mean trader beliefs is not generic under heterogeneous beliefs and risk aversion.

### R3 consequence

Separate:

1. **coherence**: does a probability/state-price vector satisfy event identities?
2. **calibration**: does the vector forecast realised frequencies well?
3. **execution value**: does the vector predict future tradable SIG prices after costs?

R3 structural projection primarily addresses the first and possibly the third. It should not silently claim the second.

---

# Part XIV — Recent prediction-market arbitrage evidence

Recent work gives a useful methodological warning.

- Oriol Saguillo et al., “Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets,” 2025 preprint: https://arxiv.org/abs/2508.03474
- Gaspard Moulinier, “Mistiming is not Arbitrage: A Pre-registered Falsification of Cross-Platform Prediction-market Arbitrage...,” SSRN 2026.
- DOI: https://doi.org/10.2139/ssrn.7175258

The key transfer is methodological rather than a conclusion about profitability:

- use synchronized observations;
- use executable bid/ask rather than midpoint-only discrepancies;
- include fees;
- respect semantic/settlement differences;
- distinguish stale-quote artifacts from genuine payoff arbitrage.

This is consistent with M-018/M-020 and should remain a hard requirement.

---

# Part XV — Computational practicality

## ~100–200 races

A global state enumeration is infeasible:

\[
|\Omega|\approx 2^n
\]

before additional non-binary states.

But most promising methods do not require enumeration.

### Exact cheap objects

- complements/partitions/threshold differences;
- count means and factorial moments;
- target bounds from small local LPs;
- Poisson-binomial conditional PMFs;
- direct-equivalent price transformations.

### Sparse exact inference

If treewidth w is small, junction-tree cost is exponential in w rather than n.

### Oracle-based convex optimisation

Frank-Wolfe/Bregman projection requires repeated linear optimisation over feasible election states. A MIP oracle avoids explicit convex-hull enumeration.

### Outer relaxations

Local-polytope or other valid relaxations can return conservative outer identified intervals.

### Factor models

A small number of latent factors reduces high-dimensional dependence to a low-dimensional numerical integration plus exact conditional count evaluation.

---

# Part XVI — Separation required for the R3 handoff

## 1. Methods already planned by R3

Read from the active R3 method registry:

- R3-S01 — assumption-free LP bounds;
- R3-S02 — LOO-PRICE weighted QP projection;
- R3-S03 — LOO-FAMILY weighted QP projection;
- R3-S04 — LOO-FAMILY Bernoulli-KL projection;
- R3-S05 — maximum-entropy reconstruction;
- R3-S06 — hierarchical latent election factor;
- R3-S07 — dynamic coherent/state-space filter;
- later predictive flow/hazard augmentation.

This literature review does not claim these are new.

## 2. Materially better formulations of planned methods

### R3-S01 LP bounds
Add:
- LP dual sub/super-replication certificates;
- identified-set width;
- contract/family information value;
- small-component decomposition;
- conservative outer relaxation when exact atoms explode.

### R3-S02/S03/S04 projection
Add:
- forecast-reconciliation framing;
- spread/interval-aware loss;
- explicit base-estimate covariance/provenance;
- Frank-Wolfe + MILP oracle when explicit coherent-state construction is infeasible.

### R3-S05 MaxEnt
Replace vague “maximum entropy” with:
- explicit base measure/prior r;
- KL/I-projection;
- report hard bounds alongside the completion;
- exploit graphical factorisation where possible.

### R3-S06 latent factor
Specify:
- probit/logit low-dimensional national/chamber/region factors;
- conditional Bernoulli independence;
- exact conditional Poisson-binomial seat distribution;
- aggregate moment and joint-cell calibration constraints.

### R3-S07 dynamic
When/if opened:
- nonlinear asynchronous observation model;
- source freshness uncertainty;
- estimator/source ancestry to prevent double counting.

## 3. Genuinely new methods/objects not currently in the active registry

- probabilistic forecast reconciliation as a named challenger;
- LP dual replication certificates as a first-class R3 artifact;
- identified-set shrinkage / information value per contract or family;
- ecological-inference aggregate-to-micro bounds;
- exact count tail-sum and factorial-moment inversion layer;
- junction-tree exact component inference;
- local-polytope conservative outer bounds;
- Frank-Wolfe + MILP election-state oracle;
- common-information/source-ancestry-aware estimator fusion;
- Hasbrouck information-share model restricted to direct-equivalent/common-payoff series.

## 4. Attractive methods that should not be promoted

### Generic ETF arbitrage
Do not use creation/redemption law-of-one-price language when there is no convertibility.

### Unconstrained Gaussian copula
Too many dependence parameters; pairwise fits need not define a valid global Bernoulli joint.

### Noisy Poisson-binomial root inversion
The exact PMF identifies an unordered independent-probability multiset, but market prices are noisy, bucketed and not guaranteed independent. Root inversion can be ill-conditioned.

### One giant exact state LP
Exponential state/atom count. Use decomposition, oracle methods or valid relaxations.

### MaxEnt presented as identification
It is a completion assumption.

### Hasbrouck/VECM on nonlinear linked securities
Only use after transforming contracts to a common latent payoff/value.

### Pairwise-correlation stitching
A set of plausible pairwise correlations may be globally infeasible.

---

# Part XVII — Recommended R3 TRAIN/DEV research stack

No tests were run here. This is a recommendation to the empirical lane.

## Immediate

1. **R3-S01+ — LP bounds with dual certificates**
   - preserve current semantic feasible set;
   - return lower/upper target bounds;
   - return active dual hedge/certificate;
   - calculate leave-one-contract/family interval shrinkage.

2. **Count identity layer**
   - derive E[K] from full threshold surfaces where available;
   - bound E[K] from coarse count bins;
   - derive total pairwise dependence mass from Var(K);
   - feed these as exact constraints, not learned features.

3. **QP/KL reconciliation upgrade**
   - compare current coherent projection with an explicitly reconciled base-forecast formulation;
   - treat bid/ask/staleness as uncertainty, not merely a point weight;
   - maintain LOO-FAMILY exclusions.

4. **R3-S05 I-projection**
   - run only inside the LP feasible set;
   - freeze the base measure/prior;
   - report LP interval and I-projection point together.

5. **R3-S06 factor-count challenger**
   - 1–3 low-dimensional factors;
   - conditional Poisson-binomial exact count likelihood/evaluation;
   - no unconstrained pairwise covariance matrix.

## High-value challenger if computation becomes limiting

6. **Frank-Wolfe + election-state MILP oracle**
   - particularly for global coherence projection where explicit state variables explode.

7. **Junction-tree/local-polytope component solver**
   - choose exact or relaxed mode based on component treewidth.

## Defer until static support exists

8. dynamic filtering;
9. covariance-intersection/common-information fusion;
10. Hasbrouck information shares for selected direct-equivalent venue pairs;
11. DRO/Wasserstein ambiguity layers.

---

# Part XVIII — Things the project should stop believing

1. A single coherent point estimate is not necessarily identified.
2. Maximum entropy is not assumption free.
3. An aggregate count distribution does not generally identify labelled race marginals.
4. Pairwise correlations do not automatically form a valid multivariate Bernoulli model.
5. ETF arbitrage is not the correct institutional description without creation/redemption.
6. Coherence does not imply calibration to physical probabilities.
7. Graph connectivity does not imply tractable global inference.
8. Exact algebraic identifiability does not imply stable inversion from noisy market prices.
9. Multiple structural estimators may contain the same underlying information and cannot be averaged as independent signals.
10. A midpoint discrepancy is not an executable arbitrage certificate.

---

# Final recommended architecture

\[
\boxed{
\begin{array}{l}
\text{semantic payoff compiler}\\
\downarrow\\
\text{exact identities + count moments}\\
\downarrow\\
\text{sharp LP identified set + dual certificates}\\
\downarrow\\
\text{coherent reconciliation/projection}\\
\downarrow\\
\text{I-projection / MaxEnt completion (optional)}\\
\downarrow\\
\text{low-dimensional Bernoulli factor challenger}\\
\downarrow\\
\text{dynamic filter / price discovery only if justified}\\
\downarrow\\
\text{execution-relative-value layer}
\end{array}
}
\]

The external literature therefore supports R3’s core direction, but it also gives the project a stricter mathematical hierarchy: **identification first, completion second, dynamics third**.
