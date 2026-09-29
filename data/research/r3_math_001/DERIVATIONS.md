# R3-MATH-001 — Core Derivations

This file records the mathematical logic independently of any DATA-004 result.

---

# 1. State-price reconstruction

Let S terminal states be indexed by s and J securities by j.

Define payoff matrix:

\[
Y\in\mathbb R^{S\times J},
\qquad
Y_{sj}=Y_j(\omega_s).
\]

Let:

\[
\pi_s=P(\omega_s),
\quad
\pi\ge0,
\quad
\mathbf1^\top\pi=1.
\]

Then the coherent security vector is:

\[
p_j
=
E_\pi[Y_j]
=
\sum_s\pi_sY_{sj}.
\]

In vector form:

\[
p=Y^\top\pi.
\]

The set of possible p is:

\[
\mathcal C
=
Y^\top\Delta_S.
\]

Because the simplex is the convex hull of its vertices:

\[
\Delta_S=\operatorname{conv}\{e_1,\ldots,e_S\},
\]

we have:

\[
\mathcal C
=
\operatorname{conv}
\{Y^\top e_s:s=1,\ldots,S\}.
\]

Each Y^T e_s is exactly the vector of security payoffs in one terminal state.

Therefore:

\[
\boxed{
\mathcal C
=
\operatorname{conv}\{Y(\omega):\omega\in\Omega\}.
}
\]

---

# 2. Sharp target bounds

Let c_s be the target-event payoff.

Observed market evidence imposes:

\[
l\le A\pi\le u,
\]

plus:

\[
\pi\ge0,
\quad
\mathbf1^\top\pi=1.
\]

Define:

\[
\Pi
=
\{\pi:
\pi\ge0,
\mathbf1^\top\pi=1,
l\le A\pi\le u\}.
\]

Then the sharp identified interval is:

\[
L=\min_{\pi\in\Pi}c^\top\pi,
\]

\[
U=\max_{\pi\in\Pi}c^\top\pi.
\]

“Sharp” means every value inside the attainable range is compatible with the stated constraints; no narrower interval follows without extra assumptions.

---

# 3. LP dual = sub/super replication

For equality constraints:

\[
L
=
\min_{\pi\ge0}
c^\top\pi
\quad
\text{s.t. }A\pi=d.
\]

Lagrangian:

\[
\mathcal L(\pi,\lambda)
=
c^\top\pi
+
\lambda^\top(d-A\pi).
\]

Rearrange:

\[
\mathcal L
=
d^\top\lambda
+
(c-A^\top\lambda)^\top\pi.
\]

For the infimum over nonnegative pi to be finite:

\[
c-A^\top\lambda\ge0.
\]

So dual:

\[
\boxed{
L
=
\max_\lambda d^\top\lambda
\quad
\text{s.t. }
A^\top\lambda\le c.
}
\]

Interpretation:

\[
A^\top\lambda
\]

is a linear combination of observed payoffs. The inequality says this portfolio never pays more than the target in any state, hence it sub-replicates the target.

The upper-bound problem similarly yields a super-replicating portfolio.

---

# 4. Identified-set information value

For target A:

\[
W_A(\Pi)=U_A-L_A.
\]

Remove source j:

\[
\Pi_{-j}.
\]

Define:

\[
\boxed{
IV_j
=
W_A(\Pi_{-j})-W_A(\Pi).
}
\]

Properties:

- IV_j ≥ 0 when removing a valid constraint can only enlarge the feasible set;
- IV_j = 0 means j is redundant for this target conditional on other constraints;
- IV_j > 0 means j tightens target identification.

For family G:

\[
IV_G
=
W_A(\Pi_{-G})-W_A(\Pi).
\]

This is not predictive alpha.

---

# 5. Maximum entropy / KL I-projection

Given feasible set Pi and reference distribution r:

\[
\pi^*
=
\arg\min_{\pi\in\Pi}
D_{\mathrm{KL}}(\pi\|r).
\]

For equality moments:

\[
\sum_s\pi_sf_k(s)=m_k,
\]

Lagrangian:

\[
\mathcal L
=
\sum_s\pi_s\log\frac{\pi_s}{r_s}
+
\alpha\left(\sum_s\pi_s-1\right)
+
\sum_k\lambda_k
\left(
\sum_s\pi_sf_k(s)-m_k
\right).
\]

FOC:

\[
\log\frac{\pi_s}{r_s}
+1+\alpha+\sum_k\lambda_kf_k(s)=0.
\]

Therefore:

\[
\pi_s
=
r_s
\exp\left(
-1-\alpha-\lambda^\top f(s)
\right).
\]

After normalization:

