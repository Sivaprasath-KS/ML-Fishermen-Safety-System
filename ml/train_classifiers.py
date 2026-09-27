"""Forecast +6h derived sea-risk labels using Random Forest and XGBoost."""
import json
import time
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score, confusion_matrix, classification_report
from sklearn.utils.class_weight import compute_sample_weight
from threadpoolctl import threadpool_limits
from xgboost import XGBClassifier

from ml.train import prepare
from ml.features import FEATURES
from ml.risk_policy import LABELS, POLICY, label_ids

OUT = Path(__file__).resolve().parents[1] / "artifacts"


def metrics(y, pred):
    return {"accuracy": float(accuracy_score(y, pred)), "balanced_accuracy": float(balanced_accuracy_score(y, pred)),
            "macro_f1": float(f1_score(y, pred, labels=[0,1,2], average="macro", zero_division=0)),
            "confusion_matrix": confusion_matrix(y, pred, labels=[0,1,2]).tolist(),
            "per_class": classification_report(y, pred, labels=[0,1,2], target_names=LABELS, output_dict=True, zero_division=0)}


def main():
    df, X, train, val, test, audit = prepare()
    y = label_ids(df.target, df.target_wind)
    current_labels = label_ids(df.wave, df.wind)
    models = {
        "random_forest": RandomForestClassifier(n_estimators=140, max_depth=18, min_samples_leaf=8, max_features=0.8, class_weight="balanced_subsample", n_jobs=4, random_state=42),
        "xgboost": XGBClassifier(n_estimators=300, max_depth=6, learning_rate=0.05, subsample=0.85, colsample_bytree=0.9, reg_lambda=2, objective="multi:softprob", num_class=3, eval_metric="mlogloss", tree_method="hist", n_jobs=4, random_state=42),
    }
    reports = {}
    with threadpool_limits(limits=4):
        for name, model in models.items():
            print(f"Training {name} +6h risk classifier…", flush=True)
            start = time.perf_counter()
            kwargs = {"sample_weight": compute_sample_weight("balanced", y[train])} if name == "xgboost" else {}
            model.fit(X[train], y[train], **kwargs)
            reports[name] = {"validation": metrics(y[val], model.predict(X[val])), "training_seconds": time.perf_counter() - start}
        selected = max(reports, key=lambda name: (reports[name]["validation"]["macro_f1"], reports[name]["validation"]["per_class"]["danger"]["recall"]))
        test_predictions = []
        for name, model in models.items():
            start = time.perf_counter()
            predicted = model.predict(X[test])
            test_predictions.append(predicted)
            reports[name]["test_prediction_seconds"] = time.perf_counter() - start
            reports[name]["test"] = metrics(y[test], predicted)
        idx = np.random.default_rng(42).choice(np.flatnonzero(val), min(3000, int(val.sum())), replace=False)
        importance = permutation_importance(models[selected], X[idx], y[idx], scoring="f1_macro", n_repeats=3, random_state=42)
    counts = {name: {label: int(np.sum(y[mask] == i)) for i,label in enumerate(LABELS)} for name,mask in [("train",train),("validation",val),("test",test)]}
    report = {"task": "Forecast project-derived sea-risk class six hours ahead", "selected_classifier": selected,
              "selection_rule": "Highest 2024 macro F1; danger recall breaks ties. Test year never selects the model.",
              "training_years": "2019–2023", "validation_year": 2024, "test_year": 2025,
              "policy": POLICY, "labels": LABELS, "class_counts": counts, "models": reports,
              "persistence_baseline": metrics(y[test], current_labels[test]),
              "conservative_ensemble": metrics(y[test], np.maximum.reduce(test_predictions)),
              "permutation_importance_macro_f1": dict(zip(FEATURES, importance.importances_mean.tolist())),
              "limitations": ["Targets are derived from future wave/wind using the documented project policy, not recorded government warnings or accident outcomes.",
                              "Scores measure six-hour prediction of those derived labels, not certified safety accuracy.",
                              "Class-weighted model probabilities are uncalibrated scores, not accident probabilities.",
                              "Live inputs are operational forecasts; historical inputs are ECMWF analyses. Prospective validation remains necessary."]}
    exported = df[["valid_time","target_time","latitude","longitude","wave","wind","lag_wave","lag_wind","target","target_wind"]].copy()
    exported["label"] = np.asarray(LABELS)[y]
    exported["split"] = np.select([train,val,test],["train","validation","test"],default="purged_boundary")
    exported["label_origin"] = POLICY["label_origin"]
    exported["policy_version"] = POLICY["version"]
    exported.to_csv(OUT / "labeled_dataset.csv.gz", index=False, compression="gzip")
    (OUT / "label_policy.json").write_text(json.dumps(POLICY, indent=2))
    (OUT / "classification_evaluation.json").write_text(json.dumps(report, indent=2))
    joblib.dump({"models": models, "selected_classifier": selected, "features": FEATURES, "policy": POLICY}, OUT / "risk_model.joblib", compress=3)
    print(json.dumps({"selected":selected,"class_counts":counts,"test":{name:{k:v for k,v in r["test"].items() if k in ["accuracy","macro_f1","balanced_accuracy"]} for name,r in reports.items()}},indent=2), flush=True)


if __name__ == "__main__":
    main()
