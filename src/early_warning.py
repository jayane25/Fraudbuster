"""Early warning: replay every laundering case transaction by transaction and record when Fraudbusters first flags it.
Answers the question rule engines never ask: how much of the scheme had already happened when we raised the alarm?
CLI:  python src/early_warning.py   -> data/early_warning.csv
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import aml  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def replay_case(df: pd.DataFrame, case: pd.DataFrame) -> dict:
    case = case.sort_values("ts")
    accts = set(case.src) | set(case.dst)
    lo, hi = case.ts.min() - pd.Timedelta(days=4), case.ts.max() + pd.Timedelta(hours=1)
    local = df[((df.src.isin(accts)) | (df.dst.isin(accts))) & (df.ts >= lo) & (df.ts <= hi)]
    case_tx = set(case.tx_id)
    n = len(case)
    caught = None
    for k in range(2, n + 1):
        cutoff = case.ts.iloc[k - 1]
        prefix = local[local.ts <= cutoff]
        alerts = aml.detect_all(prefix)
        if any(len(case_tx & set(a.tx_ids)) >= 2 for a in alerts):
            caught = k
            break
    end = case.ts.iloc[-1]
    row = dict(case_id=case.case_id.iloc[0], typology=case.typology.iloc[0], steps=n,
               amount=float(case.amount.sum()), caught_step=caught)
    if caught:
        t = case.ts.iloc[caught - 1]
        row.update(pct_complete=round(100 * caught / n, 1), hours_early=round((end - t).total_seconds() / 3600, 1),
                   money_still_moving=float(case.amount.iloc[caught:].sum()))
    else:
        row.update(pct_complete=None, hours_early=None, money_still_moving=0.0)
    return row


def compute(df: pd.DataFrame) -> pd.DataFrame:
    bad = df[df.is_laundering == 1]
    rows = [replay_case(df, g) for _, g in bad.groupby("case_id")]
    return pd.DataFrame(rows)


def summary(ew: pd.DataFrame) -> pd.DataFrame:
    got = ew.dropna(subset=["caught_step"])
    return got.groupby("typology").agg(
        cases=("case_id", "count"),
        median_pct_complete=("pct_complete", "median"),
        median_hours_early=("hours_early", "median"),
    ).round(1)


def main():
    df = aml.load_transactions(os.path.join(ROOT, "data", "transactions.csv"))
    ew = compute(df)
    ew.to_csv(os.path.join(ROOT, "data", "early_warning.csv"), index=False)
    print(f"{ew.caught_step.notna().sum()} of {len(ew)} cases flagged during replay")
    print(summary(ew).to_string())


if __name__ == "__main__":
    main()
