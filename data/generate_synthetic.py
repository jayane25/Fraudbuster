"""Generate a labelled synthetic bank: accounts with KYC details, normal traffic, laundering schemes,
and GENUINE look-alikes that have the same graph shapes as laundering.

Laundering (is_laundering = 1, column `typology`):
  fan_in      many sources -> one collector (smurfing), then the collector moves the money out fast.
              A quarter use a charity account as a front (donation sized amounts).
  fan_out     a lump arrives, is split to many accounts within hours, and half of them pass it on again.
  cycle       A -> B -> C -> ... -> A round tripping with small fees skimmed.
  mule_chain  A -> m1 -> m2 -> ... -> sink, every hop within hours, amount almost unchanged.

Genuine look-alikes (is_laundering = 0, typology "normal", column `scenario` says which):
  donation_drive         many donors -> an NGO (fan in shape); the NGO spends slowly afterwards.
  relief_redistribution  the NGO pays many beneficiaries equal amounts (fan out shape).
  payroll                a business pays the same employees every month (fan out shape).
  chit_fund              members pay a fixed amount to an organiser who pays the pot to one member (fan in shape).
  merchant_sales         many customers pay a shop for an event or sale (fan in shape).
  own_account_sweep      one customer moves money through 3 or 4 of their OWN accounts in minutes (mule chain shape).

Mule accounts are more often newly opened (as in real cases), but not always, and genuine accounts can be new too.

Outputs:  data/transactions.csv   data/accounts.csv (account, holder, kind, opened)
Usage:    python data/generate_synthetic.py --seed 7
"""
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

START = datetime(2026, 1, 1)
DAYS = 90


class Bank:
    """Accounts with KYC details and helpers to pick realistic participants."""

    def __init__(self, rng, n):
        self.rng = rng
        self.ids = np.array([f"ACC{i:05d}" for i in range(n)])
        self.kind = rng.choice(["individual", "business", "merchant", "ngo"], n, p=[0.88, 0.06, 0.04, 0.02])
        age = rng.integers(200, 15 * 365, n)
        new = rng.random(n) < 0.10                       # 10% of accounts opened in the last 6 months
        age[new] = rng.integers(15, 180, new.sum())
        self.age = age
        self.holder = np.array([f"CUS{i:05d}" for i in range(n)])
        ind = np.flatnonzero(self.kind == "individual")
        groups = rng.choice(ind, (60, 4), replace=False)  # 60 customers who own 4 accounts each
        for g in groups:
            self.holder[g] = self.holder[g[0]]
        self.multi = [self.ids[g] for g in groups]
        self.pool = {k: self.ids[self.kind == k] for k in ("individual", "business", "merchant", "ngo")}
        self.new_ind = self.ids[(self.kind == "individual") & (age < 180)]
        self.new_ngo = self.ids[(self.kind == "ngo") & (age < 365)]
        self.est_ngo = self.ids[(self.kind == "ngo") & (age >= 365)]
        self.used_multi = 0

    def frame(self):
        return pd.DataFrame({"account": self.ids, "holder": self.holder, "kind": self.kind,
                             "opened": [(START - timedelta(days=int(d))).date().isoformat() for d in self.age]})

    def pick(self, pool, k=1):
        return list(self.rng.choice(self.pool[pool] if isinstance(pool, str) else pool, k, replace=False))

    def mules(self, k):
        """Laundering participants: 60% newly opened individual accounts, the rest any individual."""
        out = set()
        while len(out) < k:
            pool = self.new_ind if self.rng.random() < 0.6 else self.pool["individual"]
            out.add(str(self.rng.choice(pool)))
        return list(out)


def _t0(rng, margin=12):
    return START + timedelta(seconds=int(rng.integers(0, (DAYS - margin) * 86400)))


def _h(rng, lo, hi):
    return timedelta(hours=float(rng.uniform(lo, hi)))


def row(src, dst, amt, ts, scenario, case, bad):
    return dict(src=src, dst=dst, amount=round(float(amt), 2), ts=ts,
                typology=scenario if bad else "normal", scenario=scenario, case_id=case, is_laundering=int(bad))


# ------------------------------------------------------------------ normal background traffic
def normal_traffic(rng, bank, n):
    w = rng.pareto(1.5, len(bank.ids)) + 1                # some legit accounts are very busy
    w = w / w.sum()
    src = rng.choice(bank.ids, n, p=w)
    dst = rng.choice(bank.ids, n, p=w)
    amt = np.round(rng.lognormal(mean=6.5, sigma=1.3, size=n), 2)
    return [row(s, d, a, START + timedelta(seconds=int(rng.integers(0, DAYS * 86400))), "normal", None, False)
            for s, d, a in zip(src, dst, amt) if s != d]


