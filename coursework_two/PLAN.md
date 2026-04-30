# Coursework 2 — Master Plan
## Team Kolmogorov · IFTE0003 Big Data in Quantitative Finance
**Deadline:** Friday 1 May 2026, 17:00 GMT · **Status:** Week 2 in progress (today = 17 Apr 2026)

---

## 0. Executive Summary

CW2 operationalises the four-factor long/short equity strategy specified in CW1 into a fully-backtested, production-grade investment index. The baseline (mandated) implementation delivers everything in the CW2 Task Allocation Guide and the Viz & Metrics Reference: the seven-file Parquet data contract, 14 charts, multi-scenario net-of-cost metrics, γ×λ parameter sensitivity, factor ablation, crisis stress tests, and a 20,000-word report spanning investment thesis, methodology, empirical analysis, fund pitch, limitations, and team reflection.

On top of that baseline, this plan layers a tiered set of **advanced methodological extensions** — each selected to maximise each dimension of the marking rubric while remaining theoretically grounded and empirically feasible within a three-week window. Every extension either (a) strengthens statistical rigour under multiple testing and data snooping, (b) improves out-of-sample risk-adjusted performance, or (c) deepens the empirical discussion. No extension is included for novelty alone; each maps to a published citation and has an explicit purpose.

**Core design principle:** *Sophistication is additive, not substitutive.* The baseline implementation is always runnable end-to-end before any Tier-2 or Tier-3 extension is layered on. Every extension is gated behind a config flag so the report can honestly report both baseline and enhanced results.

---

## 1. Mapping to the CW2 Marking Rubric

| Criterion | Weight | How This Plan Maximises It |
|---|---:|---|
| **Investment Concept & Theoretical Justification** | 25% | Vayanos-Woolley (2013) institutional flow framework deepened into a regime-conditional hypothesis; each of the four factors anchored in ≥3 peer-reviewed sources; dynamic weighting framed as a Bayesian posterior update on factor IC; explicit treatment of factor crowding, signal decay, and flow-cycle fingerprinting. |
| **Methodological Implementation** | 30% | Denoised Ledoit-Wolf covariance (López de Prado 2020); turnover-penalised MinVar; Combinatorial Purged Cross-Validation for γ×λ tuning; block bootstrap + Deflated Sharpe + PSR for statistical inference; HRP as robustness comparison; strict point-in-time data handling; ≥85% pytest coverage with unit + integration + stress tests; `engine/`↔`analytics/` clean-contract separation (Parquet data contract); Poetry lock + Docker-reproducible runs. |
| **Empirical Analysis & Interpretation** | 25% | Fama-French-5 + Momentum alpha regression with Newey-West HAC standard errors; Fama-MacBeth cross-sectional return attribution; Brinson-Fachler sector decomposition; rolling per-factor IC; Monte Carlo permutation test for dynamic-vs-static; 3-crisis stress suite; capacity estimate via Kyle's λ; sensitivity to γ×λ with deflated-Sharpe-adjusted significance. |
| **Documentation & Presentation** | 10% | Full Sphinx docs extending CW1; visual-style-guide-compliant charts (300 DPI, palette-locked); 20,000-word report with word-budgeted section ownership; architecture diagram; reproducibility statement with pinned seeds and data hash. |
| **Teamwork & CW1 Integration** | 5% | Engine reads directly from CW1 PostgreSQL schema `systematic_equity` (no data duplication); reuses `ift_global` package and CW1 test fixtures; CHANGELOG.md extended; continuity of citation style and class conventions. |
| **In-Class Bonus** | +5% | One high-quality GitHub issue proposing a concrete module-improvement (drafted in-session). |

**Target:** 85%+ baseline, 90%+ with full Tier-2 layer, push toward distinction with Tier-3.

---

## 2. Investment Thesis — Sharpened from CW1

### 2.1 Core hypothesis

Following Vayanos & Woolley (2013), momentum and value co-exist as two phases of the same institutional-flow cycle. Our contribution is to argue that **VIX-regime conditioning plus cross-sectional factor dispersion jointly fingerprint which phase of the cycle the market is currently in**:

| VIX Regime | Dispersion signal | Implied flow phase | Factor tilt |
|---|---|---|---|
| Low (<P30) | Momentum dispersion high | Gradual outflow / price-continuation phase | Tilt toward **momentum** |
| Normal (P30–P80) | Balanced | Cycle transition | Baseline 30/30/25/15 |
| High (>P80) | Value dispersion high | Post-capitulation / mean-reversion phase | Tilt toward **value + quality** |

This is a testable hypothesis: if it holds, (a) VIX regimes should carry significant coefficients in a panel regression of factor returns on regime dummies, and (b) regime-conditional Sharpe should exceed static Sharpe after deflation.

### 2.2 Four-factor pillars (from CW1 §3.2, locked)

- **Momentum** — 12-1 cumulative return with absolute-momentum filter (Jegadeesh & Titman 1993; Antonacci 2016).
- **Value** — equally-weighted z-composite of B/P, E/P, CF/P (Asness, Frazzini & Pedersen 2015; Cornell & Damodaran 2021).
- **Quality** — ROE, earnings stability, inverse D/E (Asness, Frazzini & Pedersen 2019 — QMJ; Piotroski 2000).
- **Sentiment** — VADER composite 0.45·vader + 0.25·pos_ratio + 0.20·volume + 0.10·agreement (Hutto & Gilbert 2014; Tetlock 2007; Da, Engelberg & Gao 2011).

Sector-neutral z-scoring within each of the 11 GICS sectors (Asness, Moskowitz & Pedersen 2013).

### 2.3 Why not deep reinforcement learning?

Explicitly considered and rejected. A DRL agent for weight allocation needs thousands of independent episodes; we have **~72 monthly rebalance dates** (Jul 2022–Mar 2026) and ~48 out-of-sample. With only four factors and three regime states, the policy space is small and **any DRL policy will overfit trivially**. Even with synthetic-data pre-training, the out-of-sample sample size is too small for statistically significant DRL advantage over the Bayesian posterior update proposed in §5.4. Moreover, DRL opacity is penalised by the rubric's "clarity" criterion. A **Bayesian sequential update** (§5.4) is the statistically coherent analogue of DRL for this data regime and is the method of choice.

---

## 3. System Architecture

### 3.1 Three-layer separation (mandated by Task Guide §1)

```
┌─────────────────────────────────────────────────────────────────┐
│  CW1 PostgreSQL (port 5439, schema = systematic_equity)          │
│  daily_prices · fundamentals · fx_rates · vix_data · rfr         │
│  company_static · news_sentiment · company_ratios · esg_scores   │
└──────────────────────────┬──────────────────────────────────────┘
                           │  read-only SQL
                ┌──────────▼──────────┐
                │  engine/             │   DEVELOPERS ONLY (Tamer, Lucian)
                │  data_loader ─ factors ─ zscore ─ portfolio ─ costs│
                │  dynamic_weights ─ backtest ─ runner               │
                └──────────┬──────────┘
                           │  Parquet data contract (7 files)
                ┌──────────▼──────────┐
                │  analytics/          │   SPECIALISTS ONLY (Peixi, Moyan, Xinyan)
                │  performance ─ validation ─ charts                 │
                │  sensitivity ─ ablation ─ comparison ─ stress      │
                └──────────┬──────────┘
                           │  Charts (PNG/PDF) + metric tables
                ┌──────────▼──────────┐
                │  Report (docs/)      │   IPOs ONLY (Ayudhya, Ryan, Jianyang)
                │  20,000-word report  │
                └─────────────────────┘
```

**Anti-overlap rule (strict):** Specialists never write code in `engine/`; developers never write code in `analytics/`; IPOs never write Python. Cross-boundary requests → GitHub issue with exact column schema.

### 3.2 Directory structure (to be scaffolded in Week 1)

```
coursework_two/
├── PLAN.md                         ← this document
├── Main.py                         Lucian — top-level CLI entry
├── pyproject.toml                  Tamer — Poetry config
├── README.md                       IPO — written last
├── CHANGELOG.md                    all — append-only
├── docker-compose.override.yml     Tamer — links to CW1 containers
├── config/
│   └── backtest_config.yaml        Tamer
├── engine/                         DEVELOPERS ONLY
│   ├── __init__.py
│   ├── types.py                    Tamer+Lucian (Pydantic contracts, Week 1)
│   ├── config.py                   Tamer
│   ├── data_loader.py              Tamer
│   ├── factors.py                  Tamer
│   ├── zscore.py                   Tamer (+ Bayesian module extension)
│   ├── portfolio.py                Tamer (MinVar + HRP + denoised LW)
│   ├── attribution.py              Tamer (Fama-MacBeth, Brinson-Fachler)
│   ├── dynamic_weights.py          Lucian (+ optional HMM module)
│   ├── costs.py                    Lucian
│   ├── backtest.py                 Lucian (+ purged CV)
│   ├── runner.py                   Lucian
│   └── ml_enhancer.py              Tamer (Tier-3, gated — LightGBM residual)
├── analytics/                      SPECIALISTS ONLY
│   ├── __init__.py
│   ├── performance.py              Peixi (metrics incl. Deflated Sharpe, PSR)
│   ├── validation.py               Peixi
│   ├── charts.py                   Moyan (14 charts + 2 extensions)
│   ├── sensitivity.py              Xinyan (γ×λ grid + CPCV)
│   ├── ablation.py                 Xinyan
│   ├── comparison.py               Xinyan (static vs dynamic + HRP)
│   ├── stress.py                   Xinyan (3 crisis windows + permutation)
│   └── attribution_analysis.py     Peixi (FF5+Mom regression, IC stats)
├── output/                         ← engine writes Parquet here
├── charts/                         ← charts.py writes PNG/PDF here
├── test/
│   ├── test_engine/                Tamer + Lucian (≥85% cov on engine/)
│   └── test_analytics/             Specialists (≥80% cov on analytics/)
└── docs/                           Xinyan (Sphinx extension of CW1)
```

---

## 4. Baseline Methodology — Locked from CW1 §3

The following is the implementation-ready mathematical core that **every team member must be aligned on before any Tier-2/3 extension is built**. All formulas originate in the CW1 report.

### 4.1 Factor raw-score computation (monthly, last trading day)

$$
\text{Momentum}_i = \frac{P^{adj}_{i,\,t-1}}{P^{adj}_{i,\,t-12}} - 1 \quad \text{(12-1 return, absolute-momentum filter applied to long leg)}
$$

$$
\text{Value}_i = \tfrac{1}{3}\left[z\!\left(\tfrac{B}{P}\right) + z\!\left(\tfrac{E}{P}\right) + z\!\left(\tfrac{CF}{P}\right)\right] \quad (\text{winsorise at 2.5/97.5 within GICS})
$$

$$
\text{Quality}_i = \tfrac{1}{3}\left[z(\text{ROE}) + z(\text{Earnings Stability}) + z(\text{Inverse D/E})\right]
$$

$$
\text{Sentiment}_i = 0.45\cdot\text{VADER} + 0.25\cdot\text{PosRatio} + 0.20\cdot\text{Volume} + 0.10\cdot\text{Agreement}
$$

### 4.2 Sector-neutral z-score (Eq. 8)

$$
z_{i,f,t} = \frac{x_{i,f,t} - \mu_{s(i),f,t}}{\sigma_{s(i),f,t}}
$$

Sectors with fewer than 5 stocks at date t get neutral z=0 for all constituents.

### 4.3 Dynamic weighting (Eqs. 1–3)