\[
\boxed{
\pi_s^*
=
\frac{
r_s\exp(\theta^\top f(s))
}{
\sum_u r_u\exp(\theta^\top f(u))
}.
}
\]

With uniform r this becomes the usual MaxEnt exponential family.

---

# 6. Count mean identities

Let:

\[
K=\sum_{i=1}^{n}X_i.
\]

Linearity of expectation gives:

\[
\boxed{
E[K]=\sum_iE[X_i]=\sum_ip_i.
}
\]

No independence assumption is required.

For nonnegative integer K:

\[
K=\sum_{k=1}^{n}\mathbf1\{K\ge k\}.
\]

Taking expectations:

\[
\boxed{
E[K]
=
\sum_{k=1}^{n}P(K\ge k).
}
\]

Thus a complete threshold ladder identifies the expected count exactly.

---

# 7. Coarse count-bucket bounds

Suppose buckets B_b cover count ranges:

\[
B_b=[l_b,u_b]
\]

with:

\[
q_b=P(K\in B_b).
\]

Inside bucket b:

\[
l_b
\le
E[K\mid K\in B_b]
\le
u_b.
\]

Therefore:

\[
\boxed{
\sum_bl_bq_b
\le
E[K]
\le
\sum_bu_bq_b.
}
\]

If all race marginals except p_j are known:

\[
p_j
=
E[K]-\sum_{i\ne j}p_i.
\]

Hence:

\[
\boxed{
L_E-\sum_{i\ne j}p_i
\le
p_j
\le
U_E-\sum_{i\ne j}p_i,
}
\]

intersected with [0,1].

---

# 8. Count variance and aggregate dependence

Expand:

\[
K^2
=
\sum_iX_i^2
+
2\sum_{i<j}X_iX_j.
\]

Since X_i^2=X_i:

\[
E[K^2]
=
\sum_ip_i
+
2\sum_{i<j}P(X_i=1,X_j=1).
\]

Also:

\[
K(K-1)
=
2\sum_{i<j}X_iX_j.
\]

Therefore:

\[
\boxed{
E[K(K-1)]
=
2\sum_{i<j}P(X_i=1,X_j=1).
}
\]

And:

\[
\boxed{
\operatorname{Var}(K)
=
\sum_ip_i(1-p_i)
+
2\sum_{i<j}\operatorname{Cov}(X_i,X_j).
}
\]

So if race marginals and the count variance are known, the total pairwise covariance mass is identified:

\[
\sum_{i<j}\operatorname{Cov}(X_i,X_j)
=
\frac12
\left[
\operatorname{Var}(K)
-
\sum_ip_i(1-p_i)
\right].
\]

It is not allocated to individual pairs.

---

# 9. Higher factorial moments

Define falling factorial:

\[
(K)_r=K(K-1)\cdots(K-r+1).
\]

For Bernoulli indicators:

\[
\boxed{
E[(K)_r]
=
r!
\sum_{|S|=r}
P\left(
\bigcap_{i\in S}\{X_i=1\}
\right).
}
\]

Thus a full count PMF contains symmetric aggregate information about higher-order joint dependence.

---

# 10. Poisson-binomial forward model

Under independent Bernoulli races:

\[
X_i\sim\mathrm{Bernoulli}(p_i).
\]

Probability-generating function:

\[
\boxed{
G_K(z)
=
E[z^K]
=
\prod_i(1-p_i+p_i z).
}
\]

The coefficient of z^k is P(K=k).

Hong (2013) and Biscarri et al. (2018) give efficient exact computation using DFT/convolution/FFT.

---

# 11. Ideal inverse Poisson-binomial factorisation

For each factor:

\[
1-p_i+p_i z
=
p_i\left(z+\frac{1-p_i}{p_i}\right).
\]

Hence the root is:

\[
z_i
=
-\frac{1-p_i}{p_i}.
\]

Solve:

\[
\boxed{
p_i=\frac{1}{1-z_i}.
}
\]

Therefore an exact full Poisson-binomial polynomial identifies the multiset of p_i values.

But:

- the labels are not identified;
- independence is required;
- noisy/bucketed market probabilities can make root inversion unstable.

This is primarily a theoretical identifiability result.

---

# 12. Low-dimensional factor seat model

Let F be q-dimensional common factors.

Probit form:

\[
Z_i
=
\alpha_i+\beta_i^\top F+\epsilon_i,
\qquad
\epsilon_i\sim N(0,1),
\]

\[
X_i=\mathbf1\{Z_i>0\}.
\]

Conditional probability:

\[
\boxed{
p_i(F)
=
\Phi(\alpha_i+\beta_i^\top F).
}
\]

Conditional on F, assume residual independence:

