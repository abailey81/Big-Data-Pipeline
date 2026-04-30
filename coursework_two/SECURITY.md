# Security Policy — CW2 Backtest Engine

## Supported versions

Only the current release is supported. See `CHANGELOG.md` for version history.

## Reporting a vulnerability

Although this is an academic-coursework codebase, we treat security seriously.
Please report any issue (even suspected) via:

- GitHub issue with the `security` label — for non-sensitive findings
- Direct email to the module lead (Team Kolmogorov) — for sensitive findings

Do NOT commit credentials or reproduction data that contains PII.

## Audit history

| Date | Reviewer | Finding severity | Status |
|---|---|---|---|
| 2026-04-17 | security-review agent (pass 1) | 0 HIGH · 14 MEDIUM (acceptable) · 4 LOW · 0 CVE | PASS-with-concerns → **fixed** |
| 2026-04-17 | security-review agent (pass 2) | 0 HIGH · 0 new findings (all pass-1 fixes confirmed) · 1 methodological defect in CPCV | **fixed** |

## Posture summary

- **No secrets in source control** — DB credentials env-var overridable (`POSTGRES_{HOST,PORT,USER,PASSWORD,DATABASE,SCHEMA}`); see `.env.example`.
- **No SQL injection surface** — `DatabaseConfig.schema_` passes through a strict regex validator (`engine/config.py::_IDENT_RE`). All user-controllable values use bound `:params`. `[tool.bandit] skips = ["B608"]` in `pyproject.toml` documents this approved interpolation.
- **No unsafe deserialisation** — only `yaml.safe_load` / `yaml.safe_dump`; no `pickle`, no `eval`, no `exec`, no `os.system`, no `shell=True`.
- **Resource-exhaustion bounded** — `n_workers` clamped at `2 × os.cpu_count()` in `engine/runner.py`.
- **Reproducibility seal** — content-sensitive SHA-256 over daily_prices + fundamentals payload in `engine/data_loader.py::data_snapshot_sha256` detects cell-level mutations.
- **Dependency hygiene** — 19 pinned packages, `pip-audit` confirms 0 CVEs, no typosquats.
- **Pydantic validation at every boundary** — `Config` subclasses enforce field types, ranges, and regex.