$$
D_{f,t} = \bar{z}_{f,t}^{\text{TopQ}} - \bar{z}_{f,t}^{\text{BottomQ}}
$$

$$
w^*_{f,t} = w^{\text{base}}_f \cdot (1 + \lambda^{(r_t)}_f)\cdot(1 + \gamma D_{f,t}) \quad \text{then normalised} \quad w_{f,t} = \frac{w^*_{f,t}}{\sum_{k=1}^4 w^*_{k,t}}
$$

with base = (0.30, 0.30, 0.25, 0.15) and VIX regime $r_t \in \{\text{low, normal, high}\}$ by trailing-252-day percentile (P30/P80 thresholds).

### 4.4 Composite and leg selection — **CW2 design decision**

$$
\text{Composite}_i = \sum_{f=1}^{4} w_{f,t}\cdot z_{i,f,t}
$$

**CW1 baseline (reported for reference)**: Long leg = top quartile (25%), short leg = bottom quartile, MinVar within each leg.

**CW2 team-owned design** (per CW2 Task Allocation Guide which explicitly invites teams to "determine selection rules — e.g., top/bottom deciles, threshold filters — and weighting schemes — equal-weighted, factor-weighted, risk-parity"):

1. **Decile selection**: Long = top 10%, Short = bottom 10%.  Concentrates exposure on highest-conviction names.  Selection is of ~50 stocks per leg (from ~511 after the liquidity filter).
2. **Factor-weighted (score-weighted) within-leg allocation**: weight is proportional to the *distance from leg-median composite score*, with a 5% per-name cap and renormalisation.  Maximum-IR extraction per **Grinold & Kahn (2000) "Fundamental Law of Active Management"**: IR ∝ IC · √breadth.
3. **Both variants kept** — the backtest emits Static-quartile-MinVar (CW1-baseline) alongside the decile+score-weighted enhanced variant, enabling direct ablation-style comparison per the task's "at least one robustness or sensitivity test".

**Rationale**: MinVar in a 170-stock leg with a 5% cap collapses to near-equal-weights, *diluting the signal*.  Score-weighting within a tighter decile selection concentrates alpha on the strongest signals — the exact mechanism the task's factor-weighted option is designed to deliver.  This is a legitimate design choice, *not a PLAN violation*; the PLAN's §4.4 quartile/MinVar description was the inherited CW1 baseline, not a CW2 mandate.

### 4.5 MinVar per leg + Historical VaR scaling (Eq. 10)

$$
\min_{w} \;w^\top \hat\Sigma_{\text{shrunk}}\, w \;\; \text{s.t.}\;\; w \geq 0,\; \mathbf{1}^\top w = 1,\; w_i \leq 5\%
$$

$$
\text{Scale} = \frac{\text{TargetBudget}}{|\text{VaR}_{99}|}\;\;\text{over 756-day rolling window}
$$

### 4.6 Cost model (net return)

$$
\text{Turnover}_t = \tfrac{1}{2}\sum_i |w^{\text{new}}_{i,t} - w^{\text{old}}_{i,t}|, \quad R^{\text{net}}_t = R^{\text{gross}}_t - \text{Turnover}_t \cdot c \cdot 2
$$

with c ∈ {20bp, 30bp} per side.

---

## 5. Advanced Methodological Extensions — The Grade-Maxing Layer

Each extension is tagged **T2** (must-do for distinction) or **T3** (stretch, time-permitting). Each has an owner, a citation anchor, and a concrete deliverable.

### 5.1 Denoised Ledoit-Wolf Covariance [T2 · Tamer · `engine/portfolio.py`]
**Method.** Apply Marchenko-Pastur eigenvalue detection (López de Prado 2020) to identify which eigenvalues lie in the "noise" region of the random-matrix distribution. Replace those eigenvalues with their mean, preserving the signal subspace. Then apply Ledoit-Wolf shrinkage to the denoised covariance. Compare MinVar weights vs vanilla LW in an ablation.
**Why.** Vanilla LW treats all eigenvalues equally; denoising provably improves out-of-sample portfolio risk in high-dimensional settings (N/T > 0.1). With ~170 stocks per leg on 252 daily returns, N/T = 0.67 — deep in the noisy regime.
**Citation.** López de Prado, M. (2020). *Machine Learning for Asset Managers*, Cambridge Elements.

### 5.2 Turnover-Penalised MinVar [T2 · Tamer · `engine/portfolio.py`]
**Method.** Augment the MinVar objective with an L2 (or L1) penalty on weight change:
$$
\min_w\; w^\top\Sigma w + \eta \cdot \|w - w_{\text{prev}}\|_2^2
$$
Calibrate η such that annualised one-way turnover falls by ~20% (measured in sensitivity sweep).
**Why.** Monthly rebalance at ~170 stocks per leg generates 300%+ annual turnover. 20bp × 600% round-trip = 120bp drag. Reducing turnover by 20% gives ~24bp/year free alpha. Critical for the transaction-cost section (§4.6).
**Citation.** DeMiguel, Garlappi & Uppal (2009); Olivares-Nadal & DeMiguel (2018).

### 5.3 Hierarchical Risk Parity as Robustness Comparison [T2 · Tamer · `engine/portfolio.py`]
**Method.** Implement López de Prado's (2016) HRP algorithm: hierarchical clustering on correlation distance → quasi-diagonalisation → recursive bisection weight allocation. No covariance inversion needed.
**Why.** HRP is not directionally better than MinVar but is **more robust to covariance-estimation error**. Reporting both in Section 4.7 gives a second signal that MinVar results are not artefacts of the shrinkage choice.
**Citation.** López de Prado (2016) "Building diversified portfolios that outperform out of sample", *JPM*.

### 5.4 Contextual Thompson Sampling for Adaptive Weight Selection [T2 · Tamer · `engine/bandit.py`]
**Method.** Reframe dynamic-weight selection as a linear contextual multi-armed bandit — the sample-efficient RL formalism that *works* at our data scale (unlike deep RL or tabular Q-learning).

- **Arms** $a \in \{1, \ldots, K\}$ with $K=12$: static baseline (30/30/25/15), 4 single-factor tilts (one factor to 45%, others scaled), 3 regime presets (VIX low / normal / high with ideal tilts), 4 factor-pair tilts. Each arm is a specific normalised weight vector.
- **Context** $x_t \in \mathbb{R}^{12}$: VIX z-level, VIX regime dummies (3), 4 factor dispersions $D_{f,t}$, 4 lagged per-factor ICs (3-month window).
- **Reward** $r_t$: realised next-month net portfolio return (exponentially decayed at 12-month half-life — §5.18).
- **Policy — Linear Thompson Sampling** (Agrawal & Goyal 2013): For each arm $a$, maintain Gaussian posterior $\theta_a \sim \mathcal{N}(\hat\mu_a, \Sigma_a)$. At time $t$: sample $\tilde\theta_a$ per arm, pull $a^\star = \arg\max_a x_t^\top \tilde\theta_a$, observe reward, update posterior via conjugate Bayesian rule.
- **Warm-up:** first 6 months run static baseline; posteriors initialised at baseline-informed priors.

**Why.**
1. **Sample-efficient online learning.** LinTS regret $\tilde{O}(d\sqrt{T\log T})$; with $d=12, T=48$ that's ~40 reward units — tolerable. Deep RL needs thousands of episodes; we have tens. Tabular Q/SARSA would need state-action visitation we cannot achieve.
2. **Bayesian–RL equivalence.** Russo & Van Roy (2018) prove LinTS is near-optimal in Bayesian regret; it is *formally* the Bayesian posterior update we want, with the RL lineage made explicit.
3. **Ex-ante implementability — the fund-pitch killer argument.** The γ×λ grid search (§5.5) is hindsight-optimal — picks the best parameter in realised data. The bandit is ex-ante — chooses one arm per month using only observed context. Reports both. If TS Sharpe is within 10% of grid-search Sharpe, the strategy is genuinely deployable. If not, the gap *quantifies the over-fitting credit* in grid-search performance. Either is a publishable result.
4. **Interpretability.** Posterior means + credible bands per arm are directly plottable (Fig 18) — reviewers see the learning.

**Output.** `bandit_log.parquet` with `date, arm_selected, arm_posterior_means[K], arm_posterior_stds[K], context_vector, realised_reward`. New chart: arm posterior-mean trajectory over time.

**Citations.** Thompson (1933); Li, Chu, Langford, Schapire (2010); Agrawal & Goyal (2013); Russo & Van Roy (2018).

### 5.5 Combinatorial Purged Cross-Validation for γ×λ Tuning [T2 · Lucian + Xinyan · `engine/backtest.py` + `analytics/sensitivity.py`]
**Method.** Instead of a single walk-forward split, use López de Prado's (2018) CPCV: partition the 2022-07→2026-03 window into 12 disjoint groups; evaluate each (γ, λ) candidate on all $\binom{12}{2}$ held-out combinations with purging (drop observations adjacent to test fold to prevent leakage from overlapping 12-month momentum windows) and embargo (additional buffer for sentiment-signal bleed). Report the **mean** OOS Sharpe and, more importantly, the **variance** across folds as a robustness metric.
**Why.** The original spec's γ ∈ 5 values × λ ∈ 3 values = 15 parameter combinations. Naive grid search over 48 OOS months gives a single Sharpe per combination — impossible to distinguish signal from noise. CPCV gives a distribution of OOS Sharpes per combination, enabling deflated-Sharpe-adjusted significance testing.
**Citation.** López de Prado (2018) *Advances in Financial Machine Learning*, Ch. 7.

### 5.6 Regime-Switching HMM [T3 · Lucian · `engine/dynamic_weights.py`]
**Method.** Fit a two-state Gaussian HMM on VIX log-changes (hmmlearn library). Compare HMM-inferred state posterior probabilities against the percentile-threshold regime classification. Report agreement (κ-statistic) and run the backtest with HMM regimes as a robustness comparison.
**Why.** Fixed percentile thresholds (P30/P80) are ad hoc; HMM learns thresholds endogenously. If backtest performance is preserved under HMM, that strengthens the dynamic-weighting case; if it degrades, that's a valid limitation to flag.
**Citation.** Hamilton (1989) "A new approach to the economic analysis of nonstationary time series", *Econometrica*.

### 5.7 Block Bootstrap + Deflated Sharpe + PSR [T2 · Peixi · `analytics/performance.py`]
**Method.**
- **Block bootstrap (Politis-Romano 1994):** resample overlapping 6-month blocks of monthly returns, compute Sharpe for each, build a 95% CI. Circumvents monthly return autocorrelation.
- **Deflated Sharpe (Bailey & López de Prado 2014):** adjust reported Sharpe for the multiplicity of (γ, λ) configurations tested, penalising Sharpe when the grid is large.
- **Probabilistic Sharpe Ratio (PSR):** probability that true Sharpe > threshold (e.g., 0.5) given observed sample.
**Why.** The rubric explicitly asks about robustness. Pure point estimates of Sharpe are inadequate; confidence intervals and multiple-testing-adjusted inference are best practice.
**Citation.** Bailey & López de Prado (2014) "The deflated Sharpe ratio", *Journal of Portfolio Management*.

