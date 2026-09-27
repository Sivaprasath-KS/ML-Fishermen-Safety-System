from datetime import datetime, timedelta
from pathlib import Path
import json

import joblib
import numpy as np
from threadpoolctl import threadpool_limits

from ml.features import features
from ml.risk_policy import LABELS, POLICY, describe, label_ids

ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"


class Predictor:
    def __init__(self):
        self.bundle = None
        self.risk_bundle = None

    def predict(self, row, model_name="auto"):
        path = ARTIFACTS / "wave_model.joblib"
        if self.bundle is None:
            if not path.exists():
                return {"available": False, "reason": "Train the model using python -m ml.train."}
            self.bundle = joblib.load(path)
        now = row["current"]
        issue = datetime.fromisoformat(now["time"])
        lag_time = (issue - timedelta(hours=6)).isoformat()
        lag = next((h for h in row["hourly"] if h["time"] == lag_time), None)
        if lag is None:
            return {"available": False, "reason": "Six-hour input history is unavailable."}
        if not (7 <= row["latitude"] <= 14 and 77 <= row["longitude"] <= 81):
            return {"available": False, "reason": "Outside the model's training region."}
        X = features(now["wave_m"], lag["wave_m"], now["wind_ms"], lag["wind_ms"], row["latitude"], row["longitude"], issue.timetuple().tm_yday, issue.hour)
        regressors = self.bundle.get("regressors", {"legacy": self.bundle})
        selected = self.bundle.get("selected_regressor", "legacy") if model_name == "auto" else model_name
        if selected not in regressors:
            return {"available": False, "reason": "Requested model has not been trained."}
        comparisons = {}
        with threadpool_limits(limits=2):
            for name, bundle in regressors.items():
                wave = float(np.maximum(0, bundle["model"].predict(X)[0]))
                q = bundle["residual_q"]
                comparisons[name] = {"wave_m": wave, "lower_m": max(0, wave - q), "upper_m": wave + q}
        risk_path = ARTIFACTS / "risk_model.joblib"
        if self.risk_bundle is None and risk_path.exists():
            self.risk_bundle = joblib.load(risk_path)
        risk_prediction = {"available": False, "reason": "Risk classifiers have not been trained."}
        if self.risk_bundle:
            risk_selected = self.risk_bundle["selected_classifier"] if model_name == "auto" else model_name
            classifiers = self.risk_bundle["models"]
            if risk_selected in classifiers:
                classified = {}
                with threadpool_limits(limits=2):
                    for name, model in classifiers.items():
                        probs = model.predict_proba(X)[0]
                        index = int(model.classes_[int(np.argmax(probs))])
                        classified[name] = {"label": LABELS[index], "label_id": index,
                                            "class_scores": {LABELS[int(c)]: float(p) for c, p in zip(model.classes_, probs)}}
                risk_prediction = {"available": True, "selected_model": risk_selected, **classified[risk_selected], "models": classified,
                                   "conservative_label_id": max(r["label_id"] for r in classified.values()),
                                   "score_note": "Class-weighted, uncalibrated model scores; not accident probabilities.", "policy": POLICY}
        return {"available": True, **comparisons[selected], "selected_model": selected, "models": comparisons,
                "conservative_upper_m": max(r["upper_m"] for r in comparisons.values()), "risk_prediction": risk_prediction,
                "current_risk": describe(now["wave_m"], now["wind_ms"]),
                "issue_time": now["time"], "target_time": (issue + timedelta(hours=6)).isoformat(),
                "method": "Random Forest and XGBoost using live model-estimate inputs",
                "interval_note": "90th-percentile historical residual band; live coverage is unverified."}

    def report(self):
        path = ARTIFACTS / "evaluation.json"
        report = json.loads(path.read_text()) if path.exists() else {"available": False}
        risk_path = ARTIFACTS / "classification_evaluation.json"
        report["classification"] = json.loads(risk_path.read_text()) if risk_path.exists() else {"available": False}
        return report


predictor = Predictor()
