#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
if [ ! -f artifacts/wave_model.joblib ]; then
  .venv/bin/python -m ml.train
fi
if [ ! -f artifacts/risk_model.joblib ]; then
  .venv/bin/python -m ml.train_classifiers
fi
exec .venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8000}"