### 5.8 Fama-French 5 + Momentum Alpha Regression [T2 · Peixi · `analytics/attribution_analysis.py`]
**Method.** Run monthly regression of strategy returns on Mkt-RF, SMB, HML, RMW, CMA, MOM (Fama & French 2015 + Carhart 1997). Report α with Newey-West HAC standard errors (lag = 4 per Andrews 1991 rule). Include Jensen's α confidence interval.
**Why.** The marker will immediately look for whether the strategy's return survives exposure to the common factors. A significant α is the most defensible "value-add" claim. Standard for any institutional fund pitch.
**Citation.** Fama & French (2015); Carhart (1997); Newey & West (1987).

### 5.9 Fama-MacBeth Cross-Sectional Attribution [T2 · Tamer · `engine/attribution.py`]
**Method.** At each rebalance date, run a cross-sectional regression of next-month returns on the four z-scored factor scores. Collect coefficients $\hat\beta_{f,t}$ over time. Report time-series mean and t-statistic of each $\hat\beta_f$ — the Fama-MacBeth t-statistic (1973).
**Why.** Produces the per-factor "risk premium" directly from the data, independent of the portfolio-construction step. Gives the Figure-6 stacked-bar attribution chart its quantitative foundation.
**Citation.** Fama & MacBeth (1973).

### 5.10 Brinson-Fachler Sector Attribution [T3 · Peixi · `analytics/attribution_analysis.py`]
**Method.** Decompose portfolio excess return vs the equal-weight benchmark into **allocation effect** (over/underweight sectors) and **selection effect** (stock picking within sectors). Report monthly and cumulative.
**Why.** Sector-neutral z-scoring means allocation effect should be ~zero in expectation; any non-zero allocation effect signals drift. Selection effect is where factor alpha should show up.
**Citation.** Brinson & Fachler (1985) "Measuring non-US equity portfolio performance", *JPM*.

### 5.11 Capacity Estimation via Kyle's λ [T2 · Ryan · `engine/attribution.py` helper + Report §6]
**Method.** For each stock in the portfolio at the most recent rebalance, estimate Kyle's λ = ΔP/Volume from intraday or daily data (proxy with Amihud illiquidity 2002). Compute max target-AUM such that predicted price impact ≤ 15 bps per name. Aggregate to portfolio capacity.
**Why.** Fund pitch section (Report §6) explicitly asks for capacity. A rigorous capacity estimate based on a published illiquidity measure is much stronger than hand-waving.
**Citation.** Kyle (1985); Amihud (2002); Almgren & Chriss (2001).

### 5.12 ML Residual Enhancer [T3 · GATED · Tamer · `engine/ml_enhancer.py`]
**Method.** Train a LightGBM regressor on features = {4 z-scores, 4 factor dispersions, VIX level, VIX regime dummy, sector dummies} → target = next-month residual return (observed return minus linear-composite-predicted return). Train with CPCV; report OOS IC. **Gate:** include in the live strategy only if OOS IC > 0.02 and OOS Sharpe improvement > 0.1 after deflation.
**Why.** Establishes whether there is residual cross-sectional predictability beyond the linear composite. Honest null-result reporting is still publishable; positive result is a clear value-add. Key: **must be report-ready both ways**.
**Citation.** Gu, Kelly & Xiu (2020) "Empirical asset pricing via machine learning", *Review of Financial Studies*.

### 5.13 Monte Carlo Permutation Test [T2 · Xinyan · `analytics/stress.py`]
**Method.** Null hypothesis: dynamic and static weighting produce the same return distribution. Under H₀, randomly permute the dynamic-vs-static label across months, recompute the Sharpe difference, build a null distribution of size 10,000. Report empirical p-value.
**Why.** A single observed Sharpe gap of, e.g., 0.1 means little without a null baseline. Permutation test is assumption-free (no normality assumed).
**Citation.** Efron & Tibshirani (1994).

### 5.14 Sector-Neutral Sequential Factor Orthogonalisation [T2 · Tamer · `engine/factors.py`]
**Method.** Before composite z-scoring, orthogonalise the four factor scores via Gram-Schmidt residualisation within each GICS sector at each rebalance date. Order: momentum → value → quality → sentiment (fastest-to-slowest signal decay). For each factor $f$, regress raw factor on already-orthogonalised prior factors; keep residual:
$$
f^\perp_i = f_i - \hat{\boldsymbol\beta}^\top \mathbf{F}^\perp_{<f,i}
$$
Z-score the orthogonalised residuals within sector. Retain raw-z-scored variant for ablation comparison.

**Why.**
- Linear composite of correlated signals double-counts common variance (momentum and value historically correlate ≈ −0.5; Asness et al. 2013).
- Novy-Marx (2013) shows gross-profitability earns an independent premium once orthogonalised against value; we replicate the logic across all four.
- Orthogonalised composite IC rises materially (typically 10–20%) without new data.
- Directly strengthens the "diversification across independent signals" claim underlying Report §3.4.

**Expected impact.** Composite IC +10–20%; pairwise factor correlation → ~0.

**Citations.** Grinold & Kahn (2000) Ch. 4; Novy-Marx, R. (2013) "The other side of value: The gross profitability premium", *JFE*, 108(1), 1–28.

### 5.15 Liquidity-Aware Universe Filter [T2 · Tamer · `engine/data_loader.py`]
**Method.** At each rebalance, exclude stocks with either:
- 30-day average daily dollar-volume (ADV) below $5M, OR
- Bottom 15th percentile of the universe by ADV (whichever is tighter).

Additionally, short leg excludes stocks with market cap < $1B (hard-to-borrow proxy, per Diether-Lee-Werner 2009).

**Why.**
- Removes names driving disproportionate transaction costs. Market impact ~ 1/√ADV (Almgren et al. 2005); illiquid names may cost 50–100 bps round-trip, destroying alpha.
- Validates the §5.11 capacity claim — capacity is meaningful only if the strategy excludes the names that would hit it first.
- Protects short leg from borrow-recall risk.
- Korajczyk & Sadka (2004) show momentum premium survives in large-cap-only universes; alpha cost is small.

**Expected impact.** Net-of-cost Sharpe +0.1–0.2; realised max DD −1–2%; capacity ceiling realistic.

**Output.** Add `n_stocks_filtered_liquidity, n_stocks_filtered_htb` to `exposure_log.parquet`.

**Citations.** Korajczyk, R. A. & Sadka, R. (2004); Diether, K. B., Lee, K.-H. & Werner, I. M. (2009) "Short-sale strategies and return predictability", *RFS*, 22(2), 575–607.

### 5.16 Conditional Volatility Targeting [T2 · Lucian · `engine/portfolio.py` + `backtest.py`]
**Method.** After the MinVar + HVaR scaling chain, apply a third-level conditional-vol rescaling:
$$
\hat\sigma_{60,t} = \sqrt{\tfrac{252}{60}\sum_{s=t-59}^{t-1}(R_s - \bar R)^2}, \quad \text{scale}_{\text{vol},t} = \frac{\sigma^{\text{target}}}{\hat\sigma_{60,t}}, \quad \sigma^{\text{target}} = 10\%
$$
Clip scale to [0.3, 1.5] to prevent leverage extremes. Applied uniformly to both legs.

**Why.**
- **Moreira & Muir (2017, JF)** is the single strongest published evidence that vol-targeting improves Sharpe for equity strategies — documented Sharpe improvement 0.2–0.3 across the market factor, SMB, HML, MOM, UMD, QMJ, BAB.
- Mechanism: de-lever during high-vol episodes (which empirically have low forward returns — the Volatility-Managed Portfolio anomaly). This also reduces realised drawdown.
- Complements HVaR-scaling: HVaR is about loss probability at a point in time; vol-targeting is about risk-adjusted-return over time.
- **Harvey et al. (2018)** confirms the effect persists OOS and is robust across asset classes.

**Expected impact.** Sharpe +0.2–0.3 (net of costs); realised annual vol close to 10% (institutional-fund-pitch friendly number).

**Citations.** Moreira, A. & Muir, T. (2017) "Volatility-managed portfolios", *JF*, 72(4), 1611–1644; Harvey, C. R., Hoyle, E., Korgaonkar, R., Rattray, S., Sargaison, M. & van Hemert, O. (2018) "The impact of volatility targeting", *JPM*, 45(1), 14–33.

### 5.17 Drawdown-Control Overlay [T2 · Lucian · `engine/backtest.py`]
**Method.** At each date $t$, compute drawdown relative to 12-month rolling peak of strategy NAV:
$$
\text{DD}_t = \frac{\text{NAV}_t - \text{peak}_{12m,t}}{\text{peak}_{12m,t}}
$$
Gross-exposure scalar:
- $\text{DD}_t > -3\%$: scalar = 1.0 (full)
- $-6\% \leq \text{DD}_t \leq -3\%$: scalar = 0.75
- $\text{DD}_t < -6\%$: scalar = 0.50

Recovery: return to scalar 1.0 when $\text{DD}_t > -1\%$ (asymmetric hysteresis prevents toggling).

**Why.**
- Simple but rigorous: Korn, Korn & Kroisandt (2017) formalise the expected-utility case for capital-buffer-based drawdown control; Kritzman & Rich (2002) document tail-conditional reduction.
- Historically reduces max DD by 30–40% at modest Sharpe cost (~0.05). Calmar and Sortino improve markedly.
- Key fund-pitch metric: institutional allocators care about max DD more than Sharpe above ~1.0.
- Connects to the dynamic-weighting thesis — both are regime-aware risk managers.

**Expected impact.** Max DD from baseline ~8% → ~5%; Calmar +0.3–0.5; Sortino +0.2.

**Output.** `exposure_log.parquet` adds `drawdown_12m, dd_control_scalar`.

**Citations.** Korn, O., Korn, R. & Kroisandt, G. (2017); Kritzman, M. & Rich, D. (2002) "The mismeasurement of risk", *FAJ*, 58(3), 91–99.

### 5.18 Minimum Backtest Length — Power Analysis [T2 · Peixi · `analytics/performance.py`]
**Method.** Apply the Bailey, Borwein, López de Prado & Zhu (2017) "minimum backtest length" formula:
$$
\text{MinTRL} \approx 1 + \left(1 - \gamma + \tfrac{\gamma}{\ln(\text{N})}\right)\left(\frac{z_\alpha}{\text{SR}}\right)^2
$$
where SR = target Sharpe (e.g., 1.0), N = number of trials in the grid search, $z_\alpha$ = z-critical at significance $\alpha$. Report the minimum OOS length required to detect our observed Sharpe at 95% confidence, given the grid of 15 (γ, λ) combinations, and compare to our actual OOS length (~48 months).

**Why.**
- **Directly pre-empts the marker's obvious concern.** The OOS window is ~48 months — is that enough? MBL answers quantitatively.
- If MinTRL < 48 months: we can honestly claim statistical power; if > 48 months: we acknowledge underpowering transparently (rigour).
- Paired with the Deflated Sharpe (§5.7) to bound the false-discovery claim.

**Citations.** Bailey, D. H., Borwein, J., López de Prado, M. & Zhu, Q. J. (2017) "Pseudo-mathematics and financial charlatanism: The effects of backtest overfitting on out-of-sample performance", *Notices of the AMS*, 61(5), 458–471; López de Prado, M. (2018) *Advances in Financial Machine Learning*, Ch. 8.

### 5.19 Summary of Extensions

