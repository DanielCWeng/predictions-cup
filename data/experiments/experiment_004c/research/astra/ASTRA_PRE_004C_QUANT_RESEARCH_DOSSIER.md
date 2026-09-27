# ASTRA PRE-004C QUANT RESEARCH DOSSIER

**Independent methodological prior · version 1.0 · 27 September 2026**

**Purpose:** prepare for independent 004C hypothesis generation. This document contains mechanism classes and identification requirements, not selected alpha, trading rules, fitted thresholds, or results from EXPERIMENT-004B.

## Independence and scope record

- Research instruction: the attached `Pasted text(20260927-192533).txt`.
- Project: `DanielCWeng/predictions-cup`.
- Supplied frozen 004B implementation HEAD: `e9a95fb2092c7e7385ecf27f6972b8d8921c1f72`.
- Supplied discovery-spec SHA-256: `409512367f59fefa861df89c57b96586d30e28d205f60e306b20a5e64935b504`.
- Those identifiers are **instruction-supplied anchors, not independently recomputed integrity checks** in this task.
- No project repository files, 004B empirical outputs, Lane A/B reports, response/correlation matrices, factor spectra, empirical null/FDR results, sealed-event data, or Opus dossier were opened. Project architecture is taken from the brief, not re-audited.
- Inherited conversation context contained project status, branch identifiers and task descriptions. This research did not retrieve prior conversations or use numerical 004B findings. It is an independently written prior within that limitation, not a claim of a context-free researcher.
- External papers, external public repositories, exchange documentation and public practitioner posts were researched live. No empirical dataset was downloaded or analysed. No external implementation was executed. Code review below is static and scoped to named files.
- Subsequent interpretation of 004B must be a separate dated addendum. Preserve this original and its externally reported file SHA-256. A hash establishes byte identity; the saved version records the freeze, rather than proving independence by itself.

## 1. Executive synthesis

**The strongest starting model is a latent information process observed through several imperfect, state-dependent quoting processes.** A market that updates first need not originate information; a market that updates late need not offer a tradable delay. Price direction, update timing, volatility and liquidity are distinct response variables.

The most defensible methodological directions are:

1. **Age-aware conditional responses.** Measure whether a source innovation predicts a target's future movement after accounting for the target's own history, quote age, availability, probability level and common information. Start with a small linear distributed-lag or local-projection model. Compare clock-time and next-update estimands rather than allowing one to stand in for the other.
2. **Timing before direction.** A source burst may predict that another book will update, without predicting the sign. Begin with a target update-hazard model containing target history and family activity. Hawkes excitation is a challenger only if event definitions, timestamps and nonstationary baselines are defensible.
3. **Small semantic latent states.** Related threshold contracts can reflect changes in both the location and uncertainty of an election outcome distribution. Winner, seat and coalition markets need not share one factor. Begin with a semantic aggregate or training-only PCA; use a filtered state-space model only if it adds reproducible information.
4. **Predetermined liquidity interactions.** Spread, depth and imbalance can change how strongly an information innovation is reflected in a quote. Use continuous, predeclared interactions measured before the innovation. Do not search for attractive shock-size or liquidity cutoffs.
5. **Structural constraints as controls.** Complementarity, exclusivity, nesting and exhaustive partitions create dependence without diffusion. Represent these relations explicitly; investigate residual adjustment only after establishing settlement equivalence and removing duplicated outcome representations.

These are **research priorities derived from methods and measurement considerations**, not empirical claims about the competition markets. The literature does not justify importing equity information shares, neural graph discovery or self-exciting network interpretations unchanged into sparse election books.

Recent prediction-market papers are particularly useful for identifying measurement failures, but their headline conclusions require restraint. The Dubach preprint reports poor agreement between feed-inferred and chain-based direction on comparable buckets, not a universal error rate for every public trade feed [A01]. The OpenMarket paper reports observable response timing alongside a negative out-of-sample forecasting result in BTC binaries; response speed and incremental forecasting value are demonstrably different questions in that external study [A06]. Neither result establishes what happens in the project's elections.

**The binding sample-size limitation is independent information episodes and independent elections, not rows.** Thousands of sibling contracts or millions of updates cannot provide thousands of independent election outcomes. With the small discovery-event set, event-specific prediction can be assessed provisionally; broad cross-election generalisation remains weakly identified. Bootstrap, shrinkage and FDR do not manufacture independent elections.

### Evidence ladder

| Level | What may be claimed | What is still missing |
|---|---|---|
| Measurement | A variable is defined and observed with documented timing | Any predictive relationship |
| Description | A lead, residual or event cluster is present in this sample | Adequate nulls and held-out prediction |
| Conditional prediction | A predeclared model improves future observations over a proper baseline | Structural causality and transfer across elections |
| Mechanism evidence | Competing measurement/common-shock explanations are materially weakened | Usually exogenous identification or broader replication |
| Execution relevance | A separate replay evaluates availability, costs and fills | Outside this dossier's scope |

## 2. Literature map

| Literature | Useful contribution | Transfer boundary |
|---|---|---|
| Prediction-market microstructure [A01–A06] | Direction validation, contract normalisation, calibration heterogeneity, book construction, semantic constraints, timing benchmarks | Recent preprints; heterogeneous venues, epochs and categories; archival findings are not election-network evidence |
| Traditional microstructure [A07–A12] | OFI, own-versus-cross impact, common-price discovery, asynchronous covariance, conditional response estimation | Same-asset cointegration is not the same as related binary payoffs; prices are bounded and terminal; timestamp assumptions matter |
| Temporal/network methods [A13–A21] | Counting processes, conditional dependence, latent factors, sparse precision, online state changes | Shared unobserved news, state-dependent observation and few independent events threaten identification |
| Nulls and discovery [A22–A28] | Explicit null hypotheses, dependent resampling, FDR/FWER, selection adjustment and stability | Validity is inherited from the data-generating and resampling assumptions; corrections cannot rescue a bad null |
| Practitioner evidence [P01–P06] | Operational terminology, liquidity incentives, feed-versus-analytics latency, category differences and questions to investigate | Posts are claims or leads, not sufficient empirical support; employment and promotional success claims are not scientific evidence |

The external search included the five named prediction-market repositories, additional statistical implementations, Polymarket's exchange contract, and public LinkedIn material involving liquidity, options backgrounds, engineering and market structure. Searches for NYSE/Nasdaq crossover profiles did not establish a sufficiently clear individual employment transition to assert one. No identity or employment history was inferred from a repost.

## 3. Source ledger and implementation audit

All links were accessed during this research on 27 September 2026. **M** means relevant manuscript/method sections inspected; **A** means author/publisher abstract or substantive indexed excerpt reviewed, with no claim of a full-paper audit; **C** means source code inspected. Sources labelled A support a narrower review than M. Numerical external results below are author-reported, not independently replicated. Source summaries are deliberately brief; the subsequent project design judgements are this dossier's synthesis.

### 3.1 Prediction-market sources

