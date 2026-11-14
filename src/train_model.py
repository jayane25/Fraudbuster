"""Train and test the Fraudbusters ML model, and combine it with the rule based detectors (hybrid).

Steps
  1. Split by DATE, never shuffled (a bank trains on the past and is used on the future):
       train  = 1 Jan to 28 Feb 2026   -> data/train.csv
       test   = 1 Mar to 31 Mar 2026   -> data/test.csv
     The last 2 weeks of train (15 to 28 Feb) are held out as VALIDATION to choose the alert threshold.
  2. Features (src/features.py). Train features are built from train data only. Test features may use the
     past (Jan and Feb) because a bank always has that history; test labels are never used.
  3. Two Random Forest models (300 decision trees that vote):
       ML only : graph + KYC features
       Hybrid  : the same + what our rule detectors (aml.py) and the threshold rule said
  4. Compare four systems on the test month: threshold rules, graph rules, ML only, hybrid.
     Also: how often each system raises a false alarm on GENUINE look-alikes (donations, payroll, ...).
  5. Save models/fraudbusters_model.joblib (the hybrid) and data/ml_results.json.

CLI:  python src/train_model.py
"""
from __future__ import annotations

import json
import os
import sys
import time

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score, precision_recall_curve

sys.path.insert(0, os.path.dirname(__file__))
import aml  # noqa: E402
import features as F  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
MODEL_PATH = os.path.join(ROOT, "models", "fraudbusters_model.joblib")
TRAIN_END = pd.Timestamp("2026-03-01")      # test starts here
VAL_START = pd.Timestamp("2026-02-15")      # validation = last 2 weeks of train
GENUINE_LOOKALIKES = ["donation_drive", "relief_redistribution", "payroll", "chit_fund", "merchant_sales",
                      "own_account_sweep"]


def make_model() -> RandomForestClassifier:
    return RandomForestClassifier(n_estimators=300, min_samples_leaf=3, max_depth=18, max_features="sqrt",
                                  class_weight="balanced_subsample", n_jobs=-1, random_state=7)


def best_threshold(y, p) -> float:
    """Probability cut off with the best F1 on the validation weeks."""
    prec, rec, thr = precision_recall_curve(y, p)
    f1 = 2 * prec * rec / np.maximum(prec + rec, 1e-9)
    return float(thr[int(np.argmax(f1[:-1]))])


