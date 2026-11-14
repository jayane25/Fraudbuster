"""Bank silos vs a privacy safe consortium.
Accounts are split across simulated banks. Siloed: each bank only sees transfers touching its own customers.
Consortium: banks share transfers with account IDs replaced by salted SHA-256 hashes, so no names or numbers leave the bank.
CLI:  python src/consortium.py   -> data/consortium.json
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import aml  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANKS = ["Bank A", "Bank B", "Bank C"]
SALT = "flowtrace-consortium-demo"


def pseudonym(acct: str) -> str:
    return hashlib.sha256((SALT + acct).encode()).hexdigest()[:12]


def assign_banks(df: pd.DataFrame, seed=7) -> dict:
    accts = sorted(set(df.src) | set(df.dst))
    rng = np.random.default_rng(seed)
    return dict(zip(accts, rng.choice(BANKS, len(accts))))


def _flagged(alerts):
    return {t for a in alerts for t in a.tx_ids}


def run(df: pd.DataFrame, seed=7) -> dict:
    bank_of = assign_banks(df, seed)
    sb, db = df.src.map(bank_of), df.dst.map(bank_of)

    siloed, per_bank = set(), {}
    for b in BANKS:
        view = df[(sb == b) | (db == b)]
        fl = _flagged(aml.detect_all(view))
        per_bank[b] = dict(visible_tx=int(len(view)), flagged=len(fl))
        siloed |= fl

    shared = df.copy()
    shared["src"] = shared.src.map(pseudonym)
    shared["dst"] = shared.dst.map(pseudonym)
    consortium = _flagged(aml.detect_all(shared))

    y = df.is_laundering.astype(bool)
    res = {"per_bank": per_bank,
           "siloed": aml._prf(y, df.tx_id.isin(siloed)),
           "consortium": aml._prf(y, df.tx_id.isin(consortium))}

    bad = df[y].copy()
    bad["cross_bank"] = (sb[y] != db[y]).values
    cases = bad.groupby("case_id").agg(typology=("typology", "first"),
                                       banks=("src", lambda s: len({bank_of[a] for a in s})))
    res["cases_spanning_banks_pct"] = round(100 * (cases.banks > 1).mean(), 1)
    by = []
    for t, g in bad.groupby("typology"):
        by.append(dict(typology=t, siloed=round(g.tx_id.isin(siloed).mean(), 3),
                       consortium=round(g.tx_id.isin(consortium).mean(), 3)))
    res["recall_by_typology"] = by
    sample = df.head(5)
    res["example"] = [dict(tx_id=r.tx_id, src=r.src, src_shared=pseudonym(r.src), dst=r.dst, dst_shared=pseudonym(r.dst),
                           amount=r.amount) for r in sample.itertuples()]
    return res


def main():
    df = aml.load_transactions(os.path.join(ROOT, "data", "transactions.csv"))
    res = run(df)
    with open(os.path.join(ROOT, "data", "consortium.json"), "w", encoding="utf8") as f:
        json.dump(res, f, indent=2, default=str)
    print(json.dumps({k: res[k] for k in ("siloed", "consortium", "cases_spanning_banks_pct", "recall_by_typology")}, indent=2))


if __name__ == "__main__":
    main()