| ID / source | Type / depth | Method and dataset | Finding or contribution | Relevance | Reproducibility | Limitation |
|---|---|---|---|---|---|---|
| A01 · [Dubach, *The Anatomy of a Decentralized Prediction Market* (2026)](https://arxiv.org/html/2604.24366v1) | Preprint / M+C | Public book archive joined to on-chain records; 600-market panel | Reports roughly 59% bucket-level direction agreement and category/price-level liquidity differences | Audit signs, quote units and capture delays | Named replication package inspected | Match-unit dependence; historical epoch; raw feed not fully shipped; not a universal classifier benchmark |
| A02 · [Qin & Yang, *Polymarket-v1 Database* (2026)](https://arxiv.org/html/2606.04217v1) | Dataset preprint / M | V1 settlement archive and metadata; 1.20B reported records | Reports near-chance aggregate performance for standard direction classifiers; distinguishes nominal and economic volume | Condition axes and de-duplication precede metrics | Public archive described; not downloaded | “100% ground truth” is an author claim; contract-path decoder and sample-selection assumptions remain to validate |
| A03 · [Le, *Decomposing Crowd Wisdom* (2026)](https://arxiv.org/html/2602.19520v1) | Calibration preprint / M+C | Kalshi/Polymarket trades; logistic recalibration, domain/horizon/size decomposition | Calibration varies by category and horizon; reported size effect does not transfer uniformly across venues | State dependence and event-level uncertainty | Code and revision diagnostics inspected | Calibration of resolutions is different from short-horizon price response; many trades share one label |
| A04 · [Saguillo et al., *Unravelling the Probabilistic Forest* (2025)](https://arxiv.org/html/2508.03474v1) | Arbitrage preprint / M | Semantic relationships and historical Polymarket records | Distinguishes within-condition, family and cross-market constraints | Build payoff relations before estimating dynamic edges | Manuscript methods available; no independent rerun | Hour-scale position aggregation and historical fee assumptions do not demonstrate simultaneous executable opportunities; omitting low-probability legs is not exact arbitrage |
| A05 · [Marriott, *Reconstructing Full Limit Order Books for Kalshi from WebSocket Streams* (2026)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6583921) | Methods preprint / A | Snapshot-anchored delta replay using q/KDB+ | Describes a reconstruction pipeline | Book validity needs snapshot alignment and ordered deltas | Abstract/method description; implementation not audited here | REST snapshot and streaming deltas need a defensible ordering boundary; aggregation is not order-level lifecycle |
| A06 · [Young, *OpenMarket* (2026)](https://arxiv.org/html/2607.26245v1) | Dataset/method preprint / M+C | Paired BTC binary books and Binance data, dual clocks and walk-forward benchmark | Reports collector-clock response and no incremental forecasting advantage over the market prior | Timing is not directional forecasting; quantify clock ambiguity | Public Rust code inspected; archive not run | BTC threshold results do not transfer to elections; nearest-event pairing is descriptive alignment |

### 3.2 Traditional microstructure and response estimation

| ID / source | Type / depth | Method and dataset | Finding or contribution | Relevance | Reproducibility | Limitation |
|---|---|---|---|---|---|---|
| A07 · [Cont, Kukanov & Stoikov, *The Price Impact of Order Book Events*](https://arxiv.org/abs/1011.6402) | Paper / A | Best-quote OFI; 50 US equities | Relates short-window price changes to net book pressure and depth | Distinguish OFI from static depth imbalance and signed trades | Formula and public paper; no replication run | Contemporaneous impact does not establish future prediction or causal cross-impact |
| A08 · [Cont, Cucuringu & Zhang, *Cross-Impact of Order Flow Imbalance in Equity Markets*](https://arxiv.org/abs/2112.13213) | Paper / A | Multi-level OFI, PCA and regularised cross-asset models | Richer own-book information can reduce apparent contemporaneous cross-impact | Strong own-market baseline before claiming network value | Author manuscript indexed; full HTML access failed | Equity panels and synchronous features differ from sparse binary books |
| A09 · [Hasbrouck, *One Security, Many Markets* (1995)](https://doi.org/10.1111/j.1540-6261.1995.tb04054.x) | Journal paper / A | Common efficient price and information shares; Dow stocks across venues | Attributes common-price innovation variance | Relevant to genuinely identical payoffs across venues | Published method; not replicated | Cointegration assumptions and correlated-innovation ordering; inappropriate by default for different election outcomes |
| A10 · [Gonzalo & Granger, *Estimation of Common Long-Memory Components in Cointegrated Systems* (1995)](https://doi.org/10.1080/07350015.1995.10524576) | Journal paper / A | Permanent/transitory decomposition in cointegrated systems | Common-component weights from adjustment structure | Alternative perspective on common-price discovery | Author/publisher record reviewed | Component share is not information share; bounded, resolving contracts need local justification |
| A11 · [Hoffmann, Rosenbaum & Yoshida, *Estimation of the Lead-Lag Parameter from Non-Synchronous Data*](https://arxiv.org/html/1303.4871v1) | Theory/method paper / M | Shifted Hayashi–Yoshida overlap contrast; continuous-time model | Lag consistency depends on observation sparsity and model assumptions | Avoid compulsory interpolation; inspect clock-time lag profiles | Explicit estimator and assumptions | News jumps, tick noise and endogenous update clocks require separate validation; not a causal identifier |
| A12 · [Jordà, *Estimation and Inference of Impulse Responses by Local Projections* (2005)](https://doi.org/10.1257/0002828053828518) | Journal paper / A | Horizon-specific regressions | Estimates responses without iterating a full VAR | Transparent small-model baseline and continuous interactions | Published equations and standard regressions | Identification of the shock remains separate; overlapping labels create dependent errors |

### 3.3 Temporal, latent and network sources

| ID / source | Type / depth | Method and dataset | Finding or contribution | Relevance | Reproducibility | Limitation |
|---|---|---|---|---|---|---|
| A13 · [Bacry, Mastromatteo & Muzy, *Hawkes Processes in Finance* (2015)](https://arxiv.org/html/1502.04592v2) | Survey / M | Multivariate/marked counting processes; financial applications | Unifies update, trade and price-event intensity models | Separate when an event occurs from its mark | Definitions and inference recipes; tick code inspected | Positive-kernel stationarity and missing events matter; excitation is model-conditional |
| A14 · [Filimonov & Sornette, *Apparent Criticality and Calibration Issues…*](https://arxiv.org/html/1308.6756v3) | Method critique / M | Synthetic and financial point-process calibration | Regime shifts and timestamp batching can mimic endogenous excitation | Strong nonstationary no-cross-excitation controls | Synthetic argument and manuscript available | A passing residual diagnostic alone need not distinguish competing generating processes |
| A15 · [Barnett, Barrett & Seth, *Granger Causality and Transfer Entropy Are Equivalent for Gaussian Variables* (2009)](https://arxiv.org/abs/0910.4514) | Theory paper / A | Gaussian conditional dependence | Equivalence under Gaussian assumptions | TE is not automatically an extra source of information | Analytical result | Non-Gaussian estimators require substantially different estimation and calibration |
| A16 · [Runge et al., *Detecting and Quantifying Causal Associations…* (2019)](https://arxiv.org/abs/1702.07007) | Method paper / A+C | PCMCI, synthetic and climate series | Conditional-independence discovery for autocorrelated series | Restricted diagnostic challenger | Tigramite CMI implementation inspected | Causal assumptions, hidden news and observation selection are unresolved in this application |
| A17 · [Bańbura & Modugno, *Maximum Likelihood Estimation of Factor Models…*](https://www.ecb.europa.eu/pub/pdf/scpwps/ecbwp1189.pdf) | Working paper / M | State-space EM with missing observations; macro nowcasting | Handles ragged/mixed-frequency panels | Filtered factors with availability masks | Public manuscript | Computational support for missingness does not solve informative quote arrival; small cross-sections differ from macro panels |
| A18 · [Tipping & Bishop, *Probabilistic Principal Component Analysis* (1999)](https://doi.org/10.1111/1467-9868.00196) | Journal paper / A | Gaussian latent-variable PCA | Probabilistic interpretation of principal components | Missing-observation baseline and uncertainty | Public author/publisher description | Isotropic observation noise and linear geometry are restrictive |
| A19 · [Chandrasekaran, Parrilo & Willsky, *Latent Variable Graphical Model Selection via Convex Optimization*](https://arxiv.org/abs/1008.1290) | Theory paper / A | Sparse-plus-low-rank precision decomposition | Conditions for separating hidden-variable and sparse structure | Understand identifiability before decomposing a network | Public theory | Decomposition of precision is not the same as robust PCA of the observation matrix; sparse/low-rank ambiguity remains |
| A20 · [Friedman, Hastie & Tibshirani, *Sparse Inverse Covariance Estimation with the Graphical Lasso* (2008)](https://doi.org/10.1093/biostatistics/kxm045) | Journal paper / A | Penalised precision matrix | Sparse Gaussian conditional-dependence graph | Descriptive residual-network challenger | Published algorithm | Undirected contemporaneous dependence; serial dependence and missing-data covariance require care |
| A21 · [Adams & MacKay, *Bayesian Online Changepoint Detection* (2007)](https://arxiv.org/html/0710.3742v1) | Methods paper / M | Recursive run-length posterior with hazard prior | Online rather than retrospective state-change inference | Diagnostic for model instability | Explicit recursion | Hazard/likelihood choice matters; cannot redefine frozen event regimes after seeing returns |

### 3.4 Nulls and multiplicity

| ID / source | Type / depth | Method and dataset | Finding or contribution | Relevance | Reproducibility | Limitation |
|---|---|---|---|---|---|---|
| A22 · [Schreiber & Schmitz, *Surrogate Time Series*](https://arxiv.org/abs/chao-dyn/9909037) | Methods review / A | Constrained randomisation; nonlinear time-series examples | Surrogate interpretation depends on the null preserved | Different nulls answer different questions | TISEAN discussed; not run | Nonstationarity, uneven sampling and marginal constraints defeat naive Fourier surrogates |
| A23 · [Politis & Romano, *The Stationary Bootstrap* (1994)](https://doi.org/10.1080/01621459.1994.10476870) | Theory/method paper / A | Random-length blocks of weakly dependent stationary observations | Dependent-data resampling | Within-regime uncertainty estimation | Published construction | Bootstrapping paired data preserves a relationship; does not itself impose a no-link null |
| A24 · [Benjamini & Hochberg, *Controlling the False Discovery Rate* (1995)](https://doi.org/10.1111/j.2517-6161.1995.tb02031.x) | Theory paper / A | Ordered p-value step-up rule | FDR control under stated dependence conditions | Simple declared discovery families | Standard implementations | Invalid p-values and uncounted searches remain invalid |
| A25 · [Benjamini & Yekutieli, *The Control of the FDR under Dependency* (2001)](https://doi.org/10.1214/aos/1013699998) | Theory paper / A | PRDS extension and harmonic correction | Arbitrary-dependence option | Sensitivity when overlapping graph tests do not meet BH assumptions | Author PDF encountered encoding problems; formula checked against SciPy docs [D03] | Often low power; still requires valid marginal p-values |
| A26 · [Romano & Wolf, *Exact and Approximate Stepdown Methods for Multiple Hypothesis Testing*](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=563267) | Theory/method paper / A | Resampling stepdown tests | Addresses limitations of subset-pivotal approaches | Joint maxima for a small primary family | Author working-paper record | Dependence-aware resampling must be valid; two elections cannot support ordinary cluster asymptotics |
| A27 · [Benjamini & Bogomolov, *Adjusting for Selection Bias in Testing Multiple Families*](https://arxiv.org/abs/1106.3670) | Theory paper / A | Adjustment after selecting families | Within-selected-family error differs from global error | Avoid “pick interesting families, then BH within each” | Public manuscript | Hierarchy and target error rate must be specified in advance |
| A28 · [Meinshausen & Bühlmann, *Stability Selection* (2010)](https://doi.org/10.1111/j.1467-9868.2010.00740.x) | Theory/method paper / A | Repeated subsample selection | Stability can supplement variable selection | Assess sensitivity of chosen edges/features | Author PDF available; not run | Blockwise adaptations do not inherit every iid guarantee; correlated proxies can exchange selection |

### 3.5 Practitioner and LinkedIn ledger

| ID / source | Type | Method / dataset | Claim or topic | Relevance | Reproducibility | Limitation / treatment |
|---|---|---|---|---|---|---|
| P01 · [Tarek Mansour on SIG joining Kalshi](https://www.linkedin.com/posts/mansourtarek_today-marks-a-pivotal-moment-for-kalshi-and-activity-7181308949923528705-M0Rg) | First-person company announcement | No research dataset | Institutional market making and available size | Motivates liquidity-state and incentive questions | Public post only | Promotional liquidity figures are not used as measured evidence or current capacity |
| P02 · [Neil Nachnani on joining Kalshi engineering](https://www.linkedin.com/posts/neil-nachnani_excited-to-share-that-ive-joined-kalshi-activity-7386174568882761729-4Ye_) | First-person career/practitioner post | No research dataset | Options-firm background; broker and market-maker access | Concrete crossover terminology | Public self-description | No inference about proprietary methods or current job responsibilities beyond the post |
| P03 · [Siddhant Dutta on liquidity and market microstructure](https://www.linkedin.com/posts/siddhant-dutta-b2b781202_i-spent-my-summer-following-and-trading-the-activity-7500250614568984577-mvWg) | First-person practitioner post | No research dataset | Liquidity as part of trustworthy price formation | Motivation for separating observation from information | Public post | No empirical test; relative search dates are not used as exact employment dates |
| P04 · [Telonex weekly microstructure post](https://www.linkedin.com/posts/telonex_predictionmarkets-polymarket-datascience-activity-7429289161964154880-HdfN) | Data-vendor commentary | Claimed tick/book/on-chain aggregation | Large variation in updates per trade and sweep structure | Event taxonomy and activity-count confounding | Underlying weekly computation not reproduced | Vendor assertions; none of its reported numbers calibrates this dossier |
| P05 · [ClickHouse on Polymarket's data stack](https://www.linkedin.com/posts/clickhouseinc_how-polymarket-scaled-their-data-stack-with-activity-7419828257207947264-pLBo) | Vendor engineering case study | Analytics/API architecture | Transaction storage and analytics separated | Analytics latency differs from matching-engine/feed latency | Public named case study; no system benchmark reproduced | API latency does not measure market information latency |
| P06 · [Nicolas Becas Azagra on latency](https://www.linkedin.com/posts/nicolasbecas_softwareengineering-algorithmictrading-activity-7486316772707721217-E9Z0) | Builder commentary | No validated dataset | Contrasts venue latency problems; calls Polymarket a mempool problem | Useful example of a claim requiring architecture checks | Public text | Reject blanket mempool interpretation: settlement logs do not define the entire off-chain matching and quote path [G08] |

Additional searches surfaced Nasdaq-related reposts, recruitment profiles and spectacular profit stories. These were screened out as evidence. They are not counted as substantive practitioner reviews or used to infer identities, causal mechanisms or achievable returns.

### 3.6 Exchange/software documentation

| ID / source | Type / scope | Contribution | Limitation |
|---|---|---|---|
| D01 · [Polymarket real-time data documentation](https://docs.polymarket.com/market-data/realtime-data) | Current official feed schema | Distinguishes book/price changes from last-trade messages and their fields | Version at capture must match; a field name alone does not establish historical aggressor correctness |
| D02 · [Kalshi orderbook updates](https://docs.kalshi.com/websockets/orderbook-updates) | Current official stream schema | Initial snapshot, sequenced incremental changes and signed quantity deltas | Apply the documented subscription sequence scope; current fixed-point field names need historical version handling |
| D03 · [SciPy false-discovery control](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.false_discovery_control.html) | Official implementation documentation | BH/BY distinction and assumptions | A correct correction routine cannot validate upstream p-values |

### 3.7 GitHub source review: exact snapshots

These are the commits returned during inspection, not floating recommendations to import the latest branch. All five requested repositories were inspected beyond their README. Additional method repositories and exchange code bring the total to eight.

| ID | Repository | Reviewed commit |
|---|---|---|
| G01 | [philippdubach/polymarket-microstructure](https://github.com/philippdubach/polymarket-microstructure) | `c20b9037bc67f3602ec1971b3b6e7283ae2eee7f` |
| G02 | [Jon-Becker/prediction-market-analysis](https://github.com/Jon-Becker/prediction-market-analysis) | `7fbbb1ebefedf20ce8dda7fe4ee6ecb05e80e9d8` |
| G03 | [gregyoung14/openmarket](https://github.com/gregyoung14/openmarket) | `0acbbff81d19d13b2ac99529fd663c3aa19963b2` |
| G04 | [gordonkoehn/prediction-market-microstructure](https://github.com/gordonkoehn/prediction-market-microstructure) | `c96d34edb2432c41d84885a77ba07263ffe22884` |
| G05 | [namanhzz/prediction-market-calibration](https://github.com/namanhzz/prediction-market-calibration) | `143ca8739bbbd0663da2e9d50ee8012199110ec0` |
| G06 | [jakobrunge/tigramite](https://github.com/jakobrunge/tigramite) | `ff3ff13e1481073b8c5833a6fde1c304627a208e` |
| G07 | [X-DataInitiative/tick](https://github.com/X-DataInitiative/tick) | `a40d19f868c22469be90c1e6c50bc3dab26ff070` |
| G08 | [Polymarket/ctf-exchange](https://github.com/Polymarket/ctf-exchange) | `ed5c7708b7be3aa98bf5f0c6602b57cc498e2ef4` |

**G01 — replay and direction join.** Inspected [`polydata/lob.py`](https://github.com/philippdubach/polymarket-microstructure/blob/c20b9037bc67f3602ec1971b3b6e7283ae2eee7f/polydata/lob.py), [`resample.py`](https://github.com/philippdubach/polymarket-microstructure/blob/c20b9037bc67f3602ec1971b3b6e7283ae2eee7f/polydata/resample.py) and [`onchain/join.py`](https://github.com/philippdubach/polymarket-microstructure/blob/c20b9037bc67f3602ec1971b3b6e7283ae2eee7f/polydata/onchain/join.py). Snapshot replacement, zero-size removal, decimal price keys and quantity replacement are useful patterns. The sampler consumes records up to its sample timestamp, but the inspected function does not impose an age limit or validate continuity; last state persists. Its caller must supply ordered, valid records. The join aggregates by block/token/price and selects the first sign within a bucket; mixed-sign buckets need an explicit policy. Empty/no-match sign agreement defaults to 1.0, which is not evidence of accuracy. Useful replication scaffolding, not a drop-in measurement authority.

**G02 — pseudo-replication risk in inference.** Inspected [`src/analysis/kalshi/statistical_tests.py`](https://github.com/Jon-Becker/prediction-market-analysis/blob/7fbbb1ebefedf20ce8dda7fe4ee6ecb05e80e9d8/src/analysis/kalshi/statistical_tests.py). DuckDB joins make outcome/price accounting inspectable. However `_test_yes_no_asymmetry` uses contract totals as the sample size of a two-proportion test; `_test_maker_direction` repeats returns by contract count and runs an independent-sample t-test. Repeated contracts sharing settlement outcomes are not independent trials. `_test_trade_size_by_role` compares two amounts computed from the same fills with an independent-sample rank test. The inspected summary counts raw p-values below 0.05 across tests. These paths must not supply project significance claims. This is a scoped critique of the file, not an audit of all repository outputs.

**G03 — descriptive pairing versus available features.** Inspected [`crates/recorder/src/db.rs`](https://github.com/gregyoung14/openmarket/blob/0acbbff81d19d13b2ac99529fd663c3aa19963b2/crates/recorder/src/db.rs), [`lag.rs`](https://github.com/gregyoung14/openmarket/blob/0acbbff81d19d13b2ac99529fd663c3aa19963b2/crates/recorder/src/lag.rs) and [`normalize.rs`](https://github.com/gregyoung14/openmarket/blob/0acbbff81d19d13b2ac99529fd663c3aa19963b2/crates/recorder/src/normalize.rs). `find_nearest_binance` searches both before and after a Polymarket timestamp. This is legitimate for descriptive pairing, as the paper explains, but cannot be reused as an as-of feature join. The lag file's raw BTC-price-versus-binary-bid difference is not an economically comparable price spread; its tick-rule signed-volume proxy is not book OFI. Normalisation retains source and ingestion clocks but substitutes ingestion time when source time is absent; a separate provenance flag would be necessary for timing inference. A missing best bid can fall back to the changed level's price, which need not be the best bid. None of these observations asserts that the paper's separate walk-forward model uses the suspect columns.

**G04 — missingness and units.** Inspected [`analysis/microstructure.py`](https://github.com/gordonkoehn/prediction-market-microstructure/blob/c96d34edb2432c41d84885a77ba07263ffe22884/src/prediction_market_microstructure/analysis/microstructure.py). Small functions expose absolute/relative spreads and depth calculations clearly. Nonpositive quotes return zero spread; a missing side returns zero depth. Those sentinels conflate unavailable measurements with genuine states. Depth iteration assumes pre-sorted levels and breaks at the first out-of-band level. A percentage-of-mid depth band also changes meaning with binary probability. Import concepts only with explicit validity, sorting and absolute-tick/probability units.

**G05 — useful revisions and different bootstrap units.** Inspected [`src/calibration.py`](https://github.com/namanhzz/prediction-market-calibration/blob/143ca8739bbbd0663da2e9d50ee8012199110ec0/src/calibration.py), [`scripts/run_robustness.py`](https://github.com/namanhzz/prediction-market-calibration/blob/143ca8739bbbd0663da2e9d50ee8012199110ec0/scripts/run_robustness.py), and [`run_revision_diagnostics.py`](https://github.com/namanhzz/prediction-market-calibration/blob/143ca8739bbbd0663da2e9d50ee8012199110ec0/scripts/run_revision_diagnostics.py). Event-clustered score sums, deterministic cluster indexing, cluster bootstrap and intercept diagnostics are useful. The helper `bootstrap_whale_effect` instead resamples arrays of large/single calibration slopes; it is not the event bootstrap in the robustness script. Specify which estimand and resampling unit a result uses. Logit clipping is fixed in code; it is still a modelling choice. Type-I decomposition depends on component order. Existing event-cluster methods do not make two-election asymptotics reliable.

**G06 — conditional surrogates are substantive.** Inspected [`tigramite/independence_tests/cmiknn.py`](https://github.com/jakobrunge/tigramite/blob/ff3ff13e1481073b8c5833a6fde1c304627a208e/tigramite/independence_tests/cmiknn.py). It exposes neighbour count, transforms, random state and conditional shuffling. For nonempty conditioning sets it shuffles within conditioning-neighbourhoods instead of using an unconditional block shuffle. The estimator is documented for continuous variables and adds small noise to break ties. Sparse zero-inflated price changes violate the spirit of a naive continuous-distance application. Rank transforms estimated on an evaluation window also cannot silently become predictive features. Treat it as a carefully configured diagnostic, not automatic causal discovery.

**G07 — a fitted Hawkes graph still needs identification.** Inspected [`tick/hawkes/inference/hawkes_adm4.py`](https://github.com/X-DataInitiative/tick/blob/a40d19f868c22469be90c1e6c50bc3dab26ff070/tick/hawkes/inference/hawkes_adm4.py). The implementation exposes decay, sparse/nuclear penalties, starting values, event realisations and likelihood scoring. An unspecified adjacency start uses random values; reproducibility needs explicit starts or controlled randomness. The fitted baseline is a per-node vector in this interface, not automatically an election-news baseline. Convergence and a nonzero adjacency are insufficient evidence of information transmission; assess stability and a time-varying no-cross model separately.

**G08 — liquidity role is not the event field name.** Inspected [`src/exchange/mixins/Trading.sol`](https://github.com/Polymarket/ctf-exchange/blob/ed5c7708b7be3aa98bf5f0c6602b57cc498e2ef4/src/exchange/mixins/Trading.sol). `_matchOrders` emits a taker-order summary with `takerOrder.maker` in the event's maker position and the exchange in its taker position; `_fillMakerOrder` emits maker legs. `_deriveMatchType` distinguishes same-side buy minting, same-side sell merging and complementary trades. `_deriveAssetIds` uses an individual order's side, so rejecting records where neither asset is collateral does not by itself detect every mint/merge match. Derive liquidity role and economic direction from the transaction path; avoid summary/leg double counting. This reviewed contract is not automatically the ABI or deployment active for every historical event.

No tests or CI were run for these external repositories. The review establishes visible implementation behaviour, not reproduction of their empirical conclusions.

## 4. Quant Mechanism Atlas

### 4.1 Mathematical object and observation process

Use a multiplex graph, not a single correlation network:

\[
G_t=(V_t,E^{payoff},E^{semantic},E^{latent}_t,E^{response}_t,E^{liquidity}_t).
\]

One node represents one canonical condition on a declared probability axis. YES/NO views of the same condition are measurement channels, not independent evidence. Hyperedges are preferable for exhaustive partitions and multi-leg payoff constraints; a sum-to-one family cannot always be represented faithfully by independent pairwise edges.

For a conceptual latent information state \(z_t\), let the efficient conditional probability be \(p_i^*(t)=h_i(z_t)\). What is observed is closer to

\[
\tilde p_i(t)=Q_i\{p_i^*(\tau_i(t)),s_i(\tau_i),D_i(\tau_i),I_i(\tau_i)\}+\epsilon_i(t),
\qquad a_i(t)=t-\tau_i(t).
\]

Here \(\tau_i(t)\) is the last valid observation time; \(Q_i\) represents tick rounding, spread and inventory-related quote formation. Observation/update intensity may itself depend on information, liquidity and collector state. The model is conceptual, not an assumption that midquotes are unbiased or that a unique efficient probability is observable.

Three times must be distinguished: source event time, local receipt/availability time, and on-chain settlement time. A predictor belongs to the available information set only after the local system could have received it. Ordering by a more precise-looking source timestamp does not make it available earlier. Unknown cross-clock offsets can make sub-offset lead direction unidentified.

The following atlas retains **11 mechanism classes for possible interrogation**, not 11 hypotheses authorised for testing. Each must pass the data and multiplicity gates in later sections. PRE and ACTIVE below mean the project's frozen pre-election and active-results regimes; they are not newly defined windows.

### M01 — asynchronous stale-market adjustment

| Field | Prior specification |
|---|---|
| System intuition | A target displays an older information state while a related source has refreshed. |
| Observables | Source innovation; target quote age, spread, update history and validity; common-information proxy. |
| Temporal signature | Target response at its next valid update; clock-time response depends on waiting time. |
| State dependence / regimes | Continuous age and update-rate interactions; PRE or ACTIVE separately. |
| Model / baseline | Small target-history regression plus source innovation and age; benchmark against own history plus common factor. |
| Challenger | Two-part model: update hazard and signed change conditional on update. |
| Required null | Shared latent shocks observed through market-specific update clocks, with no market-to-market transmission. |
| Negative control | Age/activity-matched unrelated target; diagnostic thinning of the fast source. |
| Falsifier | No incremental direction after age/common-shock controls, or timing is entirely reproduced by observation-clock null. |
| Confounders | Missing messages, rounding, source loading, selection of target updates. |
| Minimum data | Valid age/uptime; repeated independent source episodes and target updates; enough follow-up for censoring. |
| Burden / interpretation / overfit | Low–medium / high / moderate. Do not label stale adjustment as privately informed leadership. |

### M02 — directed cross-book response

| Field | Prior specification |
|---|---|
| System intuition | Source book pressure provides incremental information about a related target. |
| Observables | Valid source OFI or explicitly named depth imbalance; target returns and own-book pressure. |
| Temporal signature | Source precedes target movement beyond contemporaneous common response. |
| State dependence / regimes | Probability level and pre-shock depth; both frozen regimes assessed separately. |
| Model / baseline | Own-book, own-return and family-history regression; add one source term. |
| Challenger | Small regularised multivariate model on a predeclared semantic neighbourhood. |
| Required null | Conditional no-cross model retaining own dynamics, common news and observation masks. |
| Negative control | Reverse-time/placebo lags and activity-matched nonedges; neither alone establishes causality. |
| Falsifier | Source effect disappears with richer own-book information or realistic common-shock null. |
| Confounders | Simultaneous repricing; incomplete own depth; unobserved common orders. |
| Minimum data | Continuous valid L2 changes for OFI; snapshots alone permit only a different imbalance variable. |
| Burden / interpretation / overfit | Medium / medium–high / high when many directed edges are searched. |

### M03 — quote-update excitation

| Field | Prior specification |
|---|---|
| System intuition | Source activity forecasts when another book will change, regardless of direction. |
| Observables | Deduplicated meaningful update events, exposure time, source/target activity and collector uptime. |
| Temporal signature | Incremental target hazard after source updates. |
| State dependence / regimes | Event-time baseline and own excitation; ACTIVE especially requires nonstationary controls. |
| Model / baseline | Lagged update-rate association, then discrete-time hazard/Poisson model with own history and family activity. |
| Challenger | Small exponential-kernel Hawkes model with explicit nonstationary baseline. |
| Required null | Own-exciting processes driven by common exogenous activity, with cross coefficients zero. |
| Negative control | Unrelated books sharing the collector; heartbeat/reconnect events. |
| Falsifier | No held-forward hazard improvement; effect reproduced by batching or common intensity. |
| Confounders | Snapshot refreshes counted as new information, throttling, market-wide news bursts. |
| Minimum data | Actual event timestamps and gaps; enough events per kernel parameter and repeated bursts. |
| Burden / interpretation / overfit | Low baseline, medium challenger / high for timing, low for causal influence / moderate–high. |

### M04 — latent-factor residual correction

| Field | Prior specification |
|---|---|
| System intuition | A target departs from a low-dimensional family state and later adjusts. |
| Observables | As-of family prices, masks, target age/spread and leave-target-out state estimate. |
| Temporal signature | Target future change aligns with a previously observable residual; innovations may propagate across residuals. |
| State dependence / regimes | Residual magnitude and measurement uncertainty; do not assume PRE loadings work in ACTIVE. |
| Model / baseline | Semantic average or static training-only PCA, leaving target out. |
| Challenger | Small filtered dynamic factor model with explicit missing observations. |
| Required null | Latent common-state model without residual coupling, replayed through observation clocks. |
| Negative control | Synthetic stale observations generated from the fitted no-coupling model. |
| Falsifier | No gain over own history/semantic aggregate; rank/loadings are unstable; correction is entirely stale-quote catch-up. |
| Confounders | Target included in its predictor; full-sample centring; mechanical simplex constraints. |
| Minimum data | Connected overlap across several nonredundant nodes, sufficient training and filtered evaluation periods. |
| Burden / interpretation / overfit | Medium / medium / high for estimated rank and loadings. |

### M05 — semantic family-local propagation

| Field | Prior specification |
|---|---|
| System intuition | Information affects related payoffs more coherently than unrelated but equally active books. |
| Observables | Ex-ante semantic/hypergraph labels; innovations; activity, price level and liquidity. |
| Temporal signature | Stronger conditional response within a specified relation type. |
| State dependence / regimes | Edge type and regime; relation sign can be positive, negative or unspecified. |
| Model / baseline | Pairwise response with an edge-type interaction. |
| Challenger | Degree-normalised one-step graph aggregation with fixed semantic edges. |
| Required null | Activity/liquidity-matched edge-label randomisation; separately preserve common shocks. |
| Negative control | Matched nonedges and payoff-duplicate removal. |
| Falsifier | No incremental semantic effect after matching; graph aggregate fails to beat pairwise model. |
| Confounders | Degree tracks liquidity; semantic labels selected after seeing correlations; shared settlement. |
| Minimum data | Both edges and comparable nonedges; more than one family motif. |
| Burden / interpretation / overfit | Low–medium / high with fixed graph / moderate. |

### M06 — liquidity-conditioned transmission

| Field | Prior specification |
|---|---|
| System intuition | The same innovation produces different quote responses at different pre-existing liquidity states. |
| Observables | Source innovation; target pre-shock spread/depth/imbalance and their validity. |
| Temporal signature | Conditional response amplitude or delay, not merely larger unconditional volatility. |
| State dependence / regimes | Continuous source-shock × target-liquidity term; PRE/ACTIVE explicit. |
| Model / baseline | Additive linear response with liquidity main effects. |
| Challenger | One predeclared interaction, then a low-degree smooth interaction if justified. |
| Required null | No-interaction model with heteroskedasticity and serial dependence retained. |
| Negative control | Post-shock liquidity excluded from predictors; matched shock episodes. |
| Falsifier | Interaction adds no out-of-sample information or only reflects changing noise variance. |
| Confounders | Liquidity responds to the shock; spread/price coupling; sparse depth. |
| Minimum data | Joint support across shock and liquidity states, multiple episodes, valid pre-shock depth. |
| Burden / interpretation / overfit | Low / high / moderate; unsupported depth removes this branch. |

### M07 — activity-conditioned propagation

| Field | Prior specification |
|---|---|
| System intuition | Information-processing speed changes with the family's activity state. |
| Observables | Lagged family update/fill intensity excluding target where appropriate; source innovation and target response. |
| Temporal signature | Response shape changes with intensity, independently of mechanical event-count differences. |
| State dependence / regimes | Continuous trailing activity; no retrospectively selected “busy periods.” |
| Model / baseline | Additive activity/own-history model plus one shock × activity interaction. |
| Challenger | Online intensity-time response model, compared on the same clock-time target. |
| Required null | Shared time-varying activity with independent signed innovations conditional on common state. |
| Negative control | Unsigned response versus signed response; activity-matched nonedges. |
| Falsifier | Only update hazard changes, with no extra directional response; retain timing claim separately if supported. |
| Confounders | Collector congestion; counts partly constructed from the target; regime leakage. |
| Minimum data | Multiple activity states within a regime and nonzero exposure outside bursts. |
| Burden / interpretation / overfit | Low–medium / medium / moderate. |

### M08 — volatility transmission without directional transmission

| Field | Prior specification |
|---|---|
| System intuition | A source move reveals uncertainty or news intensity rather than the sign of another outcome. |
| Observables | Absolute/squared source innovations and future target magnitude, with own volatility and probability controls. |
| Temporal signature | Future magnitude response even if signed expectation is zero. |
| State dependence / regimes | Distance from boundaries and pre-shock spread; analyse PRE/ACTIVE separately. |
| Model / baseline | Target own-volatility plus common-activity model. |
| Challenger | Small magnitude local projection with one interaction. |
| Required null | Shared heteroskedastic news process with no incremental source magnitude information. |
| Negative control | Randomised signs retaining magnitudes; unrelated activity-matched books. |
| Falsifier | Effect is fully explained by own volatility, common bursts or quote rounding. |
| Confounders | Bounded return variance, spread changes, stale catch-up jumps. |
| Minimum data | Repeated magnitude episodes rather than one election-night jump. |
| Burden / interpretation / overfit | Low / high for predictive magnitude / moderate. |

### M09 — state-dependent response delay

| Field | Prior specification |
|---|---|
| System intuition | Lag duration depends on state rather than a constant pairwise offset. |
| Observables | Source event, target next-change time, pre-event age/liquidity/activity, censoring. |
| Temporal signature | Conditional delay distribution changes smoothly with observed state. |
| State dependence / regimes | Prespecified continuous covariates; compare within regime. |
| Model / baseline | Constant-delay or own-duration survival model. |
| Challenger | Covariate-dependent hazard; direction estimated in a separate mark model. |
| Required null | State-dependent target observation clock with no source-content effect. |
| Negative control | Source events with matched activity but unrelated semantic content. |
| Falsifier | Delay depends only on observation frequency; source content adds nothing. |
| Confounders | Right censoring, choosing only eventual responders, repeated source shocks before a target update. |
| Minimum data | Clear risk sets, observable nonresponses, and independent event clusters. |
| Burden / interpretation / overfit | Medium / high / moderate. M01/M03 overlap must not inflate discoveries. |

### M10 — structural-residual shock propagation

| Field | Prior specification |
|---|---|
| System intuition | An observable departure from a verified payoff constraint is followed by coordinated quote adjustment. |
| Observables | Complete family quotes; settlement-compatible constraint; leg ages, spreads and depth. |
| Temporal signature | Residual narrows through identifiable leg adjustments after a source innovation. |
| State dependence / regimes | Continuous residual × age/spread; regime-specific. |
| Model / baseline | Own residual persistence plus asynchronous mechanical-constraint model. |
| Challenger | Constrained small state-space response model. |
| Required null | Perfect latent payoff coherence viewed through asynchronous noisy books. |
| Negative control | Synchronous matched-age view; semantically similar but non-equivalent payoff family. |
| Falsifier | Residual lies within quote uncertainty, vanishes under fresh alignment, or reflects omitted outcomes. |
| Confounders | Nonexhaustive partitions, rule differences, unavailable legs, bid/ask asymmetry. |
| Minimum data | Complete verified constraint and contemporaneously available valid legs. |
| Burden / interpretation / overfit | Medium / high if constraints exact / moderate. No inference of executable arbitrage. |

### M11 — latent location-versus-uncertainty propagation

| Field | Prior specification |
|---|---|
| System intuition | Threshold contracts respond differently to a shift in expected seats/vote share and a change in uncertainty. |
| Observables | Ordered same-underlying threshold probabilities, joint availability and target family responses. |
| Temporal signature | Coherent level shift versus widening/narrowing probability surface precedes different target responses. |
| State dependence / regimes | Distance from latent centre; uncertainty; event-time regime. |
| Model / baseline | A single semantic level factor or static PCA. |
| Challenger | Two-parameter monotone surface, estimated only from available non-target quotes. |
| Required null | One-factor latent state with asynchronous observation and binary rounding. |
| Negative control | Randomised threshold labels as a diagnostic; leave-one-threshold-out prediction. |
| Falsifier | Second component unstable, unidentifiable from available strikes, or no held-forward gain. |
| Confounders | Incomparable settlement rules, sparse thresholds, tail clipping, mechanical nesting. |
| Minimum data | Several nonredundant thresholds with overlap and sensitivity to both parameters. Two quotes fitting two parameters is no validation. |
| Burden / interpretation / overfit | Medium / high for a valid surface / high with small cross-sections. Conditional retention only. |

**Rejected mechanism interpretations before seeing 004B:** autonomous graph-centrality leadership without activity controls; near-critical Hawkes “information cascades” inferred from clustering alone; settlement-calibration bias relabelled as a short-horizon lead; and universal one-factor mean reversion across semantically distinct payoffs. Rejection concerns these strong interpretations, not every possible use of the underlying tools.

## 5. Method evaluation matrix

Demand refers to independent usable variation per fitted parameter and state, not raw row count. There is no defensible universal minimum sample count. Any eventual numerical feasibility gate needs a declared power/type-I-error simulation under the proposed observation process, not a threshold reverse-engineered from desirable results.

| Method | Detects | Assumptions | Sample demand | Missingness / timing sensitivity | Leakage risk | Interpretation | Cost | Project suitability / baseline to beat |
|---|---|---|---|---|---|---|---|---|
| Lagged regression / Granger | Incremental linear predictability | Stable conditional mean over window; adequate histories | Low–moderate | High if gaps compressed or quotes stale | Lag construction, full-sample scale | Predictive, not intervention-causal | Low | Preferred starting point; beat own history + common information |
| Sparse VAR | Joint lag network | Approximate sparsity, local stationarity | High: candidate lag dimension matters | High | Penalty selection and preprocessing | Moderate | Medium | Small semantic neighbourhood only; beat restricted linear model |
| Local projections | Horizon-specific responses | Predetermined predictors; shock identification separate | Moderate per horizon | High | Same-bin contamination, overlapping labels | High | Low–medium | Preferred for a small horizon family; beat additive response |
| Hasbrouck information share | Common-price innovation attribution | Cointegrated same-payoff prices; innovation identification | Substantial stable joint series | Very high | Model/order selection | Moderate | Medium | Generally reject for different outcomes; identical-payoff local diagnostic only |
| Gonzalo–Granger share | Permanent-component weights | Valid error-correction/common-trend representation | Substantial | Very high | Same as above | Moderate | Medium | Same transfer restriction; not interchangeable with information share |
| HY/shifted overlap contrast | Asynchronous covariance/lag | Relevant continuous-time assumptions; known clocks | Dense enough overlap and repeated variation | Handles nonalignment, not missing capture or all noise | Choosing lag peak on same sample | Moderate | Medium | Sensitivity diagnostic; beat naive aligned correlation |
| Static PCA / SVD | Low-dimensional variation | Stable geometry, sufficient overlap | Moderate, scales with cross-section | High under zero fill and heterogeneous staleness | Full-sample loadings/scaling | Moderate | Low | Small training-only baseline; beat semantic aggregate |
| Probabilistic PCA | Latent Gaussian geometry | Linear loadings and specified observation noise | Moderate | Can handle absent entries; not endogenous missingness automatically | Joint fit across evaluation window | Moderate | Low–medium | Challenger to PCA, not default |
| Dynamic factor / Kalman | Evolving common state | Identified transitions/loadings/noise | Moderate–high | Explicit masks help; update selection remains | Smoothing, future-fitted parameters | Moderate | Medium | Only if filtered model beats static PCA |
| Robust PCA, low-rank + sparse | Common component plus unusual entries | Incoherence and genuinely sparse corruption | High relative to small panel | Severe structured-missingness risk | Full-window decomposition | Low–moderate | Medium | Mostly diagnostic; frequent common news is not sparse corruption |
| Latent sparse precision | Hidden factors plus residual associations | Identifiable decomposition; distributional conditions | High | High; PSD covariance required | Regularisation and full-sample selection | Low–moderate | High | Deferred; simpler factor-residual model first |
| Graphical lasso | Undirected conditional associations | Suitable Gaussian approximation, adequate covariance | High versus node count | High | Future-selected graph | Moderate | Medium | Descriptive only; cannot orient a causal edge |
| Dynamic covariance / spectral analysis | Evolving co-movement/frequency structure | Local stationarity; enough cycles | High for fine frequency bands | Interpolation and gaps distort spectrum | Centred windows, whole-event eigenvectors | Moderate | Medium | Diagnostic; beat rolling shrinkage covariance |
| Mutual information | General dependence | Estimable density/discretisation | High with multiple dimensions | Very high for zero masses/ties | Bin/estimator tuning | Low–moderate | Medium–high | No direction; beat correlation/nonlinear univariate baseline |
| Transfer entropy / CMI | Conditional temporal dependence | Adequate conditioning and estimator calibration | Very high with history/state dimension | Very high | Lag search, transformations, smoothing | Predictive unless stronger assumptions | High | Restricted challenger only; beat regularised linear model |
| PCMCI / dynamic Bayesian network | Conditional lag graph | Markov/faithfulness assumptions; hidden causes addressed | High | Very high | Graph selection and evaluation reuse | Moderate under assumptions | High | Not a primary network discovery engine for few elections |
| Hazard / duration model | Next-update probability or waiting time | Correct risk sets, censoring, exposure | Moderate event count | Missing intervals must leave risk set | Conditioning on eventual update | High | Low | Preferred event-network baseline |
| Hawkes / marked Hawkes | Self/cross event excitation; mark behaviour if modelled | Event definition, baseline, kernel validity; stationary model requires stable integrated kernel | High per source–target kernel | Extremely sensitive | Kernel/decay search; post-event marks | Moderate | Medium–high | Small challenger to hazard model and no-cross baseline |
| HMM / regime switching | Recurrent latent states | Repeated transitions, identifiable emissions | High independent transitions | High | Smoothed state probabilities | Moderate | Medium | Deferred if only one transition per election; beat frozen regimes |
| Online change-point model | Distributional instability | Declared hazard and predictive likelihood | Repeated changes for evaluation | Gaps mimic changes | Offline relabelling | Moderate | Medium | Diagnostic only; no alteration of frozen windows |
| Graph centrality / embeddings | Structural position / compressed graph | Meaningful stable graph | High across distinct motifs for generalisation | Activity shapes observed degree | Graph estimated using evaluation data | Low–moderate | Low–high | Fixed semantic aggregation first; reject learned prestige scores |
| Smooth interaction/GAM | Continuous state-dependent response | Joint support and restrained smoothness | Moderate–high | Missing covariates select sample | Knots, transforms or states selected on outcomes | High with few terms | Low–medium | Preferred nonlinear challenger after additive model |
| Neural graph / attention model | Flexible cross-node dynamics | Large diverse training set and stable task | Very high independent events | Complex masking often leaks | Many tuning degrees of freedom | Low | High | Reject for current independent-event budget |

### Transferring conventional microstructure correctly

Define a canonical YES probability change in **probability points** before considering relative returns. A one-cent move at 0.01 is not comparable in percentage-return units to one at 0.50. A clipped logit is a sensitivity transform, not a cure for boundaries. Exact 0/1 quotes require a declared observation policy, not silent arbitrary clipping. A finite-horizon bounded probability process is not globally an unconstrained I(1) asset price.

Distinguish three different variables:

- **Depth imbalance:** \((D^b-D^a)/(D^b+D^a)\), defined only with valid sides and positive denominator.
- **Book OFI:** event-by-event changes in best-quote supply/demand, including quote moves and quantity changes. It requires sufficiently complete replay; periodic depth differences do not reconstruct every intervening event.
- **Signed traded volume:** aggressor-oriented executed volume. It needs a validated trade decoder and its own availability timestamp.

Kyle-style regressions need the third variable correctly signed and units declared. Effective spread needs a quote observable before the trade; adverse-selection markouts need a specified future quote and valid horizon. Maker rebates alone are not a complete role classifier. Fee absence is not proof of taker status. Block settlement timestamps are too coarse to establish sub-block execution leadership.

For repeated related outcomes, a semantically plausible sign does not establish an information channel. A candidate's improving winner probability and another's declining probability may be the same common shock and an exhaustive-family constraint.

## 6. Directionality failure modes and asynchronous checklist

| Failure mode | False appearance | Required diagnostic/control |
|---|---|---|
| Different update frequencies | Fast book appears to lead every slow book | Include trailing intensity and target age; compare next-update and clock-time responses; simulate no-link common state on observed clocks |
| Stale midpoint | Delayed catch-up looks like transmission | Record last valid book observation, last best-quote change and last price change separately; use declared freshness masks |
| Common external shock | One observed source appears causal | Lagged leave-target-out common proxy and external timestamps if independently available; no causal claim if common shock remains unidentified |
| Different reaction speeds | Same news resembles source-to-target causation | Explicit shared-news/different-response-speed benchmark; distinguish predictive usefulness from causal origin |
| Shared collector and queues | Decode/subscription order creates arrows | Source/receipt clock comparison; batch indicators; exclude ambiguous ties; unrelated same-collector controls |
| Unknown clock offset/drift | Measured lag reflects clock difference | Offset-uncertainty band and perturbation sensitivity; no direction claim inside unresolved ordering interval |
| Zero-return inflation | Apparent persistence and non-Gaussian dependence | Separate no-update, valid unchanged update, rounded unchanged quote and missing observation |
| Missing-data compression | A row lag means different elapsed times | Preserve timestamps/masks; never drop gaps then shift rows as seconds |
| Different spreads | Midpoint motion is liquidity repricing | Compare bid/ask movement, absolute spread and pre-shock liquidity; common support |
| Different probability levels | Boundary arithmetic creates different response scale | Probability-point response; predetermined level interactions; alternate transform sensitivity |
| Resolution proximity | Terminal learning drives trends and variance changes | Frozen event regimes and as-of known scheduled horizon; do not use realised settlement time as a predictive covariate |
| Factor loading differences | Bigger-loading market appears to lead | Estimate loadings on training data, condition on common state, test asynchronous no-link model |
| Same-bin contamination | Predictor contains part of target response | Explicit half-open feature/label windows; all predictor availability before target interval |
| Full-sample normalisation | Future scale/regime information enters features | Training-only or one-sided updating; same transformation in every null/refit |
| Two-sided smoothing | Historical factors or states contain future information | Filter-only evaluation; smoothing permitted only within parameter-training data |
| Duplicate YES/NO channels | Mechanical mirror creates a strong network edge | Canonical condition axis; mirror measurements as reconciliation controls |
| Trigger selection | Extreme noise mechanically mean-reverts | Predeclared trigger universe; interaction across full support; simulate trigger rule inside null |
| Post-treatment controls | Controlling for a mediator hides or creates effects | Use pre-shock states; distinguish total from residual/direct predictive estimand |
| Conditioning on eventual update | Only responsive markets enter sample | Full risk set and censoring; report nonresponses and unavailable periods |
| Incomplete quote lifecycle | Cancellations/new orders labelled trades | Use aggregate changes as aggregate changes; do not infer intent or individual queue position |
| Current metadata backfill | Final universe/rules leak backwards | Versioned identifiers and point-in-time eligibility; report post hoc reconstruction separately |
| Search over lag, pair and state | Best arrow is selection noise | Count the full candidate family; null must rerun all searched transforms and selections |

**Interpretation discipline:** controlling for quote age may remove the very observation-delay mechanism M01 is intended to describe. Therefore report both the total available-source association and the incremental association conditional on the observation process. The former can be predictive; only the latter starts to challenge the stale-observation explanation. Neither by itself proves structural transmission.

Two future targets must remain separate:

\[
Y^{clock}_{j,t,h}=p_j(t+h)-p_j(t),\qquad
Y^{update}_{j,t}=p_j(T_j^+(t))-p_j(t).
\]

The second uses a random, possibly censored horizon. It cannot be evaluated only on observations that eventually update. A fixed-clock endpoint with an invalid or stale quote is not automatically a zero return. Report the measurement policy and its selection effect.

## 7. Null-model toolkit

First state what is being destroyed: unconditional alignment, incremental signed response, conditional interaction, cross-excitation, or semantic enrichment. No single surrogate preserves every nuisance while destroying every possible alternative.

| Null/control | Preserves | Destroys/tests | Invalid or misleading when |
|---|---|---|---|
| Within-regime circular shift | Each shifted series' empirical path and autocorrelation on a circle; move mask with values if specified | Absolute pair alignment | One-off news/regime transitions are moved to impossible times; wrap-around creates false neighbours; shared shocks are erased when they should remain |
| Block permutation | Within-block dependence, state and mask pattern if moved jointly | Cross-block alignment/order | Blocks shorter than response/dependence scale; too few exchangeable blocks; event-time drift is material |
| Stationary bootstrap, joint vectors | Approximate serial and cross-sectional dependence | Sampling uncertainty around the fitted statistic | Mistaken for a no-link null; pooled PRE/ACTIVE or whole-election iid logic |
| Null-restricted residual block bootstrap | Declared no-effect mean model and nuisance dynamics; residual blocks approximate dependence | Specific coefficient/interaction | Residuals still encode missing confounding; uncentred unrestricted bootstrap is mislabelled null; heteroskedasticity/observation model wrong |
| Phase randomisation | Fourier power spectrum for regularly sampled stationary series | Higher-order/nonlinear structure and, with independent phases, alignment | Sparse irregular updates, gaps, boundaries, heteroskedastic news bursts; missingness is not preserved |
| IAAFT-style surrogate | Approximately spectrum and marginal distribution | Certain nonlinear temporal structure | Mistaken for preservation of activity clocks or conditional states; static marginal matching is insufficient |
| Conditional mark randomisation | Event times, exposure and chosen mark-state strata | Incremental sign/content information | Sign serial dependence or state dependence destroyed inadvertently; strata chosen from responses |
| Activity-matched nonedges | Approximate activity, age, price-level and liquidity comparability | Semantic-specific excess association | Nonedges share unmodelled exposure; poor overlap; matching depends on future outcomes |
| Degree-preserving rewiring | Chosen degree sequence | Extra graph organisation beyond degrees | Semantic constraints broken, graph tiny, node covariates uncontrolled; degree itself was inferred from searched significance |
| Common-state asynchronous simulation | Shared shocks, node loadings, market-specific observation process under specified model | Need for residual coupling/transmission | Model cannot reproduce nuisance statistics; observed clocks are informative about state but treated exogenous |
| No-cross point-process simulation | Own excitation, declared time-varying common baseline and exposure | Incremental cross-excitation | Baseline flexibility is inadequate or so flexible that it absorbs all effects; missed updates unmodelled |
| Time reversal / lead placebo | A diagnostic version of serial structure | Temporal asymmetry check | Treated as a complete null or causal proof; nonstationary shared shocks are asymmetric too |

### Null construction requirements

1. Freeze the statistic, missingness rule, eligible family, transforms and allowed model selection before drawing nulls.
2. For shared null draws, use a coherent multivariate transformation rather than unrelated pairwise surrogates; otherwise maxima across a graph lose their dependence structure.
3. Distinguish nuisance-model fitting from the tested relationship. Refit every data-dependent step inside each replicate when inference requires it, including factor extraction or lag selection. Do not hold a selected “best” graph fixed and pretend the search did not happen.
4. Check preservation of age distribution, update counts, burst lengths, return autocorrelation, volatility clustering, missingness and probability-level support. A null that matches means but erases the election-night burst is too easy.
5. A fixed-observed-clock null is useful conditionally, but update times themselves can respond to news. A second model of state-dependent arrival, or a sensitivity analysis, is needed before treating the clock as exogenous.
6. Use Monte Carlo p-values \((1+\#\{T_b\ge T_{obs}\})/(B+1)\) with the declared one- or two-sided statistic. The smallest attainable p-value must be compatible with the multiplicity family. More draws improve resolution, not scientific validity.
7. Keep within-election uncertainty separate from across-election transfer. Resampling market rows or within-event blocks does not create new elections.

**Preferred conceptual combination:** an observation-process/common-shock null for identification, plus restricted block-resampling uncertainty for a small predeclared statistic. Circular shifts and matched nonedges are useful sensitivities, not sufficient protection by themselves. This is a prospective toolkit, not an instruction to alter frozen 004B nulls.

## 8. Nonlinear interactions worth testing

Consider a parsimonious conditional response:

\[
Y_{j,t,h}=\alpha_{j,r,h}+\phi_h^\top H_{j,t^-}+\theta_h^\top C_{-j,t^-}
+\beta_h X_{i,t}+\gamma_h^\top Z_{j,t^-}
+\eta_h X_{i,t}g(Z_{j,t^-})+u_{j,t,h}.
\]

The source feature must finish before the target interval begins; notation \(t^-\) means the state known before the source shock. \(H\) is target history and \(C\) a common-information control. A measured innovation is not automatically an exogenous shock. **The no-interaction model must include all main effects.**

| Candidate interaction | Meaning | Required ablation / reason to abandon |
|---|---|---|
| Source innovation × log(1 + target age) | Stale observations may adjust differently | Age-only hazard; fresh-quote subset; abandon direction claim if only updating changes |
| Source innovation × pre-shock spread | Response may depend on quote uncertainty | Separate bid/ask movement; reject if effect is spread widening alone |
| Source innovation × log valid depth | Liquidity buffers or delays repricing | Own depth and source depth separately; abandon if depth support is sparse |
| Source innovation × depth imbalance | Pre-existing pressure may reinforce or oppose a shock | Static imbalance and shock main effects; remove if denominator invalid |
| Source innovation × p(1−p) | Sensitivity differs across probability levels | Absolute-point versus fixed clipped-link sensitivity; no post hoc tail exclusions |
| Source innovation × trailing family intensity | Processing dynamics change with activity | Own intensity and common-news baseline; ensure target not defining its own predictor |
| Source innovation × frozen regime | PRE and ACTIVE may have different response mechanisms | Separate within-event estimates; a regime comparison needs support in each regime |
| Source innovation × prior residual | Family inconsistency may moderate adjustment | Target-excluded factor and age controls; reject unstable residual definition |
| Signed shock × absolute shock size | Larger shocks may have different response strength | Continuous odd response such as x\|x\| versus linear x; boundary and outlier leverage checks |

This is a menu, not permission to test all interactions, bases and horizons. Correlated age, activity, spread and probability states can be weakly identifiable even with many rows. Choose a small scientifically distinct set after the accepted evidence is read, then preregister before any sealed evaluation. If smooth terms are allowed, freeze their basis/degree or a training-only selection rule and count that flexibility. No optimal shock-size threshold is proposed here.

## 9. Latent-factor toolkit and abandonment rules

Low rank can arise from shared news, exact payoff constraints, stale-price sampling, or actual low-dimensional uncertainty. These explanations should not be conflated.

**Semantic surfaces offer an interpretable possibility.** For an illustrative same-underlying threshold family with a normal latent seat/vote variable,

\[
p_k=\Phi((\mu-k)/\sigma),\quad
\partial_\mu p_k=\phi((\mu-k)/\sigma)/\sigma,\quad
\partial_\sigma p_k=-\phi((\mu-k)/\sigma)(\mu-k)/\sigma^2.
\]

A location shift moves all threshold probabilities in the same direction; increasing uncertainty affects thresholds on opposite sides of the centre differently. This derivation motivates a two-dimensional challenger **only for a verified common-underlying family**. It does not assert normally distributed election outcomes or fit any project data. Discrete seats, coalition rules and multimodality can invalidate this surface.

### Safe estimation sequence

1. Verify semantic comparability; remove exact duplicates and represent sum constraints explicitly. An exhaustive K-way probability vector has a mechanically singular direction. That is not an empirical discovery of a latent information factor.
2. Start with raw probability points and a declared semantic aggregate. For exhaustive positive compositions, log-ratio coordinates may be considered, but zeros and omitted outcomes break simple use. Independent clipped logits do not enforce cross-market coherence.
3. Fit centring, scaling, loadings and rank using training observations only. Pairwise-deletion covariance can be non-PSD because each entry uses a different sample. Zero filling and backward filling are unacceptable.
4. In a state-space model, update on observations actually available and propagate uncertainty when none arrive. Repeatedly ingesting a carried-forward stale quote as a fresh observation falsely increases confidence.
5. For a target residual, use a leave-target-out common estimate. For an incremental source test, also assess excluding the source from the common proxy; otherwise the proxy can mechanically absorb the source. These are different estimands and must be declared.
6. Evaluate filtered states, not retrospective smoothed states. EM/smoothing may estimate parameters within the training period, but must not reach into the evaluation interval.
7. Compare subspaces/loadings under predetermined windows, not just sign-sensitive component vectors. Near-equal eigenvalues make individual principal components non-identifiable even when their span is stable.
8. Require incremental held-forward prediction beyond own history, age and semantic aggregate. A visually impressive in-sample reconstruction is insufficient.

**Abandon a factor-based hypothesis** if there is too little overlapping cross-section, rank is driven by one episode, the target cannot be excluded, loadings change under minor defensible preprocessing, residuals reflect stale observations, or a second dimension is unsupported. Keep a descriptive factor plot only if it is labelled descriptive. Low-rank-plus-sparse decomposition cannot reliably distinguish a “special information shock” from measurement error without additional restrictions.

## 10. Event-network toolkit

Define an event before modelling it: received payload, changed L2 level, changed top of book, changed midpoint, trade fill and transaction are different units. One transaction can emit multiple fills; one snapshot can contain an entire book. Counting all of them as equivalent news events changes the result mechanically.

For an update process, an illustrative intensity model is

\[
\lambda_j(t)=\mu_j(t,Z_{t^-})+
\sum_i\int_{s<t}\phi_{ji}(t-s,m_s)\,dN_i(s).
\]

The mark \(m_s\) might be direction or magnitude, but must have been available at event time. In the basic nonnegative stationary linear Hawkes case, the integrated kernel matrix requires spectral radius below one [A13]. That restriction does not by itself validate nonstationary election data. A fitted near-one value is not evidence of critical information diffusion [A14].

Baseline progression: exposure-adjusted lagged update-rate association → target-history/common-activity hazard model → a small cross-event intensity model. Compare predictive likelihood/calibration on subsequent blocks. Inspect time-rescaled residuals, but do not treat a passing diagnostic as identification of the true generating process. The cited calibration critique explicitly warns that different generating processes can pass residual checks.

Keep four targets separate:

| Target | Example estimand | Suitable starting model |
|---|---|---|
| Direction | Expected future signed price change | Linear/local-projection response |
| Magnitude | Expected absolute future change | Own-volatility + common-activity regression |
| Timing | Probability of a valid target update over an interval | Discrete hazard with at-risk exposure |
| Liquidity | Future spread/depth change | Valid-book state transition model |

The joint observed response can be factored conceptually into the chance of an update and the distribution of its mark. Conditioning only on updates can induce selection; fitting a signed response on every carried-forward grid point hides that selection in a mass at zero. Report both parts or state which one the hypothesis concerns.

Online business time may use the integral of a **past-estimated** intensity. Dividing elapsed time by the eventual full-election update count leaks future activity. Clock-time and business-time models must ultimately be compared on a declared common target, not credited for making the target easier by changing its meaning.

## 11. Multiple-testing strategy options

Define a test family across mechanism, predictor class, ordered pair or family aggregate, target type, regime, horizon and allowed transform. Baseline and challenger searches also consume degrees of freedom. Do not report only the most appealing slice.

| Option | Strength | Weakness | Prior disposition |
|---|---|---|---|
| Small family with Holm correction | Simple FWER protection with valid marginal p-values under arbitrary dependence | Conservative and still dependent on p-value validity | Attractive for a small primary set |
| BH across a fixed family | Understandable exploratory FDR | Independence/PRDS not automatic for overlapping network tests | Retain with assumptions explicit and sensitivity |
| BY across a fixed family | Arbitrary-dependence FDR with valid marginals | Harmonic penalty can destroy power | Useful conservative sensitivity, not a cure for bad nulls |
| Joint max-statistic / stepdown | Uses dependence and includes horizon/edge search | Needs valid coherent multivariate null and suitable studentisation; strong-control conditions matter | Candidate for small predeclared family |
| Hierarchical/selected-family FDR | Allocates error across meaningful groups | Naive “screen then BH” fails; target error criterion must be precise | Use only if hierarchy justified before testing [A27] |
| Formal selective inference | Accounts for a specified selection procedure | Highly model-specific; serial, missing and adaptive data complicate guarantees | Defer; sample separation is more transparent |
| Stability selection | Reveals fragile edge/feature choices | Selection frequency is not a p-value or cross-election replication | Diagnostic supplement only |

For BH, ordered p-values are compared to \(kq/m\); BY replaces \(q\) with \(q/\sum_{i=1}^m 1/i\). Directional, update-time and magnitude targets belong to explicitly declared branches of the research family, not convenient new families created after one fails.

With only a few independent elections, report event-specific effects and uncertainty separately. A pooled estimate dominated by one event is not evidence of stable transfer. Leave-one-event-out fitting is useful but only yields as many genuinely distinct transfer tests as there are events; no large-cluster approximation is warranted merely because the panel has many nodes. PRE and ACTIVE within the same election are not independent elections.

**Prospective recommendation:** keep primary questions few, use valid conditional/null-derived p-values with a transparent familywise correction, and treat wider BH-screened maps as exploratory. The accepted project's eventual preregistration, rather than this methodological menu, must set the exact family and correction. Do not retrofit 004B's frozen design.

## 12. Methods and interpretations to reject

1. Neural graph, attention or reinforcement-learning searches trained on the current tiny independent-election universe.
2. Unrestricted all-pair, all-lag, all-state transfer entropy with generic shuffled-row significance.
3. Graph-centrality “information leadership” inferred from a graph whose edges and thresholds were selected on the same outcomes.
4. A single-factor model across arbitrary winner/seat/coalition payoffs, especially with retrospective rank selection.
5. Near-critical Hawkes narratives without a credible time-varying baseline, timestamp audit and no-cross-excitation benchmark.
6. Hasbrouck/Gonzalo–Granger measures applied to different binary payoffs simply because the questions are related.
7. IID trade/contract-level significance for shared event outcomes; naive cluster-robust asymptotics with two election clusters.
8. Forward-filled or zero-filled covariance interpreted as a synchronous efficient-price network.
9. Tick-rule trade signs or fee/rebate shortcuts presented as verified aggressor truth; raw `OrderFilled` fields used without transaction-path interpretation.
10. Snapshot depth changes called event OFI, or aggregate book reductions called cancellations/trades with certainty.
11. Post hoc thresholds for large shocks, stale quotes, high activity or “good” probability ranges.
12. Full-window PCA, rank transforms, smoothing, centred volatility or realised resolution times used in predictive features.
13. Phase-randomised stationary nulls as the sole control for nonstationary, sparse election-night data.
14. A null failure relabelled as a nonlinear hypothesis without independent mechanism and a new, bounded preregistered interaction.
15. A non-significant effect described as absent with adequate power unexamined; conversely, a descriptive effect described as established alpha.
16. A return-calibration or arbitrage headline imported as an implementable short-horizon opportunity.

The rejected items include four weak **mechanism interpretations** identified in the atlas; other entries are rejected estimation or measurement practices. This distinction avoids inflating the mechanism count with methodological variants.

## 13. Precommitted questions for the frozen 004B package

These questions are written before opening the package. Some may be unanswerable from its frozen outputs. Mark those **not measured**, rather than inventing a finding or rerunning 004B.

1. What are the effective observation units, masks, horizons and test-family denominators behind each reported response?
2. Which relationships survive nulls that preserve activity and serial dependence, and which survive only permissive shifts?
3. Does any claimed direction exceed timestamp/availability uncertainty and remain after removing same-bin ambiguity?
4. How much is explained by source update frequency, target quote age or unequal observation support?
5. Can signed direction be separated from update likelihood, change magnitude and liquidity change?
6. Do richer own-market controls weaken cross-market evidence, as conventional OFI work suggests can happen?
7. Are available variables truly book OFI, depth imbalance, unsigned activity or signed executed volume? What is the provenance of any sign?
8. Are results concentrated in one information burst, source market or target family?
9. Does semantic family membership add evidence beyond degree, activity, probability level and liquidity?
10. Are exact complement/nesting/exhaustive constraints responsible for the strongest apparent dependencies?
11. Is low rank present beyond mechanical constraints and asynchronous observation, and are its subspaces stable?
12. Can a target-excluded factor be estimated from enough contemporaneously available nonredundant nodes?
13. Is any apparent residual correction distinguishable from stale observation of a common state?
14. Is there enough joint support to test a prespecified continuous state interaction without extrapolating from a few rows?
15. Do PRE and ACTIVE differ within events, or does pooled regime comparison change the event/market composition?
16. Does Hungary-versus-Peru-first-round comparison support a shared mechanism or only event-specific descriptions?
17. What exactly does the canonical 30-second calibration slice measure, and is it selection-independent? It must not be treated as a new independent event.
18. Which conclusions survive the declared multiplicity correction, and which untested interactions remain genuinely new questions?
19. Are null p-values sufficiently resolved for their family size, and are confidence intervals uncertainty intervals rather than null tests?
20. Are any promising variable classes ruled out by sparse depth, missing clocks, unstable factors or insufficient event counts?
21. Is the sealed-event boundary documented and intact in the supplied acceptance record, without opening those events?
22. Which candidate classes should be abandoned rather than rescued with more parameters?

### Prospective boundary for the next stage

After this file is frozen, the accepted 004B package may narrow or reject this atlas. A separate 004C hypothesis document must cite exact 004B artefacts and distinguish a motivating observation from an effect already surviving controls. It must set claim regime, predictor, target, baseline, challenger, horizon family, null, falsifier, negative controls, ablations, multiplicity family and data sufficiency before sealed-event access. Reading 004B does not authorise opening sealed empirical data or tuning a strategy.

ASTRA PRE-004C RESEARCH COMPLETE

Academic sources reviewed: 28, with manuscript/abstract review depth explicitly recorded; no independent empirical replications.

GitHub repositories reviewed: 8, including all five requested repositories; named implementation files inspected at recorded commits.

Practitioner/LinkedIn sources reviewed: 6 substantive public sources; other search hits screened out.

Quant mechanism classes retained: 11 conditional candidates, M01–M11; retention is not selection as alpha or authorisation to test every variant.

Mechanism classes rejected: 4 strong interpretations — uncontrolled centrality leadership, clustering-as-critical-cascade, calibration-as-short-horizon-lead, and universal one-factor correction. Additional invalid methods are listed in Section 12.

Most promising methodological directions: age-aware conditional responses; separate update-hazard and mark models; small semantic latent-state models; parsimonious predetermined liquidity/activity interactions.

Highest-risk false-positive mechanisms: asynchronous observation of common news; capture latency and batching; shared-outcome pseudo-replication; stale/invalid books; searched lags/states; future-fitted factors.

Major unanswered questions: which of these variables and estimands are actually supported by the frozen evidence; whether information content adds anything beyond observation clocks; whether any effect transfers across independent elections.

READY TO RECEIVE FROZEN 004B PACKAGE