def prf(y: np.ndarray, pred: np.ndarray) -> dict:
    tp = int((pred & y).sum()); fp = int((pred & ~y).sum()); fn = int((~pred & y).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0,
            "tp": tp, "fp": fp, "fn": fn, "flagged": tp + fp}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    t0 = time.time()
    df = aml.load_transactions(os.path.join(DATA, "transactions.csv"))
    acc = F.load_accounts(os.path.join(DATA, "accounts.csv"))

    # 1. split by date and save the two files
    is_test = (df.ts >= TRAIN_END).to_numpy()
    train, test = df[~is_test], df[is_test]
    train.to_csv(os.path.join(DATA, "train.csv"), index=False)
    test.to_csv(os.path.join(DATA, "test.csv"), index=False)
    print(f"train {len(train):,} tx ({train.is_laundering.sum():,} laundering)   "
          f"test {len(test):,} tx ({test.is_laundering.sum():,} laundering)", flush=True)

    # 2. features: train from train only; test from all history up to the end of March
    X_train = F.build(train, acc)
    alerts_all = aml.detect_all(df)
    X_test = F.build(df, acc, alerts_all)[is_test]
    y_train = train.is_laundering.to_numpy(bool)
    y_test = test.is_laundering.to_numpy(bool)
    fit = (train.ts < VAL_START).to_numpy()

    # 3. train ML only and hybrid; pick each threshold on validation, then refit on all of train
    models, thresholds, probs = {}, {}, {}
    for name, cols in (("ML only", F.GRAPH_FEATURES), ("Hybrid", F.HYBRID_FEATURES)):
        m = make_model().fit(X_train.loc[fit, cols], y_train[fit])
        thresholds[name] = best_threshold(y_train[~fit], m.predict_proba(X_train.loc[~fit, cols])[:, 1])
        models[name] = make_model().fit(X_train[cols], y_train)
        probs[name] = models[name].predict_proba(X_test[cols])[:, 1]
        print(f"{name}: threshold {thresholds[name]:.2f}", flush=True)

    # 4. evaluate four systems on the test month
    flagged_rules = {t for a in alerts_all for t in a.tx_ids}
    preds = {
        "Threshold rules": aml.threshold_baseline(df)[is_test].to_numpy(bool),
        "Graph rules (aml.py)": test.tx_id.isin(flagged_rules).to_numpy(bool),
        "ML only": probs["ML only"] >= thresholds["ML only"],
        "Hybrid (rules + ML)": probs["Hybrid"] >= thresholds["Hybrid"],
    }
    metrics = [{"system": k, **prf(y_test, v)} for k, v in preds.items()]
    for k in ("ML only", "Hybrid"):
        name = "Hybrid (rules + ML)" if k == "Hybrid" else k
        next(m for m in metrics if m["system"] == name)["pr_auc"] = round(float(average_precision_score(y_test, probs[k])), 3)

    scen = test.scenario.to_numpy()
    by_scenario = []
    for s in sorted(set(scen)):
        mask = scen == s
        by_scenario.append({"scenario": s, "laundering": bool(y_test[mask][0]), "transactions": int(mask.sum()),
                            **{k: round(float(v[mask].mean()), 3) for k, v in preds.items()}})

    cases = []
    tcase = test.case_id.to_numpy()
    for s in sorted(set(scen) - {"normal"}):
        ids = sorted({c for c, sc in zip(tcase, scen) if sc == s and isinstance(c, str)})
        row = {"scenario": s, "laundering": s not in GENUINE_LOOKALIKES, "cases": len(ids)}
        for k, v in preds.items():
            hit = [v[(tcase == c) & (scen == s)].mean() >= 0.5 for c in ids]
            row[k] = int(sum(hit))
        cases.append(row)

    look = np.isin(scen, GENUINE_LOOKALIKES)
    false_alarms = {k: {"flagged": int(v[look].sum()), "of": int(look.sum()), "rate": round(float(v[look].mean()), 3)}
                    for k, v in preds.items()}

    hyb = models["Hybrid"]
    imp = sorted(zip(F.HYBRID_FEATURES, hyb.feature_importances_), key=lambda x: -x[1])
    pred_h = preds["Hybrid (rules + ML)"]
    res = {
        "method": "Random Forest (300 trees) on graph + KYC features, combined with rule detector outputs (hybrid)",
        "split": {"train": "1 Jan to 28 Feb 2026", "validation": "15 to 28 Feb 2026 (part of train, picks the threshold)",
                  "test": "1 to 31 Mar 2026",
                  "train_transactions": int(len(train)), "train_laundering": int(y_train.sum()),
                  "test_transactions": int(len(test)), "test_laundering": int(y_test.sum())},
        "thresholds": {k: round(v, 3) for k, v in thresholds.items()},
        "metrics": metrics,
        "by_scenario": by_scenario,
        "cases": cases,
        "false_alarms_on_genuine_lookalikes": false_alarms,
        "confusion_hybrid": {"tp": int((pred_h & y_test).sum()), "fp": int((pred_h & ~y_test).sum()),
                             "fn": int((~pred_h & y_test).sum()), "tn": int((~pred_h & ~y_test).sum())},
        "importance": [{"feature": f, "label": F.LABELS[f], "importance": round(float(v), 4)} for f, v in imp],
        "seconds": round(time.time() - t0, 1),
    }
    with open(os.path.join(DATA, "ml_results.json"), "w", encoding="utf8") as f:
        json.dump(res, f, indent=2)
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    joblib.dump({"model": hyb, "features": F.HYBRID_FEATURES, "threshold": thresholds["Hybrid"],
                 "trained": "1 Jan to 28 Feb 2026", "sklearn": sklearn.__version__}, MODEL_PATH, compress=3)

    print(pd.DataFrame(metrics).set_index("system").to_string())
    print("\nShare of transactions flagged, by scenario (test month):")
    print(pd.DataFrame(by_scenario).set_index("scenario").to_string())
    print("\nFalse alarms on genuine look-alikes:", {k: v["rate"] for k, v in false_alarms.items()})
    print(f"\nModel saved to {MODEL_PATH} ({os.path.getsize(MODEL_PATH) / 1e6:.1f} MB) in {res['seconds']} s")


if __name__ == "__main__":
    main()