| # | Extension | Tier | Owner | Rubric leverage | Expected impact |
|---|---|---|---|---|---|
| 5.1 | Denoised Ledoit-Wolf covariance | T2 | Tamer | Method 30% | Vol −5–10%, stable MinVar |
| 5.2 | Turnover-penalised MinVar | T2 | Tamer | Method 30%, Empirical 25% | Turnover −20%, net Sharpe +0.05 |
| 5.3 | HRP as robustness comparison | T2 | Tamer | Method 30%, Empirical 25% | Covariance-free alternative |
| 5.4 | **Contextual Thompson Sampling (RL)** | T2 | Tamer | **Concept 25%, Method 30%** | Ex-ante implementable; Bayesian-RL equivalence narrative |
| 5.5 | Combinatorial Purged CV | T2 | Lucian+Xinyan | Method 30% | Data-snooping defence |
| 5.6 | Regime-switching HMM | T3 | Lucian | Method 30% | Regime-threshold robustness |
| 5.7 | Block bootstrap + Deflated Sharpe + PSR | T2 | Peixi | Empirical 25% | Sharpe CI + multiple-testing adj |
| 5.8 | FF5+Momentum α (Newey-West HAC) | T2 | Peixi | Empirical 25%, Concept 25% | Defensible α claim |
| 5.9 | Fama-MacBeth cross-sectional | T2 | Tamer | Empirical 25% | Per-factor premium & t-stat |
| 5.10 | Brinson-Fachler sector attribution | T3 | Peixi | Empirical 25% | Allocation-vs-selection diagnostic |
| 5.11 | **Capacity via Kyle's λ / Amihud** | **T2** | Ryan | Concept 25% (fund pitch) | $XBn AUM claim credible |
| 5.12 | ML residual enhancer (heavily gated) | T3 | Tamer | Method 30% | Gate: OOS IC > 0.03 or dropped |
| 5.13 | MC permutation test | T2 | Xinyan | Empirical 25% | Dynamic-vs-static p-value |
| **5.14** | **Factor orthogonalisation (Gram-Schmidt)** | **T2** | Tamer | Method 30%, Concept 25% | Composite IC +10–20% |
| **5.15** | **Liquidity-aware universe filter** | **T2** | Tamer | Method 30%, Concept 25% | Cost drag −5–10bp, capacity valid |
| **5.16** | **Conditional volatility targeting** (Moreira-Muir) | **T2** | Lucian | Method 30% | **Sharpe +0.2–0.3** |
| **5.17** | **Drawdown-control overlay** | **T2** | Lucian | Method 30%, Pitch | **Max DD −30–40%, Calmar +0.3–0.5** |
| **5.18** | **Minimum Backtest Length power analysis** | **T2** | Peixi | Empirical 25% | Pre-empts "n too small" critique |

**Excluded as marginal (documented for completeness):** Quality × Value interaction (small IC gain vs construction complexity), Almgren-Chriss cost model (supersedes spec-mandated 20/30bp), risk-parity leg sizing (legs already near-equal vol in large-cap universe), DCC-GARCH covariance (marginal OOS gain vs complexity), copula tail dependence, Black-Litterman, GRS test (redundant with FF5 Newey-West). Deep RL and tabular Q-learning rejected on data-size grounds — classical RL included via Thompson Sampling (§5.4), which is sample-efficient.

---

## 6. Data Contract — The Engine→Analytics Boundary

Extension of the Task Guide §1.1 table with additional output files for the Tier-2/3 extensions. **All Parquet. All analytics code reads only these files.**

| File | Schema | Owner | Extension? |
|---|---|---|---|
| `portfolio_returns.parquet` | date, dynamic_gross, dynamic_net_20bp, dynamic_net_30bp, static_net_20bp, static_net_30bp, hrp_net_20bp, benchmark_ew, long_leg, short_leg, rf_rate | Lucian | HRP added |
| `portfolio_weights.parquet` | date, symbol, weight, leg (long/short), strategy (dynamic/static/hrp) | Lucian | HRP added |
| `factor_scores.parquet` | date, symbol, momentum_z, value_z, quality_z, sentiment_z, composite_z, gics_sector | Tamer | — |
| `factor_ic.parquet` | date, factor, ic_spearman, ic_pearson, forward_return | Tamer | Added pearson |
| `factor_premia.parquet` | date, factor, fama_macbeth_beta, t_stat | Tamer | **NEW (5.9)** |
| `regime_log.parquet` | date, vix_level, vix_percentile, regime_pct, regime_hmm, hmm_prob_high, D_mom, D_val, D_qual, D_sent, w_mom, w_val, w_qual, w_sent | Lucian | HMM added |
| `exposure_log.parquet` | date, gross_exposure, net_exposure, portfolio_beta, var_99, es_99, position_scale, turnover_1way, cost_drag_20bp, cost_drag_30bp, long_alpha, short_alpha, hhi_concentration | Lucian | HHI added |
| `sensitivity_grid.parquet` | gamma, lambda_magnitude, cv_fold, sharpe_net, sharpe_deflated, max_dd, info_ratio, turnover | Lucian | **CPCV folds added** |
| `ablation_results.parquet` | variant, sharpe_net, max_dd, info_ratio, alpha_ff5, alpha_tstat | Xinyan | FF5 alpha added |
| `bayesian_weights.parquet` | date, w_mom_bayes, w_val_bayes, w_qual_bayes, w_sent_bayes, kappa | Tamer | **NEW (5.4)** |
| `ml_residual_predictions.parquet` | date, symbol, predicted_residual, model_feature_importance | Tamer | **NEW (5.12, gated)** |

**Protocol:** If a specialist needs a column not in this contract, they open a GitHub issue with the exact schema needed, assigned to the relevant developer. No out-of-band data passing.

---

## 7. Backtest Design — Production-Grade Engine

The backtest is **not** a Jupyter notebook. It is a dependency-injected, event-driven, audited, parallelised, Monte-Carlo-capable engine whose every decision is logged with timestamp, seed, and data hash.

### 7.1 Engine Architecture — Strategy + Dependency-Injection Pattern

```python
# engine/backtest.py — top-level engine (Lucian)
@dataclass
class BacktestEngine:
    data_loader:     DataLoader          # Tamer — reads CW1 PG, strict PIT
    factor_engine:   FactorEngine        # Tamer — 4 factors + orthogonalisation (§5.14)
    zscore_engine:   ZScoreEngine        # Tamer — sector-neutral, handles |sector|<5
    weight_engine:   WeightEngine        # Lucian — SWAPPABLE:
                                         #   - StaticWeights (baseline 30/30/25/15)
                                         #   - DynamicWeights (VIX regime × dispersion)
                                         #   - BanditWeights  (Thompson Sampling §5.4)
    portfolio_engine: PortfolioEngine    # Tamer — SWAPPABLE:
                                         #   - MinVarVanillaLW
                                         #   - MinVarDenoisedLW (§5.1)
                                         #   - MinVarTurnoverPenalty (§5.2)
                                         #   - HRP (§5.3)
    risk_scaler:     CompositeRiskScaler # Lucian — chain of:
                                         #   HVaRScaler → VolTargetScaler (§5.16) → DDControlScaler (§5.17)
    cost_model:      CostModel           # Lucian — proportional 20/30bp (spec-mandated)
    executor:        Executor            # Lucian — trade planning + short-feasibility filter
    ledger:          TradeLedger         # Lucian — per-trade immutable log
    metric_tracker:  MetricTracker       # Peixi — Observer pattern
    calendar:        TradingCalendar     # Lucian — NYSE via pandas_market_calendars
    seed:            int = 42
    data_snapshot_sha256: str            # reproducibility hash of the CW1 DB snapshot

    def run(self, start: date, end: date) -> BacktestResult:
        for rebalance_date in self.calendar.last_trading_days(start, end):
            ctx = self.data_loader.context(rebalance_date)       # PIT snapshot
            f   = self.factor_engine.compute(ctx)                # 4 raw factors
            z   = self.zscore_engine.compute(f, ctx.gics_map)    # sector-neutral
            w_f = self.weight_engine.compute(z, ctx)             # strategy-dependent
            tgt = self.portfolio_engine.optimise(w_f, ctx)       # MinVar-family
            tgt = self.risk_scaler.scale(tgt, ctx)               # VaR + vol + DD
            plan = self.executor.plan(tgt, self.positions, ctx)  # short-feasibility filter
            costs = self.cost_model.compute(plan)
            self.ledger.record(rebalance_date, plan, costs)
            self.positions = self.executor.execute(plan)
            self.metric_tracker.observe(rebalance_date, ...)
        return BacktestResult(self.ledger, self.metric_tracker, self.data_snapshot_sha256)
```

**Why this pattern.** (1) Every component is mockable — unit tests inject fakes. (2) Ablation becomes trivial — swap `DynamicWeights → StaticWeights` and re-run. (3) Audit trail is complete — no hidden state. (4) Reproducibility is bit-level — seed + data hash fully determine output. (5) Dependency-injection makes the whole backtest a pure function of (config, data, seed), which is the López de Prado (2018) gold standard.

### 7.2 Windows & Calendar Discipline

- **In-sample training window:** 2022-01-01 → 2023-06-30 (per CW1 §3.5; hyperparameter-only)
- **Walk-forward OOS window:** 2023-07-01 → 2024-12-31 (mandated by Task Guide)
- **Extended OOS window:** 2025-01-01 → 2026-03-31 (supplementary — covers Q4 2025 momentum reversal)
- **Rebalance:** **last NYSE trading day of each month** (not calendar month-end). Computed via `pandas_market_calendars.get_calendar('NYSE')`.
- **Estimation windows:** 252 trading days for covariance, 756 for HVaR, 60 for realised-vol target.

### 7.3 Point-in-Time Discipline — Non-Negotiable

Violations of PIT are the single most common source of inflated backtest results. We enforce seven hard rules, tested programmatically in `test/test_engine/test_pit.py`:

1. **Fundamentals:** filter on `report_date ≤ rebalance_date` (NOT `period_end`). CW1 §5.1 flagged this.
2. **News sentiment:** strict cutoff — no articles `published_at > rebalance_date` enter the composite.
3. **VIX regime:** only trailing 252 days up to `rebalance_date − 1`.
4. **Prices:** close of `rebalance_date − 1` for both signal and return computation.
5. **Currency conversion:** $R^{USD}_t = (1 + R^{\text{local}}_t)(FX_t / FX_{t-1}) - 1$, close-to-close FX.
6. **Corporate actions:** use adjusted close (CW1 already applies this).
7. **Automated PIT audit:** integration test verifies that for a sample of rebalance dates, no data with a timestamp > rebalance_date touches the decision path — raises if the test harness detects it.

### 7.4 Nested CV for Hyperparameter Protection

**Outer loop (performance estimation):** CPCV with 12 disjoint month-groups, $\binom{12}{2} = 66$ train/test combinations, 2-month purge + 1-month embargo.
**Inner loop (hyperparameter selection):** Within each outer training fold, a further 5-fold purged CV selects optimal (γ, λ, κ, η_turnover, σ_target). Hyperparameters never cross the outer-test boundary.

```
Outer CV:        [train fold 1-10] → [select HPs via inner CV] → [eval on fold 11,12]
Inner CV:        [inner-train 1-8] → [inner-val 9,10]  (on outer train only)
```

This is López de Prado (2018) §7.4 — prevents hyperparameter-selection leakage, a silent killer of OOS performance.

### 7.5 Monte Carlo Path Simulation [T2]

Generate **10,000 bootstrap return paths** via circular block bootstrap (Politis-Romano 1994), block length = 6 months, to produce:
- Median + 5th/95th percentile "cone of uncertainty" around cumulative return
- Distribution of terminal Sharpe / Max DD / Calmar across simulations
- Probability of achieving target Sharpe ≥ 1.2

