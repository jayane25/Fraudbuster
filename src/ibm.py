"""Benchmark FlowTrace on the public IBM AML dataset (Altman et al., 2023).
Download from Kaggle (needs a free login): ealtman2019/ibm-transactions-for-anti-money-laundering-aml
Put HI-Small_Trans.csv and HI-Small_Patterns.txt in data/.

CLI:  python src/ibm.py --days 3      -> data/ibm_results.json
The transactions file has a duplicate 'Account' header, so columns are read by position.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import aml  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLS = ["ts", "from_bank", "from_acct", "to_bank", "to_acct", "amt_recv", "cur_recv", "amt_paid", "cur_paid", "fmt", "is_laundering"]
TYPO_MAP = {"FAN-IN": "fan_in", "FAN-OUT": "fan_out", "CYCLE": "cycle", "STACK": "stack", "RANDOM": "random",
            "SCATTER-GATHER": "scatter_gather", "GATHER-SCATTER": "gather_scatter", "BIPARTITE": "bipartite"}


def _key(parts):
    return tuple(p.strip() for p in parts[:6]) + (parts[7].strip(),)  # ts, banks, accounts, amt_recv, amt_paid


def load_patterns(path: str) -> dict:
    """tx key -> IBM pattern name, from the BEGIN/END LAUNDERING ATTEMPT blocks."""
    out, current = {}, None
    with open(path, encoding="utf8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            m = re.match(r"BEGIN LAUNDERING ATTEMPT\s*-\s*([A-Z\-]+)", line)
            if m:
                current = TYPO_MAP.get(m.group(1), m.group(1).lower()); continue
            if line.startswith("END LAUNDERING ATTEMPT"):
                current = None; continue
            if current and line:
                parts = line.split(",")
                if len(parts) >= 11:
                    out[_key(parts)] = current
    return out


def load_ibm(trans_path: str, patterns_path: str | None = None, days: float | None = 3, nrows: int | None = None) -> pd.DataFrame:
    raw = pd.read_csv(trans_path, header=0, names=COLS, dtype=str, nrows=nrows)
    raw["ts_dt"] = pd.to_datetime(raw.ts, format="%Y/%m/%d %H:%M")
    if days:
        raw = raw[raw.ts_dt <= raw.ts_dt.min() + pd.Timedelta(days=days)]
    df = pd.DataFrame({
        "tx_id": [f"IBM{i:08d}" for i in range(len(raw))],
        "src": (raw.from_bank.str.strip() + "_" + raw.from_acct.str.strip()).values,
        "dst": (raw.to_bank.str.strip() + "_" + raw.to_acct.str.strip()).values,
        "amount": raw.amt_paid.astype(float).values,
        "ts": raw.ts_dt.values,
        "is_laundering": raw.is_laundering.astype(int).values,
    })
    typ = ["normal"] * len(df)
    if patterns_path and os.path.exists(patterns_path):
        pats = load_patterns(patterns_path)
        keys = list(zip(raw.ts, raw.from_bank, raw.from_acct, raw.to_bank, raw.to_acct, raw.amt_recv, raw.amt_paid))
        keys = [tuple(str(x).strip() for x in k) for k in keys]
        typ = [pats.get(k, "laundering_other" if l else "normal") for k, l in zip(keys, df.is_laundering)]
    else:
        typ = ["laundering_other" if l else "normal" for l in df.is_laundering]
    df["typology"] = typ
    df = df[df.src != df.dst]  # drop self transfers
    return df.sort_values("ts").reset_index(drop=True)


def benchmark(df: pd.DataFrame) -> dict:
    t = time.time()
    alerts = aml.detect_all(df)
    secs = time.time() - t
    m = aml.evaluate(df, alerts)
    bad = df[df.is_laundering == 1]
    flagged = {x for a in alerts for x in a.tx_ids}
    base = aml.threshold_baseline(df)
    by = []
    for typ, g in bad.groupby("typology"):
        by.append(dict(pattern=typ, laundering_tx=int(len(g)), flowtrace_recall=round(g.tx_id.isin(flagged).mean(), 3),
                       baseline_recall=round(base[g.index].mean(), 3)))
    return dict(transactions=int(len(df)), laundering_tx=int(len(bad)), alerts=len(alerts), seconds=round(secs, 1),
                period=f"{df.ts.min():%d %b %Y} to {df.ts.max():%d %b %Y}",
                metrics=m.reset_index().to_dict("records"), by_pattern=by)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trans", default=os.path.join(ROOT, "data", "HI-Small_Trans.csv"))
    ap.add_argument("--patterns", default=os.path.join(ROOT, "data", "HI-Small_Patterns.txt"))
    ap.add_argument("--days", type=float, default=3)
    ap.add_argument("--nrows", type=int, default=None)
    ap.add_argument("--fan-min", type=int, default=None, help="override FAN_MIN_PARTIES")
    ap.add_argument("--limit", type=float, default=None, help="override REPORT_THRESHOLD (IBM amounts are mostly USD)")
    a = ap.parse_args()
    if a.fan_min:
        aml.FAN_MIN_PARTIES = a.fan_min
    if a.limit:
        aml.REPORT_THRESHOLD = a.limit
    df = load_ibm(a.trans, a.patterns, a.days, a.nrows)
    print(f"loaded {len(df):,} tx, {df.is_laundering.sum():,} laundering", flush=True)
    res = benchmark(df)
    res["settings"] = dict(days=a.days, fan_min=aml.FAN_MIN_PARTIES, limit=aml.REPORT_THRESHOLD)
    with open(os.path.join(ROOT, "data", "ibm_results.json"), "w", encoding="utf8") as f:
        json.dump(res, f, indent=2, default=str)
    print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
