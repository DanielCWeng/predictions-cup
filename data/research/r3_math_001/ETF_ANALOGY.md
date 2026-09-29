# R3-MATH-001 — ETF Analogy

# Is this actually ETF arbitrage?

## Short answer

**Institutionally, no. Mathematically, partially.**

The useful transfer is basket/value reconstruction, stale-component adjustment, relative-value residuals, price discovery and limits-to-arbitrage. The defining ETF creation/redemption mechanism does not generally exist between SIG event shares and a network of Polymarket securities.

The recommended terminology is:

- **coherent state-price reconstruction** for the static inference problem;
- **structural probability relative value** for a tradeable discrepancy;
- **combinatorial probability arbitrage** only when an executable state-by-state payoff certificate exists.

---

## 1. Institutional analogy

An ETF trades in the secondary market while authorized participants can create or redeem ETF units against a specified basket in the primary market.

The creation/redemption mechanism links:

\[
\text{ETF share}
\leftrightarrow
\text{underlying basket}.
\]

That link supports a law-of-one-price arbitrage mechanism, subject to transaction costs, inventory, capital and market frictions.

References:

- Madhavan, Exchange-Traded Funds and the New Dynamics of Investing, 2016. DOI for structure/mechanics chapter: https://doi.org/10.1093/acprof:oso/9780190279394.003.0002
- Ben-David, Franzoni & Moussawi, “Exchange-Traded Funds,” Annual Review of Financial Economics, 2017. https://doi.org/10.1146/annurev-financial-110716-032538
- Raddatz, “Authorized participants’ regulatory constraints and limits to ETF arbitrage during market turmoil,” Journal of Banking & Finance, 2025. https://doi.org/10.1016/j.jbankfin.2025.107499

### SIG/Polymarket difference

There is generally no operation equivalent to:

\[
1\text{ SIG target share}
\leftrightarrow
\sum_jw_j\text{ Polymarket claims}.
\]

Even when expected payoffs are logically related, positions are not necessarily convertible, portable or fungible.

Therefore an apparent target/basket discrepancy is normally **relative value**, not institutional ETF arbitrage.

---

## 2. Mathematical analogy

| ETF | R3 event-security network |
| --- | --- |
| ETF price | SIG/direct target price |
| basket NAV | structural fair-value estimate |
| constituent | related race/joint/count contract |
| basket weights | payoff/constraint coefficients |
| premium/discount | structural residual |
| stale NAV | stale related-market observations |
| tracking error | persistent model/semantic/basis residual |
| sampled basket | incomplete event-security coverage |
| price discovery | which market first impounds common information |
| AP capacity/friction | venue liquidity, capital, fees, nonportability |

Useful transferable mathematics:

### Basket reconstruction

A target value can be reconstructed from exact constituent identities where settlement semantics create a true linear relation.

### Basis

\[
\text{basis}_t
=
p^{\text{target}}_t
-
\widehat p^{\text{struct}}_t.
\]

Unlike ETF premium/discount, this basis may contain genuine model risk because the structural estimator may not be a replicating basket.

### Stale-component adjustment

If constituent markets update asynchronously, the latent structural surface should not treat every quote as equally current.

### Price discovery

A highly traded aggregate contract may incorporate information before constituents. ETF research is a warning against assuming information always flows constituent → aggregate.

Ben-David, Franzoni & Moussawi show that ETF arbitrage can transmit shocks between ETF and underlying securities, not simply enforce a passive NAV readout:
https://doi.org/10.1111/jofi.12727

### Limits to arbitrage

Even true ETF arbitrage weakens under intermediary constraints. R3 has stronger frictions because convertibility is absent.

---

## 3. Where the analogy fails

### No creation/redemption

This is the decisive break.

### No guaranteed linear basket

Election contracts are nonlinear functions of shared states.

### No common settlement instrument

Different contracts may have:

- runoff conventions;
- recount rules;
- party attribution differences;
- tie/Other outcomes;
- different time horizons;
- different resolution sources.

### Nonfungibility

A Polymarket position cannot generally be delivered against a SIG obligation.

### Execution asymmetry

Depth, fees, shorting mechanics and settlement capital differ.

### Probability vs NAV

An ETF NAV is an accounting basket value. A structural event probability is inferred from a partially observed joint distribution.

### Incomplete identification

A seat-count surface may only bound one race probability. There is no ETF equivalent of an intrinsically unidentified constituent.

---

## 4. Better terminology

### Coherent state-price reconstruction

Best term for:

\[
p=Y^\top\pi
\]

plus inference over the admissible pi set.

### Structural probability relative value

Best term when:

\[
\widehat p_{\text{struct}}
-
p_{\text{direct}}
\]

is treated as a predictive discrepancy without a guaranteed hedge.

### Combinatorial probability arbitrage

Reserve for a strategy with an explicit state-by-state payoff proof and executable legs.

### Event-basket analogy

Acceptable informal language for communicating aggregate/constituent intuition, provided the lack of convertibility is stated.

---

## R3 rule

Do not use ETF language to upgrade a structural disagreement into an arbitrage claim.

An R3 discrepancy is hard arbitrage only if:

1. settlement semantics are exact;
2. all required payoff states are covered;
3. bid/ask/depth make the portfolio executable;
4. fees/capital are included;
5. the portfolio has nonnegative payoff in every valid state and strictly positive net payoff in at least one / guaranteed positive minimum as required by the certificate.

Otherwise classify it as reconstruction, reconciliation or relative value.