Output: `monte_carlo_paths.parquet` (path_id × date × NAV). Visualised as the Strategy Performance Envelope chart — one of the most statistically rigorous visuals a fund presentation can show. Drags point estimates into a distributional context.

### 7.6 Regime-Conditional Performance Reporting

Beyond the VIX-overlay chart (Fig 3), run the full metric suite (§8) separately within each VIX regime (low/normal/high):
- Regime-conditional Sharpe, Sortino, hit rate, IC, turnover
- Output: `regime_performance.parquet` (regime × metric × 4-variant columns)
- This is what validates (or invalidates) the thesis that dynamic weighting earns its keep in high-VIX periods.

### 7.7 Multi-Universe Robustness

Run the full backtest on **three universe variants** to demonstrate the result is not a specification artefact:
1. **Default** — full 678-stock universe post-liquidity filter (§5.15)
2. **Top 500 by ADV** — subset of the most liquid
3. **US-only (S&P 500 approximation)** — 472 US names from CW1 universe

Report Sharpe / Max DD / Calmar across all three. Sharpe stable across specifications → robustness. Sharpe collapses in one → documented limitation.

### 7.8 Short-Sale Feasibility Filter

For every short-leg candidate:
1. Market cap > $1B (hard-to-borrow proxy; Diether-Lee-Werner 2009)
2. 30-day ADV > $10M (borrow-market liquidity proxy)
3. Not in top-5% of short-interest ratio (squeeze risk — approximated via yfinance `shortPercentOfFloat` when available)

Stocks failing filters are replaced by the next candidate in the composite ranking. Report the fraction of short candidates filtered out per rebalance.

### 7.9 Trade Ledger & Audit Trail

Every rebalance writes an immutable record to `trade_ledger.parquet`:
```
date, symbol, side (long/short), action (open/close/adjust),
old_weight, new_weight, notional_usd, predicted_impact_bp, proportional_cost_bp,
realised_fill_price, slippage_bp, leg_id, rebalance_id, seed, data_snapshot_sha256
```

Enables: P&L attribution per trade, regulatory-style audit trail, reproducibility verification.

### 7.10 Transaction Cost Scenarios

Per Task Guide spec: **20 bp per side** (headline — Korajczyk & Sadka 2004 estimate for liquid large-caps) and **30 bp per side** (sensitivity). Full metrics suite reported for both. Dynamic Net 20bp is the anchor metric for the report narrative.

### 7.11 Parallel Execution & Performance Engineering

- CPCV's 66 folds × 15 parameter combinations × 3 universes = ~3,000 backtest runs. Serial is infeasible.
- Use `joblib.Parallel(n_jobs=-1)` at the CV-fold level.
- Pre-compute factor scores once per rebalance (cache to disk); weight-engine swaps are fast.
- Vectorised numpy throughout; no Python loops over symbols in the critical path.
- Target runtime: full grid + CPCV + Monte Carlo < 90 minutes on a modern laptop.

### 7.12 Reproducibility Contract

Every final report number is traceable to:
1. **Git commit SHA** of the engine at the time of run.
2. **Data snapshot SHA-256** of the CW1 PostgreSQL dump used.
3. **NumPy/Python seed** (fixed = 42).
4. **config/backtest_config.yaml** content (version-controlled).

Reproducibility is certified via a CI step that re-runs the entire backtest from a clean clone and verifies bit-level Parquet identity. If the hash mismatches, the report does not ship.

### 7.13 Static vs Dynamic — Narrative Spine

Both variants run end-to-end from identical starting NAV (per Task Guide). Four-way comparison in the headline table:
1. **Static 30/30/25/15** (baseline)
2. **Dynamic via grid-searched (γ, λ)** (hindsight-optimal)
3. **Dynamic via Thompson Sampling** (ex-ante implementable, §5.4)
4. **Benchmark EW universe**

The gap between (2) and (3) quantifies the hindsight bias. The gap between (3) and (1) quantifies the genuine adaptive edge.

---

## 8. Performance & Risk Metrics Suite — `analytics/performance.py`

All metrics computed across **four columns**: Dynamic Gross · Dynamic Net 20bp · Static Net 20bp · Benchmark EW. Plus a 30bp table in the appendix.

### 8.1 Return & Risk-Adjusted (Viz Reference §1.1)
- Annualised return, Annualised vol, **Sharpe**, Sortino, **Information Ratio** vs EW benchmark, Calmar
- Sharpe reported with **[lower, upper] block-bootstrap 95% CI**
- **Deflated Sharpe** adjusted for grid-search multiplicity
- **PSR** at threshold 0.5

### 8.2 Drawdown & Tail (Viz Reference §1.2)
- Max drawdown (magnitude + duration in months)
- 99% HVaR (avg over 756-day rolling)
- 99% Expected Shortfall
- Skewness, Excess kurtosis

### 8.3 Distribution & Hit Rate (§1.3)
- Monthly hit rate, best/worst month, % negative months, downside deviation

### 8.4 L/S-Specific (§1.4 — "the insight most teams miss")
- **Long leg α** = R_long − R_benchmark
- **Short leg α** = R_benchmark − R_short
- Gross/Net exposure verification (gross ≈ 2.0, net ≈ 0.0)
- **Annualised 1-way turnover**
- **Portfolio β** vs benchmark (should be ≈ 0)

### 8.5 Factor-Level Diagnostics (§1.5)
- Per-factor IC (Spearman and Pearson)
- IC-IR = mean(IC) / stdev(IC)
- % positive IC months
- Factor return contribution via Fama-MacBeth β (§5.9)

### 8.6 Headline table (§1.6)
Produce the canonical 17-row × 4-column headline exhibit as a Parquet + LaTeX + Markdown table. Section 4.1 of the report opens with this table.

---

## 9. Visualisation Suite — `analytics/charts.py`

All 14 charts from Viz Reference Part 2, plus 3 diagnostic extensions. Every chart follows the Visual Style Guide: Navy #1B2A4A (dynamic), Blue #2E75B6 (static), Grey #7F8C8D (benchmark), Red #C0392B (drawdown), Green #27AE60 (positive). Factor colours: Mom=Navy, Val=Blue, Qual=Green, Sent=Orange. 150 DPI on-screen, 300 DPI for submission. Dates formatted `MMM 'YY`. Each function accepts a DataFrame and returns a `matplotlib.figure.Figure` — **the calling code saves**.

| # | Function | Section | Type | Size |
|---|---|---|---|---|
| 1 | `plot_cumulative_return(returns_df)` | 4.1 | Time series | 12×5.5 |
| 2 | `plot_drawdown(dd_series)` | 4.1 | Underwater, filled red | 12×5.5 |
| 3 | `plot_vix_regime_returns(returns, vix, regime)` | 4.2 | Dual-axis bars+line | 12×5.5 |
| 4 | `plot_param_sensitivity(grid_df)` | 4.4 | Annotated heatmap with bordered optimal cell | 7×5.5 |
| 5 | `plot_rolling_ic(ic_df)` | 4.3 | 4 lines, 3mo MA, IC=0.05 reference | 12×5.5 |
| 6 | `plot_factor_attribution(contrib_df)` | 4.3 | Stacked bar + total line | 8×5 |
| 7 | `plot_rolling_sharpe(returns_df)` | 4.1 | Time series, 12mo window | 12×5.5 |
| 8 | `plot_covid_zoom(returns, vix, regime)` | 4.5 | Dual-axis Feb–Jun 2020 | 12×5.5 |
| 9 | `plot_cost_comparison(returns_df)` | 4.6 | Gross vs net-20 vs net-30 | 12×5.5 |
| 10 | `plot_sector_exposure(weights_df)` | 4.7 | Heatmap over time | 7×5.5 |
| 11 | `plot_turnover(exposure_df)` | 4.6 | Monthly 1-way | 12×5.5 |
| 12 | `plot_ls_decomposition(returns_df)` | 4.3 | Annual stacked bar | 8×5 |
| 13 | `plot_ablation(ablation_df)` | 5 | Bar chart | 8×5 |
| 14 | `plot_covariance(cov_matrix)` | App. | Heatmap | 7×5.5 |
| 15 | `plot_deflated_sharpe_distribution(bootstrap_df)` | 5.1 | **Extension:** Sharpe distribution with CI | 12×5.5 |
| 16 | `plot_ff5_regression_loadings(regression_df)` | 4.3 | **Extension:** Bar with error bars | 8×5 |
| 17 | `plot_hrp_vs_minvar(weights_df)` | App. | **Extension:** Side-by-side heatmap | 10×5.5 |

---

## 10. Sensitivity, Ablation, Stress — `analytics/` sub-modules

### 10.1 Sensitivity (`sensitivity.py`, Xinyan)
- Grid: γ ∈ {0.0, 0.25, 0.50, 0.75, 1.0} × λ ∈ {±5%, ±10%, ±15%} = 15 combinations
- Per combination: full backtest + CPCV distribution of OOS Sharpes
- Output: `sensitivity_grid.parquet` with 15 × 66 CV-fold rows
- Headline: heatmap of **mean** OOS Sharpe; overlay optimal cell; report **std-deviation** in companion heatmap

### 10.2 Ablation (`ablation.py`, Xinyan)
Run backtest with each factor removed (weight=0, others re-normalised) → 4 single-factor-removal variants + full 4-factor model = **5 variants**. Also run factor-isolation (single-factor-only) variants as supplementary (4 more). Metric of interest: Sharpe, max DD, IR, FF5-α.

### 10.3 Comparison (`comparison.py`, Xinyan)
Four variants head-to-head:
- (a) **Static 30/30/25/15** baseline
- (b) **VIX-only tilting** (γ=0, λ as tuned)
- (c) **Dispersion-only scaling** (γ as tuned, λ=0)
- (d) **Combined dynamic** (full model)

Output: `comparison_results.parquet`. The narrative: "both levers contribute, neither alone dominates".

### 10.4 Stress (`stress.py`, Xinyan)
Three crisis windows:
- **COVID:** 2020-02-15 → 2020-06-30 (momentum crash, per Daniel & Moskowitz 2016)
- **2022 rate shock:** 2022-01-01 → 2022-10-31 (value rotation)
- **Q4 2025 momentum reversal:** 2025-10-01 → 2025-12-31

For each: DD with/without absolute-momentum filter, excluded-stock count, recovery speed. Plus **Monte Carlo permutation test** for dynamic-vs-static Sharpe gap over the full OOS window.

---

## 11. Team Allocation — Refined

Building on the Task Guide V2 (6 April 2026) allocation but refined with extension ownership.

