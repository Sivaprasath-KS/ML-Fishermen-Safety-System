"""Train on 2019–2023, select on 2024, and evaluate once on 2025."""
import json
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import xarray as xr
from sklearn.ensemble import RandomForestRegressor
from xgboost import XGBRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from threadpoolctl import threadpool_limits

from ml.features import FEATURES, features

ROOT = Path(__file__).resolve().parents[1]


def prepare():
    frames, audit = [], []
    for year in range(2019, 2026):
        folder = ROOT / "DataSet" / str(year)
        with xr.open_dataset(folder / "data_stream-wave_stepType-instant.nc") as waves, xr.open_dataset(folder / "data_stream-oper_stepType-instant.nc") as weather:
            # Wind grid is finer; nearest selection lands exactly on the wave grid here.
            wind = np.hypot(weather.u10, weather.v10).sel(latitude=waves.latitude, longitude=waves.longitude, method="nearest")
            ds = xr.Dataset({"wave": waves.swh, "wind": wind})
            frame = ds.to_dataframe().reset_index()[["valid_time", "latitude", "longitude", "wave", "wind"]]
            audit.append({"year": year, "grid_rows": len(frame), "missing_wave_rows": int(frame.wave.isna().sum()), "times": int(waves.sizes["valid_time"])})
            frames.append(frame)
    df = pd.concat(frames).sort_values(["latitude", "longitude", "valid_time"])
    group = df.groupby(["latitude", "longitude"], sort=False)
    df["lag_wave"] = group.wave.shift(1)
    df["lag_wind"] = group.wind.shift(1)
    df["target"] = group.wave.shift(-1)
    df["target_wind"] = group.wind.shift(-1)
    df["target_time"] = group.valid_time.shift(-1)
    df["lag_time"] = group.valid_time.shift(1)
    valid = ((df.valid_time - df.lag_time) == pd.Timedelta(hours=6)) & ((df.target_time - df.valid_time) == pd.Timedelta(hours=6))
    df = df[valid].dropna().copy()
    df = df[(df.wave >= 0) & (df.target >= 0) & (df.wind >= 0)]
    X = features(df.wave, df.lag_wave, df.wind, df.lag_wind, df.latitude, df.longitude, df.valid_time.dt.dayofyear, df.valid_time.dt.hour)
    # Both issue time and target time must be in the same split (purges boundary targets).
    train = (df.target_time.dt.year <= 2023).to_numpy()
    val = ((df.valid_time.dt.year == 2024) & (df.target_time.dt.year == 2024)).to_numpy()
    test = ((df.valid_time.dt.year == 2025) & (df.target_time.dt.year == 2025)).to_numpy()
    return df, X, train, val, test, audit


def scores(y, pred):
    return {"mae_m": float(mean_absolute_error(y, pred)), "rmse_m": float(np.sqrt(mean_squared_error(y, pred))), "r2": float(r2_score(y, pred))}


def main():
    out = ROOT / "artifacts"
    out.mkdir(exist_ok=True)
    df, X, train, val, test, audit = prepare()
    y = df.target.to_numpy()
    candidates, models = [], {}
    best, best_mae = None, float("inf")
    with threadpool_limits(limits=4):
        for name, model in {
            "random_forest": RandomForestRegressor(n_estimators=120, max_depth=18, min_samples_leaf=5, max_features=0.8, n_jobs=4, random_state=42),
            "xgboost": XGBRegressor(n_estimators=300, max_depth=6, learning_rate=0.05, subsample=0.85, colsample_bytree=0.9, reg_lambda=2, tree_method="hist", n_jobs=4, random_state=42),
        }.items():
            print(f"Training {name} wave regressor…", flush=True)
            start = time.perf_counter()
            model.fit(X[train], y[train])
            pred = np.maximum(0, model.predict(X[val]))
            mae = mean_absolute_error(y[val], pred)
            q = float(np.quantile(np.abs(y[val] - pred), 0.9, method="higher"))
            models[name] = {"model": model, "residual_q": q}
            candidates.append({"name": name, "validation_mae_m": float(mae), "training_seconds": time.perf_counter() - start})
            if mae < best_mae:
                best, best_mae, best_name = model, mae, name
        # Selection is complete before touching the test targets.
        for candidate in candidates:
            m = models[candidate["name"]]
            candidate["test_metrics"] = scores(y[test], np.maximum(0, m["model"].predict(X[test])))
        val_pred = np.maximum(0, best.predict(X[val]))
        # Empirical temporal holdout residual interval; not a safety guarantee.
        residual_q = float(np.quantile(np.abs(y[val] - val_pred), 0.9, method="higher"))
        t0 = time.perf_counter()
        test_pred = np.maximum(0, best.predict(X[test]))
        elapsed = time.perf_counter() - t0
        idx = np.random.default_rng(42).choice(np.flatnonzero(val), min(5000, int(val.sum())), replace=False)
        imp = permutation_importance(best, X[idx], y[idx], scoring="neg_mean_absolute_error", n_repeats=3, random_state=42)
    baseline = scores(y[test], df.wave.to_numpy()[test])
    model_scores = scores(y[test], test_pred)
    report = {
        "target": "Significant wave height 6 hours ahead", "training_years": "2019–2023", "validation_year": 2024, "test_year": 2025,
        "samples": {"train": int(train.sum()), "validation": int(val.sum()), "test": int(test.sum())},
        "coverage": {"south": 7.0, "north": 14.0, "west": 77.0, "east": 81.0},
        "cadence_hours": 6, "wave_grid_degrees": 0.5, "weather_grid_degrees": 0.25,
        "model": best_name, "selected_regressor": best_name, "candidates": candidates,
        "test_metrics": model_scores, "persistence_baseline": baseline,
        "mae_improvement_percent": 100 * (baseline["mae_m"] - model_scores["mae_m"]) / baseline["mae_m"],
        "interval": {"method": "2024 absolute residual 90th percentile", "half_width_m": residual_q,
                     "test_coverage": float(np.mean(np.abs(y[test] - test_pred) <= residual_q))},
        "batch_prediction_seconds": elapsed, "prediction_ms_per_row": elapsed / int(test.sum()) * 1000,
        "permutation_importance_mae_m": dict(zip(FEATURES, imp.importances_mean.tolist())),
        "audit": audit,
        "limitations": ["Retrospective historical-analysis evaluation, not prospective live forecast validation.", "Live operational-model inputs differ from historical analyses; accuracy and interval coverage can shift.", "Coarse wave grid cannot resolve harbour conditions or small coastal hazards.", "Risk scores are transparent heuristics, not learned accident probabilities.", "No stakeholder field evaluation has been conducted."],
    }
    # Deploy the trained model without refitting on validation/test, preserving interval calibration.
    joblib.dump({"model": best, "residual_q": residual_q, "features": FEATURES, "regressors": models, "selected_regressor": best_name}, out / "wave_model.joblib", compress=3)
    (out / "evaluation.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report[k] for k in ["samples", "test_metrics", "persistence_baseline", "mae_improvement_percent"]}, indent=2))


if __name__ == "__main__":
    main()
