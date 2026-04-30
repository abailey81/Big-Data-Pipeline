#!/usr/bin/env bash
# CW2 one-command marker run.  Handles every known gotcha (PATH-shadowing,
# kernel mismatch, Poetry venv-config) so the marker can verify the
# submission with a single command.
#
# Usage from coursework_two/:
#     ./run_marker.sh           # tearsheet only (no DB needed, ~3 min)
#     ./run_marker.sh full      # full pipeline (requires CW1 DB up, ~40 min)
#     ./run_marker.sh fast      # full minus sensitivity/ablation/cost-stress (~8 min, needs DB)

set -e
cd "$(dirname "$0")"
MODE="${1:-tearsheet}"

echo "============================================================"
echo "CW2 marker run — mode: $MODE"
echo "============================================================"

# 1. Force in-project venv (avoids the "Cannot install scipy" trap on macOS Homebrew Python)
echo
echo "[1/5] Configuring Poetry to use an in-project venv..."
poetry config virtualenvs.in-project true

# 2. Install dependencies.  poetry install can emit "Cannot install scipy" warnings
# on Homebrew Python because of stale dist-info; we tolerate those and verify the
# install via a direct import probe afterwards.
echo
echo "[2/5] Installing dependencies (~60 s on a warm cache)..."
poetry install 2>&1 | tail -3 || true
if ! poetry run python -c "import numpy, scipy, pandas, statsmodels, pandas_market_calendars" 2>/dev/null; then
    echo "    ERROR: a required package failed to import after poetry install."
    echo "    Try:  poetry config virtualenvs.in-project true && poetry env remove --all && poetry install"
    exit 1
fi
echo "    core deps importable: numpy, scipy, pandas, statsmodels, pandas_market_calendars"

# 3. Register the kernel so the notebook executes against this venv
echo
echo "[3/5] Registering Jupyter kernel cw2-poetry..."
poetry run python -m ipykernel install --user --name=cw2-poetry --display-name="CW2 (poetry venv)" >/dev/null
echo "    kernel cw2-poetry registered."

# 4. Run the requested pipeline mode
case "$MODE" in
    full)
        echo
        echo "[4/5] Running full engine pipeline (~40 min)..."
        poetry run python Main.py --mode full        --start 2023-07-01 --end 2026-03-31
        poetry run python Main.py --mode sensitivity --start 2023-07-01 --end 2026-03-31
        poetry run python Main.py --mode ablation    --start 2023-07-01 --end 2026-03-31
        poetry run python Main.py --mode stress
        poetry run python Main.py --mode monte_carlo
        poetry run python Main.py --mode regime_perf
        poetry run python ../analysis/run_attribution_ls.py
        poetry run python ../analysis/run_inference_ls.py
        poetry run python ../analysis/run_cost_stress_ls_v2.py
        poetry run python -m jupyter nbconvert --to notebook --execute \
            notebooks/CW2_Tearsheet.ipynb --inplace \
            --ExecutePreprocessor.kernel_name=cw2-poetry \
            --ExecutePreprocessor.timeout=900
        poetry run python -m jupyter nbconvert --to html notebooks/CW2_Tearsheet.ipynb
        ;;
    fast)
        echo
        echo "[4/5] Running fast pipeline (skipping sensitivity/ablation/cost-stress, ~8 min)..."
        poetry run python Main.py --mode full --start 2023-07-01 --end 2026-03-31
        poetry run python Main.py --mode stress
        poetry run python Main.py --mode monte_carlo
        poetry run python Main.py --mode regime_perf
        poetry run python ../analysis/run_attribution_ls.py
        poetry run python ../analysis/run_inference_ls.py
        poetry run python -m jupyter nbconvert --to notebook --execute \
            notebooks/CW2_Tearsheet.ipynb --inplace \
            --ExecutePreprocessor.kernel_name=cw2-poetry \
            --ExecutePreprocessor.timeout=900
        poetry run python -m jupyter nbconvert --to html notebooks/CW2_Tearsheet.ipynb
        ;;
    tearsheet)
        echo
        echo "[4/5] Rendering tearsheet from committed parquets (no DB needed, ~3 min)..."
        poetry run python -m jupyter nbconvert --to notebook --execute \
            notebooks/CW2_Tearsheet.ipynb --inplace \
            --ExecutePreprocessor.kernel_name=cw2-poetry \
            --ExecutePreprocessor.timeout=900
        poetry run python -m jupyter nbconvert --to html notebooks/CW2_Tearsheet.ipynb
        ;;
    *)
        echo "ERROR: unknown mode '$MODE' — use 'full', 'fast', or 'tearsheet'."
        exit 2
        ;;
esac

# 5. Verify everything is consistent
echo
echo "[5/5] Verifying artefacts..."
poetry run python scripts/verify_pipeline.py

echo
echo "============================================================"
echo "Done.  Open notebooks/CW2_Tearsheet.html in a browser to view"
echo "the rendered investment tearsheet."
echo "============================================================"
