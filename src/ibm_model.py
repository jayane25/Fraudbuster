"""FlowTrace IBM profile: graph features + a learned scorer, evaluated on later days than it was trained on.

Why: the hand tuned typology rules in aml.py fit our synthetic data but not IBM's real format data, where
hops are days apart, more than half the laundering follows no named pattern, and the payment rail matters
(about 87% of IBM laundering is ACH versus about 12% of all traffic).

Split by time (no shuffling): train 1 to 6 Sep, choose the alert threshold on 7 Sep, report on 8 to 10 Sep.
Days after 10 Sep are dropped: the file tails off there and the few remaining rows are about half laundering,
an artefact of how the simulation ends.

CLI:  python src/ibm_model.py     -> data/ibm_model_results.json
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score

sys.path.insert(0, os.path.dirname(__file__))
import ibm  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRAIN_END, VAL_END, TEST_END = "2022-09-07", "2022-09-08", "2022-09-11"
REPORT_LIMIT = 10_000  # same threshold baseline idea as the synthetic run


def load() -> pd.DataFrame:
    raw = pd.read_csv(os.path.join(ROOT, "data", "HI-Small_Trans.csv"), header=0, names=ibm.COLS, dtype=str)
    raw["ts"] = pd.to_datetime(raw.ts, format="%Y/%m/%d %H:%M")
    raw = raw[raw.ts < TEST_END].copy()
    raw["src"] = raw.from_bank.str.strip() + "_" + raw.from_acct.str.strip()
    raw["dst"] = raw.to_bank.str.strip() + "_" + raw.to_acct.str.strip()
    raw["amount"] = raw.amt_paid.astype(float)
    raw["y"] = raw.is_laundering.astype(int)
    return raw[raw.src != raw.dst].sort_values("ts").reset_index(drop=True)


def features(d: pd.DataFrame) -> pd.DataFrame:
    """Per transaction features built from the transaction graph. No labels are used here."""
    X = pd.DataFrame(index=d.index)
    X["log_amt"] = np.log1p(d.amount)
    X["round_100"] = (d.amount % 100 == 0).astype(int)
    X["hour"] = d.ts.dt.hour
    X["same_bank"] = (d.from_bank == d.to_bank).astype(int)
    X["fmt"] = d.fmt.astype("category").cat.codes
    X["cur"] = d.cur_paid.astype("category").cat.codes
    ach = (d.fmt == "ACH").astype(int)

    out_n = d.groupby("src").size(); in_n = d.groupby("dst").size()
    out_u = d.groupby("src").dst.nunique(); in_u = d.groupby("dst").src.nunique()
    out_ach = ach.groupby(d.src).mean(); in_ach = ach.groupby(d.dst).mean()
    out_amt = d.groupby("src").amount.median(); in_amt = d.groupby("dst").amount.median()
    banks_out = d.groupby("src").to_bank.nunique()
    for side, acct in (("s", d.src), ("d", d.dst)):
        X[f"{side}_out_n"] = acct.map(out_n).fillna(0).values
        X[f"{side}_in_n"] = acct.map(in_n).fillna(0).values
        X[f"{side}_out_u"] = acct.map(out_u).fillna(0).values      # fan out breadth
        X[f"{side}_in_u"] = acct.map(in_u).fillna(0).values        # fan in breadth
        X[f"{side}_out_ach"] = acct.map(out_ach).fillna(0).values
        X[f"{side}_in_ach"] = acct.map(in_ach).fillna(0).values
        X[f"{side}_out_banks"] = acct.map(banks_out).fillna(0).values
        X[f"{side}_amt_vs_out"] = (d.amount / acct.map(out_amt).fillna(1).replace(0, 1)).values
        X[f"{side}_amt_vs_in"] = (d.amount / acct.map(in_amt).fillna(1).replace(0, 1)).values
    X["d_passthru"] = X.d_out_n / (X.d_in_n + 1)   # relay: receiver forwards what it gets
    X["s_passthru"] = X.s_out_n / (X.s_in_n + 1)
    pair = d.src + ">" + d.dst
    X["pair_n"] = pair.map(pair.value_counts()).values
    X["d_next_u"] = d.dst.map(out_u).fillna(0).values  # second hop breadth
    X["s_prev_u"] = d.src.map(in_u).fillna(0).values
    return X


def prf(y, pred):
    tp = int(((pred == 1) & (y == 1)).sum()); fp = int(((pred == 1) & (y == 0)).sum()); fn = int(((pred == 0) & (y == 1)).sum())
    p = tp / max(tp + fp, 1); r = tp / max(tp + fn, 1)
    return dict(precision=round(p, 4), recall=round(r, 4), f1=round(2 * p * r / max(p + r, 1e-9), 4), tp=tp, fp=fp, fn=fn)


def main():
    t0 = time.time()
    d = load()
    y = d.y.values
    tr = (d.ts < TRAIN_END).values
    va = ((d.ts >= TRAIN_END) & (d.ts < VAL_END)).values
    te = (d.ts >= VAL_END).values
    # strict: each period's graph features come only from that period's own transactions (no future leakage)
    X = pd.concat([features(d[m]) for m in (tr, va, te)]).loc[d.index]
    print(f"rows {len(d):,}  train {tr.sum():,}/{y[tr].sum()}  val {va.sum():,}/{y[va].sum()}  test {te.sum():,}/{y[te].sum()}", flush=True)

    clf = HistGradientBoostingClassifier(max_iter=400, learning_rate=0.05, max_leaf_nodes=63, l2_regularization=1.0,
                                         class_weight={0: 1, 1: 20},
                                         categorical_features=[X.columns.get_loc(c) for c in ("fmt", "cur")],
                                         random_state=7)
    clf.fit(X[tr], y[tr])
    pv = clf.predict_proba(X[va])[:, 1]
    cands = np.unique(np.quantile(pv, np.linspace(0.9, 0.99999, 400)))
    best = max(cands, key=lambda th: prf(y[va], (pv >= th).astype(int))["f1"])
    pt = clf.predict_proba(X[te])[:, 1]
    model = prf(y[te], (pt >= best).astype(int))
    model["pr_auc"] = round(float(average_precision_score(y[te], pt)), 4)

    dt = d[te]
    busy = dt.groupby([dt.src, dt.ts.dt.date]).amount.transform("size") >= 10
    baseline = prf(y[te], ((dt.amount >= REPORT_LIMIT) | busy).astype(int).values)
    base_rate = float(y[te].mean())

    res = dict(
        method="Graph features + gradient boosted scorer (FlowTrace IBM profile)",
        split="Train 1 to 6 Sep 2022, threshold chosen on 7 Sep, tested on 8 to 10 Sep 2022",
        test_transactions=int(te.sum()), test_laundering=int(y[te].sum()), base_rate=round(base_rate, 6),
        threshold=round(float(best), 4), seconds=round(time.time() - t0, 1),
        metrics=[dict(system="Threshold baseline", **baseline), dict(system="FlowTrace IBM profile", **model)],
        lift_vs_random=round(model["precision"] / base_rate, 1),
        flagged=int(model["tp"] + model["fp"]), baseline_flagged=int(baseline["tp"] + baseline["fp"]),
    )
    with open(os.path.join(ROOT, "data", "ibm_model_results.json"), "w", encoding="utf8") as f:
        json.dump(res, f, indent=2)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