\[
X_i\perp X_j\mid F.
\]

Then:

\[
K\mid F
\sim
\mathrm{PoissonBinomial}
(p_1(F),\ldots,p_n(F)).
\]

Unconditional count PMF:

\[
\boxed{
P(K=k)
=
\int
P_{\mathrm{PB}}
(k;p_1(f),\ldots,p_n(f))
\,dP_F(f).
}
\]

This reduces 2^n explicit states to low-dimensional factor integration plus exact conditional count computation.

---

# 13. Joint-contract calibration of a factor model

If a joint market supplies:

\[
m_{ij}=P(X_i=1,X_j=1),
\]

factor model implies:

\[
m_{ij}
=
\int
p_i(f)p_j(f)
\,dP_F(f)
\]

under conditional independence.

Count-market constraints add:

\[
P(K\in B)
=
\int
\sum_{k\in B}
P_{\mathrm{PB}}(k;p(f))
\,dP_F(f).
\]

This creates a coherent calibration objective across:

- race marginals;
- count buckets;
- pair/joint markets;
- chamber control.

It remains a model, not an identity.

---

# 14. Junction-tree factorisation

For a decomposable graph with maximal cliques C and separators S, a consistent joint distribution can be assembled schematically as:

\[
p(x)
=
\frac{
\prod_{C}p_C(x_C)
}{
\prod_{S}p_S(x_S)^{\nu_S-1}
}.
\]

Exact computation scales exponentially in largest clique/treewidth, not total variable count.

This motivates solving sparse components exactly rather than globally enumerating all races.

---

# 15. Local-polytope conservative bounds

Let M be exact globally realisable marginals and L a local-consistency relaxation:

\[
\mathcal M\subseteq\mathcal L.
\]

For target linear functional c:

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

Therefore L produces an outer identified interval.

It can be wider, but it is conservative if the relaxation is valid.

---

# 16. Frank-Wolfe with election-state oracle

Suppose coherent projection solves:

\[
\min_{p\in\mathcal C}f(p).
\]

Frank-Wolfe iteration requires:

\[
v_t
=
\arg\min_{v\in\mathcal C}
\nabla f(p_t)^\top v.
\]

Every extreme point is an outcome payoff Y(omega), so:

\[
\boxed{
\omega_t
=
\arg\min_{\omega\in\Omega}
\nabla f(p_t)^\top Y(\omega).
}
\]

If election feasibility is encoded as MILP, this step can be solved without enumerating all omega.

Then update:

\[
p_{t+1}
=
(1-\gamma_t)p_t+\gamma_tv_t.
\]

For Bregman projection variants, use the corresponding convex geometry.

---

# 17. Probabilistic reconciliation

Let raw/base estimates be y_hat and exact linear constraints be:

\[
Ay=0
\]

or:

\[
y=S b
\]

for structural matrix S.

A simple weighted projection is:

\[
\boxed{
y^*
=
\arg\min_{Ay=0}
(y-\hat y)^\top W^{-1}(y-\hat y).
}
\]

R3 already contains a related QP. Forecast-reconciliation literature extends this to probabilistic distributions and score-optimised weights.

---

# 18. Common-information ancestry

If estimator e consumes source set S_e and estimator f consumes S_f, then:

\[
S_e\cap S_f\ne\varnothing
\]

signals shared information.

A combined estimator that treats e and f as independent will understate uncertainty when shared source error propagates into both.

The minimum implementation requirement is provenance, even before a formal covariance-intersection method is introduced.

---

# 19. Dynamic structural filter

Latent factor state:

\[
x_t
=
Fx_{t-1}+\eta_t.
\]

Market j observation:

\[
z_{j,t}
=
g_j(x_t)+\epsilon_{j,t}.
\]

Examples:

Race:

\[
g_j(x_t)=\sigma(a_j^\top x_t).
\]

Joint:

\[
g_j(x_t)=P_{x_t}(A\cap B).
\]

Count bucket:

\[
g_j(x_t)=P_{x_t}(K\in B).
\]

Because observations arrive asynchronously, each market update is a measurement event rather than a synchronized full vector.

Filter choice depends on nonlinearity:

- EKF/UKF for smooth low-dimensional state;
- particle/SMC for strongly nonlinear/multimodal state.

Do not open this layer before the static structural model is justified.

---

# 20. Direct-equivalent price discovery

For genuinely equivalent/common-payoff series p_1t and p_2t, a cointegrated representation can separate a common efficient price from transitory deviations.

Hasbrouck information shares attribute innovations in the common trend to each market.

Do not apply this equation mechanically to a seat-count security and an individual race; first map both onto a common latent payoff/value.