# ------------------------------------------------------------------ laundering
def fan_in(rng, bank, case):
    front = rng.random() < 0.25                           # fake charity used as a front
    if front:
        collector = str(rng.choice(bank.new_ngo if len(bank.new_ngo) and rng.random() < 0.5 else bank.pool["ngo"]))
    else:
        collector = bank.mules(1)[0]
    smurfs = [s for s in bank.mules(int(rng.integers(8, 25))) if s != collector]
    t0, rows, total, last = _t0(rng), [], 0.0, None
    for s in smurfs:
        amt = rng.choice([500, 1000, 2000, 5000, 10000]) if front else rng.uniform(8000, 9900)
        ts = t0 + _h(rng, 0, 48)
        rows.append(row(s, collector, amt, ts, "fan_in", case, True))
        total += amt
        last = max(last or ts, ts)
    exits = [e for e in bank.mules(int(rng.integers(1, 4))) if e != collector]
    shares = rng.dirichlet(np.ones(len(exits))) * total * rng.uniform(0.85, 0.98)
    for e, amt in zip(exits, shares):                     # the collector moves the money on within a day
        rows.append(row(collector, e, amt, last + _h(rng, 2, 24), "fan_in", case, True))
    return rows


def fan_out(rng, bank, case):
    source, feeder = bank.mules(2)
    k = int(rng.integers(8, 25))
    dsts = [d for d in bank.mules(k) if d not in (source, feeder)]
    total = float(rng.uniform(80_000, 250_000))
    t0 = _t0(rng)
    rows = [row(feeder, source, total * rng.uniform(1.0, 1.05), t0, "fan_out", case, True)]   # lump arrives
    shares = rng.dirichlet(np.ones(len(dsts))) * total
    sinks = bank.mules(int(rng.integers(2, 5)))
    for d, a in zip(dsts, shares):
        ts = t0 + _h(rng, 1, 36)
        rows.append(row(source, d, a, ts, "fan_out", case, True))
        if rng.random() < 0.5:                            # layering: receivers pass it on again
            sink = str(rng.choice(sinks))
            if sink != d:
                rows.append(row(d, sink, a * rng.uniform(0.9, 0.99), ts + _h(rng, 1, 24), "fan_out", case, True))
    return rows


def cycle(rng, bank, case):
    nodes = bank.mules(int(rng.integers(3, 7)))
    amt, t, rows = float(rng.uniform(20_000, 150_000)), _t0(rng), []
    for i in range(len(nodes)):
        t = t + _h(rng, 1, 30)
        rows.append(row(nodes[i], nodes[(i + 1) % len(nodes)], amt, t, "cycle", case, True))
        amt *= rng.uniform(0.95, 0.995)                   # fees skimmed each hop
    return rows


def mule_chain(rng, bank, case):
    nodes = bank.mules(int(rng.integers(4, 8)))
    amt, t, rows = float(rng.uniform(15_000, 90_000)), _t0(rng), []
    for i in range(len(nodes) - 1):
        t = t + timedelta(minutes=float(rng.uniform(5, 240)))
        rows.append(row(nodes[i], nodes[i + 1], amt, t, "mule_chain", case, True))
        amt *= rng.uniform(0.97, 0.999)
    return rows


# ------------------------------------------------------------------ genuine look-alikes
def donation_drive(rng, bank, case):
    ngo = str(rng.choice(bank.est_ngo if rng.random() < 0.8 else bank.pool["ngo"]))
    donors = [d for d in bank.pick("individual", int(rng.integers(10, 50))) if d != ngo]
    fixed = rng.random() < 0.45                           # "suggested donation" campaigns: everyone gives the same
    amount = rng.choice([1000, 2000, 5000])
    t0, rows, total = _t0(rng, 15), [], 0.0
    days = rng.uniform(1, 5)
    for d in donors:
        amt = amount if fixed else rng.choice([100, 250, 500, 1000, 2000, 5000, 10000, 25000],
                                              p=[.1, .15, .25, .2, .15, .1, .04, .01])
        rows.append(row(d, ngo, amt, t0 + _h(rng, 0, 24 * days), "donation_drive", case, False))
        total += amt
    end = t0 + timedelta(days=days)
    if rng.random() < 0.4:                                # relief: redistribute to many beneficiaries
        ben = [b for b in bank.pick("individual", int(rng.integers(10, 30))) if b != ngo]
        each = round(total * rng.uniform(0.5, 0.9) / len(ben), -2)
        t1 = end + timedelta(days=float(rng.uniform(2, 7)))
        rows += [row(ngo, b, each, t1 + _h(rng, 0, 30), "relief_redistribution", case, False) for b in ben]
    else:                                                 # normal spending on suppliers over weeks
        t = end
        for v in bank.pick("business", int(rng.integers(3, 8))):
            t = t + timedelta(days=float(rng.uniform(2, 6)))
            rows.append(row(ngo, v, total * rng.uniform(0.05, 0.12), t, "donation_drive", case, False))
    return rows


