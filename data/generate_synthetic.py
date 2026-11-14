"""Generate a labeled synthetic transaction dataset with injected laundering typologies.

Typologies injected (label column `typology`):
  fan_in      - many sources -> one collector in a short window (smurfing / structuring)
  fan_out     - one source -> many recipients in a short window (dispersion)
  cycle       - A -> B -> C -> ... -> A round-tripping with slight value decay
  mule_chain  - A -> m1 -> m2 -> ... -> sink, each hop fast, amount ~preserved (pass-through)

Everything else is normal background traffic (salary, retail, P2P).
Usage:  python data/generate_synthetic.py --accounts 3000 --tx 40000 --seed 7
"""
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

START = datetime(2026, 1, 1)


def _ts(rng, day_span=90):
    return START + timedelta(seconds=int(rng.integers(0, day_span * 86400)))


def normal_traffic(rng, accounts, n):
    # Power-law-ish activity so some legit accounts are busy (hard negatives for the baseline)
    w = rng.pareto(1.5, len(accounts)) + 1
    w = w / w.sum()
    src = rng.choice(accounts, n, p=w)
    dst = rng.choice(accounts, n, p=w)
    amt = np.round(rng.lognormal(mean=6.5, sigma=1.3, size=n), 2)
    rows = [
        dict(src=s, dst=d, amount=a, ts=_ts(rng), typology="normal", case_id=None)
        for s, d, a in zip(src, dst, amt)
        if s != d
    ]
    return rows


def fan_in(rng, accounts, case):
    collector = rng.choice(accounts)
    k = int(rng.integers(8, 25))
    srcs = rng.choice(accounts, k, replace=False)
    t0 = _ts(rng)
    rows = []
    for s in srcs:
        if s == collector:
            continue
        # Structuring: amounts just under a reporting threshold
        amt = round(float(rng.uniform(8000, 9900)), 2)
        rows.append(dict(src=s, dst=collector, amount=amt, ts=t0 + timedelta(hours=float(rng.uniform(0, 48))), typology="fan_in", case_id=case))
    return rows


def fan_out(rng, accounts, case):
    source = rng.choice(accounts)
    k = int(rng.integers(8, 25))
    dsts = rng.choice(accounts, k, replace=False)
    t0 = _ts(rng)
    total = float(rng.uniform(80_000, 250_000))
    shares = rng.dirichlet(np.ones(k)) * total
    return [
        dict(src=source, dst=d, amount=round(float(a), 2), ts=t0 + timedelta(hours=float(rng.uniform(0, 36))), typology="fan_out", case_id=case)
        for d, a in zip(dsts, shares)
        if d != source
    ]


def cycle(rng, accounts, case):
    k = int(rng.integers(3, 7))
    nodes = list(rng.choice(accounts, k, replace=False))
    amt = float(rng.uniform(20_000, 150_000))
    t = _ts(rng)
    rows = []
    for i in range(k):
        t = t + timedelta(hours=float(rng.uniform(1, 30)))
        rows.append(dict(src=nodes[i], dst=nodes[(i + 1) % k], amount=round(amt, 2), ts=t, typology="cycle", case_id=case))
        amt *= rng.uniform(0.95, 0.995)  # fees skimmed each hop
    return rows


def mule_chain(rng, accounts, case):
    k = int(rng.integers(4, 8))
    nodes = list(rng.choice(accounts, k, replace=False))
    amt = float(rng.uniform(15_000, 90_000))
    t = _ts(rng)
    rows = []
    for i in range(k - 1):
        t = t + timedelta(minutes=float(rng.uniform(5, 240)))  # rapid pass-through
        rows.append(dict(src=nodes[i], dst=nodes[i + 1], amount=round(amt, 2), ts=t, typology="mule_chain", case_id=case))
        amt *= rng.uniform(0.97, 0.999)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", type=int, default=3000)
    ap.add_argument("--tx", type=int, default=40000)
    ap.add_argument("--cases", type=int, default=40, help="cases per typology")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(Path(__file__).parent / "transactions.csv"))
    a = ap.parse_args()

    rng = np.random.default_rng(a.seed)
    accounts = np.array([f"ACC{i:05d}" for i in range(a.accounts)])
    rows = normal_traffic(rng, accounts, a.tx)
    case = 0
    for fn in (fan_in, fan_out, cycle, mule_chain):
        for _ in range(a.cases):
            case += 1
            rows += fn(rng, accounts, f"C{case:04d}")

    df = pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)
    df.insert(0, "tx_id", [f"TX{i:07d}" for i in range(len(df))])
    df["is_laundering"] = (df.typology != "normal").astype(int)
    df.to_csv(a.out, index=False)
    print(f"wrote {len(df):,} tx ({df.is_laundering.sum():,} laundering) -> {a.out}")
    print(df.typology.value_counts().to_string())


if __name__ == "__main__":
    main()