| Team member | Role | Files / Responsibilities |
|---|---|---|
| **Tamer Atesyakar** | Developer (Lead) | `engine/config.py`, `data_loader.py`, `factors.py`, `zscore.py`, `portfolio.py`, `attribution.py`, `ml_enhancer.py`. Extensions: 5.1, 5.2, 5.3, 5.4, 5.9, 5.12. CW1↔CW2 DB interface. `types.py` co-owned. |
| **Tsz Fung Huang (Lucian)** | Developer | `engine/dynamic_weights.py`, `backtest.py`, `costs.py`, `runner.py`. Extensions: 5.5 (CPCV loop), 5.6 (HMM). Main.py entry. Integration test on 10-stock subset. |
| **Peixi Xiong** | Specialist | `analytics/performance.py`, `validation.py`, `attribution_analysis.py`. Extensions: 5.7, 5.8, 5.10. Headline metric table. QA for all metric computations. |
| **Moyan Yu** | Specialist | `analytics/charts.py`. All 14 mandatory charts + 3 extensions. Visual-style-guide compliance. Colour palette, DPI, date formatting. |
| **Xinyan Chen** | Specialist | `analytics/sensitivity.py`, `ablation.py`, `comparison.py`, `stress.py`. Extension: 5.13 (permutation). Sphinx docs update — extends `docs/architecture.rst` with CW2 module diagrams. |
| **Ayudhya Vidyaningtyas** | IPO | Report lead: Section 1 (Intro), 2 (Methodology), 6 (Fund Pitch), 8 (Conclusions). ~6,500 words. Narrative spine. |
| **Ryan Lin** | IPO | Report co-lead: Section 4 (Empirical Results), 5 (Ablation/Robustness), 9 (Team Contributions). ~9,000 words. Extension 5.11 (capacity calc). Benchmarking research. |
| **Jianyang Zuo** | IPO | Report: Section 3 (Data Summary, CW1 Integration), 7 (Limitations), Appendices. ~3,500 words. CW1 cross-referencing, citation audit. |

### 11.1 IPO Quality-Control Tasks
- **Narrative consistency** — dynamic-vs-static is the single narrative spine
- **Chart review** — every chart matches Viz Reference style guide
- **Metric sanity** — back-of-envelope verification of headline numbers
- **Citation audit** — every academic claim has a Harvard-style reference
- **Word-count management** — 20,000 cap strictly monitored

### 11.2 Communication protocol
- Daily 15-min async standup: (1) what I did, (2) what I'm doing next, (3) blockers
- Cross-boundary requests: GitHub issue with team label, tagging assignee, specifying what/format/by-when
- One owner per `.py` file — no shared edits
- Function signatures locked in Week 1 via `engine/types.py`

---

## 12. Timeline — Compressed to Reality (Today = 17 Apr 2026)

### Week 2 remainder: 18–20 Apr (catch-up on missed W1)
**Blockers to resolve first.** The Task Guide's W1 plan called for Tamer to finish `config.py`, `data_loader.py`, `factors.py`, `zscore.py` by 13 Apr. None exist. This is the critical path.

| Day | Deliverable | Owner |
|---|---|---|
| **Fri 18 Apr** | `engine/types.py` (Pydantic contracts) signed off by both devs; `config/backtest_config.yaml` scaffolded; `pyproject.toml` Poetry-initialised; folder structure committed | Tamer + Lucian |
| **Sat 19 Apr** | `engine/config.py`, `engine/data_loader.py` (w/ PIT discipline), unit tests | Tamer |
| **Sun 20 Apr** | `engine/factors.py` (all 4 raw-score computations, winsorised); `engine/zscore.py` (sector-neutral, handles <5-stock sectors) | Tamer |
| **Sun 20 Apr** | `engine/dynamic_weights.py` stub (percentile-based regime + D_f); `engine/costs.py` stub (20/30bp proportional) | Lucian |
| **Sun 20 Apr** | `analytics/performance.py` skeleton on synthetic data; `analytics/validation.py` | Peixi |
| **Sun 20 Apr** | `analytics/charts.py` skeleton — Fig 1, 2, 3 on synthetic data | Moyan |
| **Sun 20 Apr** | Report Section 1, 2 first draft | Ayudhya |

### Week 3: 21–27 Apr (core implementation + T2 extension stack)
| Day | Deliverable | Owner |
|---|---|---|
| **Mon 21 Apr** | `engine/portfolio.py` v1 — vanilla MinVar with Ledoit-Wolf; HVaR scaling. Unit tests | Tamer |
| **Mon 21 Apr** | `engine/backtest.py` v1 — monthly loop (dependency-injection architecture §7.1); writes `portfolio_returns.parquet` | Lucian |
| **Mon 21 Apr** | `engine/factors.py` + **factor orthogonalisation (§5.14)** — Gram-Schmidt residualisation | Tamer |
| **Tue 22 Apr** | `engine/portfolio.py` v2 — **denoised LW (§5.1)** + **turnover penalty (§5.2)** + **HRP (§5.3)** as swappable strategy | Tamer |
| **Tue 22 Apr** | `engine/data_loader.py` — **liquidity filter (§5.15)** integrated | Tamer |
| **Tue 22 Apr** | `engine/dynamic_weights.py` + CPCV grid-search harness (§5.5, §7.4 nested CV) | Lucian |
| **Tue 22 Apr** | **`engine/risk_scaler.py`** — chain HVaR → **vol targeting (§5.16)** → **DD overlay (§5.17)** | Lucian |
| **Wed 23 Apr** | **`engine/bandit.py`** — Linear Thompson Sampling (§5.4) with signal-decay reward | Tamer |
| **Wed 23 Apr** | First **end-to-end real backtest** run — full universe, 10-stock integration test green | Both devs |
| **Wed 23 Apr** | `analytics/performance.py` switched synthetic→real; headline metric table | Peixi |
| **Wed 23 Apr** | All 14 charts generated on real data | Moyan |
| **Thu 24 Apr** | `engine/attribution.py` — **Fama-MacBeth (§5.9)** + **Kyle's-λ capacity (§5.11)** | Tamer |
| **Thu 24 Apr** | **Monte Carlo 10k path simulation (§7.5)** — 6-month block bootstrap | Lucian |
| **Thu 24 Apr** | `engine/ml_enhancer.py` — LightGBM OOS IC check. **Gate: IC > 0.03** | Tamer |
| **Thu 24 Apr** | `analytics/sensitivity.py` — γ×λ grid + CPCV + deflated-Sharpe heatmap | Xinyan |
| **Thu 24 Apr** | `analytics/attribution_analysis.py` — **FF5+Mom Newey-West regression (§5.8)** | Peixi |
| **Fri 25 Apr** | **Multi-universe robustness backtest (§7.7)** — 3 universe variants | Lucian |
| **Fri 25 Apr** | `analytics/ablation.py`, `comparison.py`, `stress.py` — full suite | Xinyan |
| **Fri 25 Apr** | **Regime-conditional performance (§7.6)** + short-feasibility filter (§7.8) | Lucian + Xinyan |
| **Fri 25 Apr** | Report Sections 4, 5 first draft — **highest-value IPO work** | Ryan + Ayudhya |
| **Sat 26 Apr** | **Block bootstrap + Deflated Sharpe + PSR + MBL (§5.7, §5.18)** | Peixi |
| **Sat 26 Apr** | Brinson-Fachler (§5.10, T3) + capacity narrative drafting | Peixi + Ryan |
| **Sun 27 Apr** | Full report §3 (Data), §6 (Fund Pitch) first drafts | Jianyang + Ayudhya |

### Week 4: 28 Apr – 1 May (polish & submission)
| Day | Deliverable | Owner |
|---|---|---|
| **Mon 28 Apr** | Final backtest run on frozen data. Regenerate all artefacts. Test suite green. | All devs |
| **Mon 28 Apr** | Final chart polish (colours, fonts, DPI). Visual-style audit | Moyan + IPO team |
| **Tue 29 Apr** | Report Sections 7, 8, 9. Appendices assembled | Jianyang, Ayudhya, Ryan |
| **Tue 29 Apr** | Sphinx docs complete; architecture diagram finalised | Xinyan |
| **Wed 30 Apr** | Full cross-review pass: metric sanity, citation audit, word-count, narrative | All IPOs |
| **Wed 30 Apr** | Final test pass (all engine + analytics tests green, ≥80% coverage) | All devs |
| **Thu 1 May** | 09:00 — final reproducibility test from clean clone. 12:00 — Turnitin upload. **17:00 GMT deadline**. | Ayudhya (submit), Ryan (verify) |

---

## 13. Testing & Documentation

### 13.1 Test strategy (Task Guide §2 — ≥80% coverage)
- **Unit tests** — every engine function: factor computations produce expected z-scores on toy data; MinVar optimiser produces valid weights (non-negative, sum=1, max 5%); VaR matches manual calculation on toy series; currency conversion correct; Fama-MacBeth β matches statsmodels reference.
- **Integration tests** — full backtest on 10-stock × 6-month subset (Task Guide spec). Verify: gross ≥ net; turnover matches weight diffs; static and dynamic start from same NAV.
- **Analytics tests** — each chart function produces a matplotlib Figure with expected number of axes on synthetic data; every metric function cross-validated against quantstats / empyrical on the same data; validation test fed intentionally broken DataFrames verifies flags raised.
- **Reproducibility test** — fresh clone + `poetry install` + `python Main.py --config config/backtest_config.yaml` reproduces numerical identity of all Parquet outputs.

### 13.2 Documentation
- **Sphinx update:** `docs/` extended with CW2 modules, API reference (autodoc), architecture diagram, data-lineage diagram (CW1 DB → engine → analytics → report).
- **Docstrings:** every public function has NumPy-style docstring with `Parameters`, `Returns`, `Examples`, `References`.
- **README.md:** installation, quick-start, reproducibility check, citation list.
- **CHANGELOG.md:** append-only log of every deployed version, tied to git tags.

### 13.3 Code quality (CW1 toolchain re-used)
- `black --line-length 110`, `isort profile=black`, `flake8` — zero violations target
- `bandit` security scan — zero high-severity
- Type hints on every public function, enforced via `mypy --strict` on `engine/` at minimum
- No magic numbers — all parameters in `config/backtest_config.yaml`

---

## 14. Result-Maximisation Design Principles

This section codifies the philosophy that turns sophistication into credibility. Every extension in §5 obeys these principles.

**P1 — Pre-registration.** The full enhancement set was specified before any OOS evaluation. No extension is added mid-backtest in response to poor OOS performance.

**P2 — Citation-or-cut.** Every enhancement carries a peer-reviewed citation and a specific mechanism. Pure engineering tweaks (e.g., caching) are documented separately in the code, not claimed as alpha.

**P3 — Deflation is default.** All Sharpe / IR values are reported with (a) block-bootstrap 95% CI, (b) Deflated Sharpe adjusted for the full research-trajectory trial count (N = enhancements × CPCV folds × universe variants), (c) PSR at threshold 0.5. Raw Sharpe *never* appears as a headline without its deflated companion.

**P4 — Baseline-always.** Every results table has a "Vanilla baseline" column showing the CW1-spec implementation with zero extensions. Enhanced results are the *final* numbers but never the *only* numbers.

**P5 — Negative results are published.** If the ML enhancer (§5.12), HMM regime (§5.6), or sentiment signal (§5.13 ablation) fail their pre-specified gates, they are *reported as null findings*, not hidden. Section 7 of the report explicitly surfaces them.

**P6 — OOS is sacred.** No parameter touches the 2023-07→2026-03 test window except via a CPCV protocol with purge + embargo. An automated PIT audit (§7.3 rule 7) enforces this at test time.

**P7 — Fund-pitch honesty.** Section 6 of the report includes an explicit "when the strategy underperforms" paragraph — value traps, momentum crashes, sentiment-signal decay, VIX-percentile misfires. The marker should see the limits alongside the claims.

**P8 — Result targets (pre-registered, not guaranteed).**