def payroll(rng, bank, case):
    employer = bank.pick("business")[0]
    staff = [e for e in bank.pick("individual", int(rng.integers(10, 35))) if e != employer]
    salary = {e: round(float(rng.uniform(12_000, 60_000)), -2) for e in staff}
    rows = []
    for _ in range(int(rng.integers(15, 40))):            # revenue from clients through the quarter
        client = bank.pick("business")[0] if rng.random() < 0.6 else bank.pick("individual")[0]
        if client != employer:
            rows.append(row(client, employer, rng.lognormal(10.5, 0.7), START + _h(rng, 0, 24 * DAYS), "payroll", case, False))
    for month in (1, 2, 3):
        day = datetime(2026, month, 1) + timedelta(days=int(rng.integers(0, 2)), hours=9)
        rows += [row(employer, e, salary[e], day + _h(rng, 0, 4), "payroll", case, False) for e in staff]
    return rows


def chit_fund(rng, bank, case):
    organiser = bank.pick("individual")[0]
    members = [m for m in bank.pick("individual", int(rng.integers(10, 20))) if m != organiser]
    contrib = rng.choice([2000, 5000, 9000, 10000])
    winners = list(rng.choice(members, 3, replace=False))
    rows = []
    for month, winner in zip((1, 2, 3), winners):
        t0 = datetime(2026, month, int(rng.integers(5, 10)), 10)
        rows += [row(m, organiser, contrib, t0 + _h(rng, 0, 48), "chit_fund", case, False) for m in members]
        rows.append(row(organiser, winner, contrib * len(members) * 0.95, t0 + _h(rng, 50, 72), "chit_fund", case, False))
    return rows


def merchant_sales(rng, bank, case):
    shop = bank.pick("merchant")[0]
    buyers = [b for b in bank.pick("individual", int(rng.integers(15, 60))) if b != shop]
    price = rng.choice([499, 999, 1499, 2500]) if rng.random() < 0.4 else None   # ticketed event: one price
    t0, rows, total = _t0(rng, 5), [], 0.0
    for b in buyers:
        amt = price or round(float(rng.lognormal(7, 0.6)), 2)
        rows.append(row(b, shop, amt, t0 + _h(rng, 0, 60), "merchant_sales", case, False))
        total += amt
    supplier = bank.pick("business")[0]
    rows.append(row(shop, supplier, total * rng.uniform(0.3, 0.6), t0 + timedelta(days=float(rng.uniform(4, 9))),
                    "merchant_sales", case, False))
    return rows


def own_account_sweep(rng, bank, case):
    accts = list(bank.multi[bank.used_multi % len(bank.multi)])
    bank.used_multi += 1
    hops = 3 if rng.random() < 0.7 else 2
    rows = []
    for month in rng.choice([1, 2, 3], int(rng.integers(2, 4)), replace=False):
        amt = round(float(rng.uniform(20_000, 150_000)), -2)
        t = datetime(2026, int(month), int(rng.integers(1, 25)), int(rng.integers(8, 20)))
        for i in range(hops):
            t = t + timedelta(minutes=float(rng.uniform(5, 120)))
            rows.append(row(accts[i], accts[i + 1], amt, t, "own_account_sweep", case, False))
    return rows


LAUNDERING = (fan_in, fan_out, cycle, mule_chain)
GENUINE = {donation_drive: 40, payroll: 15, chit_fund: 12, merchant_sales: 30, own_account_sweep: 40}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", type=int, default=3000)
    ap.add_argument("--tx", type=int, default=40000)
    ap.add_argument("--cases", type=int, default=50, help="laundering cases per typology")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(Path(__file__).parent / "transactions.csv"))
    a = ap.parse_args()

    rng = np.random.default_rng(a.seed)
    bank = Bank(rng, a.accounts)
    rows = normal_traffic(rng, bank, a.tx)
    n = 0
    for fn in LAUNDERING:
        for _ in range(a.cases):
            n += 1
            rows += fn(rng, bank, f"C{n:04d}")
    g = 0
    for fn, count in GENUINE.items():
        for _ in range(count):
            g += 1
            rows += fn(rng, bank, f"G{g:04d}")

    end = START + timedelta(days=DAYS)
    df = pd.DataFrame([r for r in rows if r["src"] != r["dst"] and r["ts"] < end]).sort_values("ts").reset_index(drop=True)
    df.insert(0, "tx_id", [f"TX{i:07d}" for i in range(len(df))])
    df.to_csv(a.out, index=False)
    bank.frame().to_csv(Path(a.out).with_name("accounts.csv"), index=False)
    print(f"wrote {len(df):,} tx ({df.is_laundering.sum():,} laundering) -> {a.out}")
    print(df.scenario.value_counts().to_string())


if __name__ == "__main__":
    main()
