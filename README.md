# Team Kolmogorov — Big Data in Quantitative Finance (IFTE0003)

University College London · MSc Banking and Digital Finance · 2026.

This repository contains both courseworks for the IFTE0003 module, organised
per the institutional submission template:

```
.
├── CHANGELOG.md
├── coursework_one/    Data-pipeline ETL for the 678-stock investable universe
└── coursework_two/    Multi-factor long/short backtest engine + analytics
```

## [coursework_one/](coursework_one/) — Data Pipeline (Feb 2026)

Production-grade ETL ingesting six years of structured and unstructured market
data for **678 listed companies** across US, UK, European, Canadian, and Swiss
exchanges into a triple-database architecture (PostgreSQL + MongoDB + MinIO)
with Apache Kafka event streaming.

- 11 data streams (prices, fundamentals, ratios, FX, VIX, RFR, benchmarks,
  ESG, sentiment, company static, news cascade)
- Three storage substrates: PostgreSQL `systematic_equity` schema, MongoDB
  collections for unstructured news, MinIO buckets for raw filings
- Docker-compose stack (port 5439 for Postgres, plus MongoDB, MinIO, Kafka)
- 1,221 tests with 94 % coverage
- Sphinx documentation under `coursework_one/docs/`

See [coursework_one/README.md](coursework_one/README.md) for setup,
architecture, and run instructions.

## [coursework_two/](coursework_two/) — Investment Strategy (May 2026)

Monthly-rebalanced sector-neutral, dollar-neutral long/short equity backtest
on the CW1 universe.  The implemented composite combines two factors —
**momentum (12-1)** and **value (B/P + E/P + CF/P)** — at equal 50/50 weights,
after CW1's four-factor proposal was reduced based on out-of-sample
information-coefficient evidence.

- Four portfolio-construction variants: Dynamic (VIX-regime + dispersion
  overlay), Static, Linear Thompson Sampling Bandit, Hierarchical Risk Parity
- Three-layer risk stack: 99 % Historical VaR + Moreira-Muir vol target +
  Korn-Korn-Kroisandt drawdown control
- Statistical inference: Politis-Romano circular-block bootstrap,
  Probabilistic Sharpe Ratio, Deflated Sharpe Ratio, Minimum Backtest Length
- Fama-French 5 + Carhart momentum α attribution with Newey-West HAC SEs
- 17 parquet output artefacts, 3 analysis CSVs, executed Jupyter tearsheet,
  Sphinx API site, 87 unit tests

Headline out-of-sample window: **July 2023 → February 2026** (32 months,
net of 20 bp/side).

| Variant | Ann return | Vol | Raw Sharpe | Excess Sharpe | Max DD |
|---|---:|---:|---:|---:|---:|
| Static  Net 20 bp | +17.83 % | 11.39 % | +1.505 | +1.087 | −7.86 % |
| Dynamic Net 20 bp | +16.92 % | 11.67 % | +1.404 | +0.997 | −8.64 % |

FF5 + Carhart momentum α: Dynamic +23.97 % (t = +2.353, p = 0.019), Static
+25.33 % (t = +2.563, p = 0.010) — both significant at the 5 % level.

See [coursework_two/README.md](coursework_two/README.md) for setup, the three
run paths (full from scratch / CW1-DB-already-up / tearsheet only),
troubleshooting, and reproducibility details.

## Team

| Name | Student number | Final role |
|---|---|---|
| Ryan Lin | 24038712 | Investment Product Owner, Group Leader |
| Ayudhya Vidyaningtyas | 25143187 | Investment Product Owner |
| Jianyang Zuo | 25088271 | Investment Product Owner |
| Tamer Atesyakar | 22167510 | Developer (Lead) |
| Tsz Fung Huang | 25122340 | Developer (Auditor) |
| Peixi Xiong | 25138149 | Investment Specialist |
| Moyan Yu | 25094276 | Investment Specialist |
| Xinyan Chen | 22033931 | Investment Specialist |

## License

MIT.  See [LICENSE](LICENSE).