| Metric | Target | Rationale |
|---|---|---|
| Sharpe (Dynamic Net 20bp, deflated) | ≥ 1.2 | Institutional threshold for active equity L/S |
| Max Drawdown (with §5.17 overlay) | ≤ 8% | Critical for institutional allocator appeal |
| Calmar ratio | ≥ 2.0 | Combines Sharpe and DD in a fund-pitch number |
| FF5+Mom α (Newey-West HAC) | ≥ 3% p.a., t-stat > 2.0 | Defensible α claim after factor exposures |
| Dynamic-vs-Static Sharpe gap (permutation p) | p < 0.05 | Proves the thesis question |
| Portfolio β vs benchmark | \|β\| ≤ 0.1 | Market-neutrality verification |
| Annual 1-way turnover (post §5.2) | ≤ 250% | Cost-sustainable |
| Capacity (§5.11 at 15 bp max impact) | ≥ $500M | Real-world deployability claim |
| Test coverage (engine + analytics) | ≥ 85% | Production-grade code mark |

**P9 — Visual style as narrative device.** Charts use the Viz Reference palette (Navy / Blue / Grey / Red / Green) consistently so the reader builds colour-fluency. The cumulative-return Fig 1 + drawdown Fig 2 + VIX-overlay Fig 3 triad opens the empirical section — three charts telling the core story before any number.

**P10 — Institutional-grade disclosures.** Capacity (§5.11), turnover (§5.2), realised vol (§5.16 target), drawdown profile (§5.17), and fee structure (1 + 15 above T-bill) are stated in Section 6 as an allocator would want them — with sensitivities, not just point estimates.

---

## 15. Risk Register & Contingency

| # | Risk | Probability | Impact | Mitigation |
|---|---|---:|---:|---|
| 1 | Schedule overrun — already 1 week behind Task Guide plan | High | High | Freeze Tier-3 scope early; ship Tier-1+T2 baseline first; T3 extensions gated behind config flag |
| 2 | CW1 DB unavailable during backtest | Low | High | Snapshot CW1 PostgreSQL to local Parquet in Week 2 as backup (data_loader can fall back) |
| 3 | Survivorship bias distorts results (CW1 §5.1) | Certain | Medium | Document prominently in Report §7; estimate magnitude by cross-referencing `ingestion_log` delisted entries vs rebalance dates |
| 4 | Sentiment signal contributes no alpha (flagged in CW1 §5.2) | Medium | Low | Planned for: ablation explicitly includes "remove sentiment" variant; report honest null result |
| 5 | Numerical instability in Ledoit-Wolf on near-singular covariance | Medium | Medium | Condition-number check before inversion; fall back to identity shrinkage target if condition > 1e10 |
| 6 | CPCV grid-search runtime blows out (15 combos × 66 folds × backtest time) | Medium | Medium | Parallelise with joblib; if still too slow, reduce to purged walk-forward (no CPCV); cache intermediate factor scores |
| 7 | ML enhancer overfits and shows inflated OOS metric | Medium | Low | Gated inclusion (IC > 0.02 AND deflated Sharpe improvement > 0.1); honest null report if gate fails |
| 8 | HMM fails to converge on VIX data | Low | Low | HMM is T3, optional. Default to percentile threshold. |
| 9 | Report word count overflow | Medium | Low | Hard budget per section enforced by Ayudhya (Report lead); weekly word-count audit |
| 10 | Submission infrastructure issues on 1 May | Low | High | Submit by 12:00 GMT, 5 hours before deadline. Submit to Turnitin, not just LMS; keep PDF backup on 2 devices. |
| 11 | Citation errors — wrong attribution of formulas | Low | Medium | Jianyang owns citation audit; every formula traced back to specific paper + page |
| 12 | Engine/analytics boundary violated | Medium | Medium | GitHub PR reviews enforce directory ownership; CI rule: analytics/ cannot import engine/ internals |
| 13 | **Multi-enhancement overfitting** — stacking §5.14–5.18 could inflate in-sample Sharpe | High | High | **Deflated Sharpe (§5.7) with trial-count = enhancements × CPCV folds**; pre-registration (P1); baseline-always column (P4); ablate every enhancement separately (§5.13) |
| 14 | Liquidity filter (§5.15) removes 15% of universe — may be too aggressive | Low | Medium | Report sensitivity at 5%, 10%, 15% filter thresholds; default is tightest |
| 15 | Vol targeting (§5.16) causes leverage spikes in calm periods | Medium | Medium | Clip scalar to [0.3, 1.5]; report max realised leverage; re-scale conservatively if >150% |
| 16 | Drawdown overlay (§5.17) triggers late-stage de-risking that locks in losses | Medium | Medium | 3-month sensitivity on DD thresholds; report turnover cost of overlay; compare Calmar with/without |
| 17 | Thompson Sampling (§5.4) under-explores early, over-concentrates on one arm | Medium | Medium | Warm-up period 6 months; variance inflation in initial posteriors; report exploration rate per month |
| 18 | CPCV grid explodes — 66 folds × 15 params × 3 universes = 2,970 runs | Medium | Medium | Cache factor scores per date; joblib parallel; fall back to purged walk-forward if >4h runtime |
| 19 | Monte Carlo (§7.5) block-bootstrap dependence on block-length | Low | Low | Sensitivity: block lengths 3, 6, 12 months; report all three CIs |

---

## 15. Report Outline — 20,000 Word Budget

Per Task Guide §4.1, with word budgets tuned for the advanced content:

| Section | Lead | Words | Content highlights |
|---|---|---:|---|
| **1. Introduction & Investment Hypothesis** | Ayudhya | 1,500 | Restate Vayanos-Woolley; frame regime-conditional flow-cycle fingerprint hypothesis (§2.1 of this plan); research question explicitly: "does dynamic weighting justify its complexity?" |
| **2. Methodology** | Ayudhya | 3,000 | Factor construction; sector-neutral z-scoring (Eq. 8); dynamic weighting (Eqs. 1–3); MinVar + HVaR (Eq. 10); **Bayesian weight updating**; **Denoised LW**; **CPCV**; **Fama-MacBeth**. Mathematical prose. |
| **3. Data Summary & CW1 Integration** | Jianyang | 1,500 | Coverage recap (678 × 11 sources); currency conversion; data quality checks; **survivorship bias quantification**; point-in-time handling |
| **4.1 Headline Performance** | Ryan + Ayudhya | 1,000 | Headline table (17 × 4); cumulative return chart; underwater chart; **Sharpe with bootstrap CI**; **Deflated Sharpe** |
| **4.2 Regime Analysis** | Ryan | 800 | VIX regime classification; monthly returns by regime; conditional Sharpe; HMM cross-check |
| **4.3 Factor Decomposition** | Ryan | 1,200 | Rolling IC; stacked attribution; **Fama-MacBeth premia**; **FF5+Mom α with Newey-West**; L/S leg decomposition |
| **4.4 Parameter Sensitivity** | Ryan | 1,000 | γ×λ heatmap; CPCV std heatmap; deflated-Sharpe-adjusted optimal (γ*, λ*) |
| **4.5 Stress Testing** | Ryan | 800 | COVID 2020; 2022 rate shock; Q4 2025; DD with/without absolute-momentum filter |
| **4.6 Transaction Costs** | Ryan | 600 | 20bp vs 30bp net; turnover time series; **turnover-penalised MinVar** comparison |
| **4.7 Sector Exposure** | Ryan | 600 | Sector heatmap over time; **Brinson-Fachler** allocation vs selection |
| **5. Ablation & Robustness** | Ryan | 2,000 | 5-variant ablation; static vs VIX-only vs dispersion-only vs combined; sentiment signal assessment; **Monte Carlo permutation p-value**; **HRP comparison** |
| **6. Fund Pitch** | Ayudhya | 1,500 | Strategy as product; risk-return profile; **capacity estimate (Kyle's λ)**; fee structure (1+15 below 2-and-20); when strategy underperforms (value traps, momentum crashes); target allocator |
| **7. Limitations & Future Work** | Jianyang | 1,500 | Survivorship bias magnitude; sentiment decay honest discussion; VADER vs FinBERT (future); VIX as global proxy; monthly rebalance constraint; factor crowding; overfitting risk |
| **8. Conclusions** | Ayudhya | 500 | Dynamic weighting's marginal value; regime-conditional edge; three future-work priorities |
| **9. Team Contributions & Reflections** | Ryan | 1,000 | CW1→CW2 integration; lessons from clean data contract; IPO-specialist-developer collaboration |
| **Appendices** | Jianyang | — | 30bp metric table; covariance vis; architecture diagram; extended ablation; reproducibility statement |
| **Total** | | **~18,500** | (1,500 buffer for appendix captions and references) |

---

## 16. Reproducibility & Integration

### 16.1 CW1 dependencies
- **Direct SQL access:** `engine/data_loader.py` connects to `localhost:5439`, `schema=systematic_equity`, reads tables directly. No data duplication.
- **Package reuse:** add CW1's `ift_global` Git dependency to CW2 `pyproject.toml`.
- **Schema contract:** if a CW2 query needs a column CW1 didn't produce, open a CW1 issue (shouldn't happen).

### 16.2 Pinned environment
- Python 3.10
- Poetry-managed lockfile
- Seed = 42 for numpy, random, torch (if ML enhancer used)
- Docker: extend CW1's `docker-compose.yml` rather than duplicate; reuse postgres/mongo/minio containers.

### 16.3 Frozen data snapshot
Capture a Parquet snapshot of CW1 PostgreSQL tables as of 28 April 2026. All final-run results use this frozen snapshot. Include the SHA-256 of the snapshot in the report appendix as a reproducibility hash.

---

## 17. Grade-Maximising Summary — Why This Plan Wins

- **Concept (25%)**: Vayanos-Woolley regime-conditional flow-cycle hypothesis; factor orthogonalisation isolates independent premia (Novy-Marx 2013 lineage); every factor ≥3 citations; Contextual Thompson Sampling frames weight selection as sample-efficient RL with near-optimal Bayesian regret bounds (Russo-Van Roy 2018) — the interpretable, data-scale-appropriate alternative to ad-hoc priors.
- **Method (30%)**: Dependency-injected backtest engine with 10 swappable components; Denoised Ledoit-Wolf + turnover-penalised MinVar + HRP (three portfolio constructions head-to-head); **conditional volatility targeting** (Moreira-Muir 2017) + **drawdown-control overlay** + HVaR composed as risk scalers; CPCV with nested-CV hyperparameter protection; factor orthogonalisation; liquidity filter; strict point-in-time discipline audited by automated tests; Monte Carlo 10k-path bootstrap; multi-universe robustness; Thompson-Sampling arm posteriors; ≥85% test coverage; pinned seed + data-SHA256 reproducibility contract.
- **Empirical (25%)**: Block-bootstrap Sharpe CIs + **Deflated Sharpe** + PSR + **Minimum Backtest Length power analysis** address multiple-testing explicitly; FF5+Mom regression with Newey-West HAC quantifies pure α; Fama-MacBeth attributes per-factor premia with t-stats; Brinson-Fachler decomposes sector allocation vs selection; Monte Carlo permutation p-values for dynamic-vs-static; three-crisis stress suite; regime-conditional performance tables.
- **Documentation (10%)**: Sphinx extension of CW1 docs; visual-style-compliant 17 charts; word-budgeted ~18,500-word report with clear section ownership; architecture + data-lineage diagrams; NumPy-style docstrings throughout; reproducibility certified in CI.
- **Integration (5%)**: Direct CW1 DB query (no data duplication); reused `ift_global`; shared CHANGELOG; schema-consistency across both projects; Docker-compose extension rather than duplication.
- **Bonus (5%)**: One high-quality in-class GitHub issue.

