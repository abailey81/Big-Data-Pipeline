Architecture
============

The CW2 backtest engine is a dependency-injected event-driven pipeline
(PLAN §7.1).  Every component is swappable — the backtest loop sees only
protocol-level interfaces.

Data flow
---------

.. code-block:: text

    CW1 PostgreSQL (port 5439, schema=systematic_equity)
        │
        ▼
    DataLoader ──► PITContext (per rebalance date)
        │
        ▼
    FactorEngine ──► raw scores (momentum, value, quality, sentiment)
        │
        ▼
    ZScoreEngine ──► sector-neutral z + Gram-Schmidt orthogonalisation
        │
        ├─► StaticWeights ──┐
        ├─► DynamicGridWeights ──┤
        └─► BanditWeights (Thompson Sampling) ──┤
                                                ▼
                                    PortfolioEngine (MinVar DLW + turnover)
                                                │
                                                ▼
                                    CompositeRiskScaler
                                       (HVaR → Vol-target → DD-control)
                                                │
                                                ▼
                                    CostModel (20/30 bp proportional)
                                                │
                                                ▼
                                    TradeLedger + MetricTracker
                                                │
                                                ▼
                                    output/*.parquet (17 files)
                                                │
                                                ▼
                                    analytics/ (charts + metrics + tests)

Component responsibilities
--------------------------

- **DataLoader** — PIT-disciplined SQL reader; builds ``PITContext`` with
  prices, fundamentals, ratios, sentiment, FX, VIX, RF, benchmark, and
  ADV for a single rebalance date.

- **FactorEngine** — Computes 12-1 momentum, equal-weighted B/P+E/P+CF/P
  value composite, ROE+stability+inverse-D/E quality composite, and
  pre-computed VADER sentiment.  All four factors are computed and
  surfaced in ``factor_ic.parquet`` for the diagnostic IC exhibit; the
  implemented composite is two-factor (momentum + value at 50/50) per
  the report's §§1.2, 2.2.1, 4.2 reduction on out-of-sample IC evidence.

- **ZScoreEngine** — Sector-neutral winsorised z-score + sequential
  Gram-Schmidt orthogonalisation (§5.14).

- **Weight engines** — StaticWeights / DynamicGridWeights / BanditWeights
  all expose the same interface and can be swapped by strategy flag.

- **PortfolioEngine** — Four swappable constructions: MinVar with vanilla LW,
  Denoised LW, turnover-penalised MinVar, and HRP.

- **CompositeRiskScaler** — Three-stage chain applied to every variant:
  99% HVaR-scaling → Moreira-Muir vol-target → Korn et al. DD-overlay.

- **CostModel** — Spec-compliant proportional costs at 20 and 30 bp/side.

- **BacktestEngine** — Event-driven loop iterating monthly NYSE trading-day
  ends; produces 17 Parquet artefacts per the data contract documented
  in the project README.

Point-in-time guarantees
-------------------------

All downstream components only see the ``PITContext`` snapshot; data beyond
the rebalance date can never enter the decision path.  Seven PIT rules
(PLAN §7.3) are enforced at the SQL level and test-audited in
``test/test_engine/test_data_loader_pit.py``.

Reproducibility contract
-------------------------

Every backtest result carries:

1. ``config_hash`` — SHA-256-derived prefix of the YAML config
2. ``data_snapshot_sha256`` — hash of CW1 DB's current table counts + max dates
3. ``git_sha`` — current commit of the CW2 repo
4. ``seed`` — numpy/random seed (default 42)

These four values are sufficient to reproduce any number in the report.