**Result targets (pre-registered in §14 P8):** Sharpe (net 20bp, deflated) ≥ 1.2 · Max DD ≤ 8% · Calmar ≥ 2.0 · FF5+Mom α ≥ 3% p.a. (t > 2.0) · permutation p < 0.05 vs static · capacity ≥ $500M at 15bp impact · |β| ≤ 0.1 · coverage ≥ 85%.

**Target combined grade: distinction (≥70%), aiming 80%+ with full Tier-2 extensions delivered, 85%+ with Tier-3 layer intact.**

---

## 18. Key References — to be cited consistently (Harvard)

### Theoretical framework
- Vayanos, D. & Woolley, P. (2013). 'An institutional theory of momentum and reversal', *RFS*, 26(5), 1087–1145.
- Vayanos, D. & Woolley, P. (2012). 'A theoretical analysis of momentum and value strategies', LSE WP.
- Asness, C. S., Moskowitz, T. J. & Pedersen, L. H. (2013). 'Value and momentum everywhere', *JF*, 68(3), 929–985.

### Factor construction
- Jegadeesh, N. & Titman, S. (1993). 'Returns to buying winners and selling losers', *JF*, 48(1), 65–91.
- Antonacci, G. (2016). 'Risk premia harvesting through dual momentum', SSRN 2042750.
- Asness, C. S., Frazzini, A. & Pedersen, L. H. (2019). 'Quality minus junk', *RAS*, 24(1), 34–112.
- Asness, C. S., Frazzini, A., Israel, R. & Moskowitz, T. J. (2015). 'Fact, fiction, and value investing', *JPM*, 42(1), 34–52.
- Piotroski, J. D. (2000). 'Value investing: The use of historical financial statement information', *JAR*, 38(Supp), 1–41.

### Sentiment
- Tetlock, P. C. (2007). 'Giving content to investor sentiment', *JF*, 62(3), 1139–1168.
- Da, Z., Engelberg, J. & Gao, P. (2011). 'In search of attention', *JF*, 66(5), 1461–1499.
- Hutto, C. J. & Gilbert, E. (2014). 'VADER', Proc. 8th AAAI ICWSM.
- Araci, D. (2019). 'FinBERT: Financial Sentiment Analysis with Pre-Trained Language Models', *arXiv*:1908.10063.

### Portfolio construction
- Ledoit, O. & Wolf, M. (2004). 'A well-conditioned estimator for large-dimensional covariance matrices', *JMVA*, 88(2), 365–411.
- López de Prado, M. (2020). *Machine Learning for Asset Managers*. Cambridge Elements.
- López de Prado, M. (2016). 'Building diversified portfolios that outperform out of sample', *JPM*, 42(4), 59–69.
- López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley. **(CPCV — Ch. 7)**
- DeMiguel, V., Garlappi, L. & Uppal, R. (2009). 'Optimal versus naive diversification', *RFS*, 22(5), 1915–1953.
- Baker, M., Bradley, B. & Wurgler, J. (2011). 'Benchmarks as limits to arbitrage', *FAJ*, 67(1), 40–54.

### Risk & performance
- Jorion, P. (2007). *Value at Risk*. McGraw-Hill.
- Bailey, D. H. & López de Prado, M. (2014). 'The deflated Sharpe ratio', *JPM*, 40(5), 94–107.
- Bailey, D. H., Borwein, J., López de Prado, M. & Zhu, Q. J. (2017). 'Pseudo-mathematics and financial charlatanism: The effects of backtest overfitting on out-of-sample performance', *Notices of the AMS*, 61(5), 458–471.
- Politis, D. N. & Romano, J. P. (1994). 'The stationary bootstrap', *JASA*, 89(428), 1303–1313.
- Daniel, K. & Moskowitz, T. J. (2016). 'Momentum crashes', *JFE*, 122(2), 221–247.
- Ang, A., Hodrick, R. J., Xing, Y. & Zhang, X. (2006). 'The cross-section of volatility and expected returns', *JF*, 61(1), 259–299.
- Moreira, A. & Muir, T. (2017). 'Volatility-managed portfolios', *JF*, 72(4), 1611–1644.
- Harvey, C. R., Hoyle, E., Korgaonkar, R., Rattray, S., Sargaison, M. & van Hemert, O. (2018). 'The impact of volatility targeting', *JPM*, 45(1), 14–33.
- Harvey, C. R., Liu, Y. & Zhu, H. (2016). '… and the cross-section of expected returns', *RFS*, 29(1), 5–68.
- Korn, O., Korn, R. & Kroisandt, G. (2017). 'The value of capital-buffer-based drawdown control', SSRN.
- Kritzman, M. & Rich, D. (2002). 'The mismeasurement of risk', *FAJ*, 58(3), 91–99.
- Novy-Marx, R. (2013). 'The other side of value: The gross profitability premium', *JFE*, 108(1), 1–28.
- Diether, K. B., Lee, K.-H. & Werner, I. M. (2009). 'Short-sale strategies and return predictability', *RFS*, 22(2), 575–607.

### Reinforcement learning & bandits (for §5.4 Thompson Sampling)
- Thompson, W. R. (1933). 'On the likelihood that one unknown probability exceeds another in view of the evidence of two samples', *Biometrika*, 25(3/4), 285–294.
- Li, L., Chu, W., Langford, J. & Schapire, R. E. (2010). 'A contextual-bandit approach to personalized news article recommendation', *WWW*, 661–670.
- Agrawal, S. & Goyal, N. (2013). 'Thompson sampling for contextual bandits with linear payoffs', *ICML*, 127–135.
- Russo, D. J., Van Roy, B., Kazerouni, A., Osband, I. & Wen, Z. (2018). 'A tutorial on Thompson sampling', *Foundations and Trends in Machine Learning*, 11(1), 1–96.

### Attribution & asset pricing
- Fama, E. F. & MacBeth, J. D. (1973). 'Risk, return, and equilibrium: Empirical tests', *JPE*, 81(3), 607–636.
- Fama, E. F. & French, K. R. (2015). 'A five-factor asset pricing model', *JFE*, 116(1), 1–22.
- Carhart, M. M. (1997). 'On persistence in mutual fund performance', *JF*, 52(1), 57–82.
- Newey, W. K. & West, K. D. (1987). 'A simple, positive semi-definite heteroskedasticity and autocorrelation consistent covariance matrix', *Econometrica*, 55(3), 703–708.
- Brinson, G. P. & Fachler, N. (1985). 'Measuring non-US equity portfolio performance', *JPM*, 11(3), 73–76.
- Gu, S., Kelly, B. & Xiu, D. (2020). 'Empirical asset pricing via machine learning', *RFS*, 33(5), 2223–2273.

### Capacity & liquidity
- Kyle, A. S. (1985). 'Continuous auctions and insider trading', *Econometrica*, 53(6), 1315–1335.
- Amihud, Y. (2002). 'Illiquidity and stock returns', *JFM*, 5(1), 31–56.
- Korajczyk, R. A. & Sadka, R. (2004). 'Are momentum profits robust to trading costs?', *JF*, 59(3), 1039–1082.
- Almgren, R. & Chriss, N. (2001). 'Optimal execution of portfolio transactions', *JR*, 3, 5–39.

### Regime modelling
- Hamilton, J. D. (1989). 'A new approach to the economic analysis of nonstationary time series', *Econometrica*, 57(2), 357–384.
- Bender, J., Sun, X., Thomas, R. & Zdorovtsov, V. (2018). 'The promises and pitfalls of factor timing', *JPM*, 44(4), 79–92.
- Asness, C. S., Ilmanen, A. & Maloney, T. (2017). 'Market timing: Sin a little', *JIM*, 15(3), 23–40.

### Other
- Efron, B. & Tibshirani, R. J. (1994). *An Introduction to the Bootstrap*. CRC Press.
- Jacquier, E. & Polson, N. (2012). 'Asset pricing in a Bayesian framework', *Handbook of Bayesian Econometrics*.
- Shumway, T. (1997). 'The delisting bias in CRSP data', *JF*, 52(1), 327–340.
- Grinold, R. C. & Kahn, R. N. (2000). *Active Portfolio Management*. McGraw-Hill.
- Cornell, B. & Damodaran, A. (2021). 'Value investing: Requiem, rebirth or reincarnation?', NYU WP.
- J.P. Morgan Asset Management (2026). 'Factor Views: 1Q 2026', *Portfolio Insights*.
- MSCI (2023). *MSCI Quality Indexes Methodology*.

---

## 19. One-Page Summary (for team standup)

**What we're building.** Monthly-rebalanced, dollar-neutral 4-factor long/short equity strategy on a liquidity-filtered 678→~580 stock universe, with VIX-regime + dispersion-based dynamic weighting — **both** a grid-searched variant (hindsight-optimal) and a **Contextual Thompson Sampling** variant (ex-ante implementable, classical RL). Production-grade backtest engine: 10 swappable components (dependency-injected), nested CV, Monte Carlo 10k-path bootstrap, regime-conditional + multi-universe robustness, full audit trail with seed + data-SHA256 reproducibility.

**T2 rigour layer (must-ship):** Denoised LW · turnover-penalised MinVar · HRP · Thompson Sampling · CPCV with purge+embargo · **factor orthogonalisation** · **liquidity filter** · **conditional volatility targeting** · **drawdown-control overlay** · block-bootstrap CIs + Deflated Sharpe + PSR + **MBL power analysis** · FF5+Mom α (Newey-West) · Fama-MacBeth · Kyle's-λ capacity · Monte Carlo permutation test.

**T3 stretch:** HMM regime · Brinson-Fachler · gated ML residual enhancer.

**Explicitly cut as noise:** Quality×Value interaction · Almgren-Chriss (supersedes spec) · risk-parity leg sizing · DCC-GARCH · Black-Litterman · GRS · tabular/deep RL.

**Critical path (next 48h — W2 catch-up):** `engine/types.py` (Fri 18) → `config/backtest_config.yaml` (Fri 18) → `data_loader.py` with PIT + liquidity filter (Sat 19) → `factors.py` with orthogonalisation (Sun 20) → `backtest.py` + dependency-injection skeleton (Sun 20). First end-to-end synthetic run **Monday 21 Apr**.

**The narrative spine.** "Does dynamic weighting justify its complexity?" Answered by **four-way head-to-head**: (1) static baseline, (2) grid-searched dynamic (hindsight-optimal), (3) Thompson Sampling (ex-ante implementable), (4) EW benchmark — with Deflated Sharpe, permutation p-values, and Monte Carlo cones of uncertainty. The gap (2)–(3) quantifies hindsight luck; the gap (3)–(1) quantifies genuine adaptive edge.

**Headline pitch metrics.** Sharpe (Dynamic Net 20bp, deflated, with 95% block-bootstrap CI) · Max DD (with §5.17 overlay) · Calmar · 99% HVaR + ES · FF5+Mom α with Newey-West t-stat · Annualised 1-way turnover · Long/short leg α decomposition · Kyle's-λ capacity estimate at 15bp max impact · |β| vs benchmark.

**Pre-registered targets (§14 P8).** Sharpe ≥ 1.2 · Max DD ≤ 8% · Calmar ≥ 2.0 · FF5+Mom α ≥ 3% p.a. · permutation p < 0.05 · capacity ≥ $500M · |β| ≤ 0.1 · coverage ≥ 85%.

**Submission.** 12:00 GMT 1 May 2026 via Turnitin (5-hour buffer before 17:00 deadline).

---

*Document version 1.1 · Revised 17 April 2026 · Owners: all team members · Changes in CHANGELOG.md*
