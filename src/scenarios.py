"""Beat Fraudbusters: let a person design a laundering scheme, inject it into real background traffic, and see if it is caught.
Also an automatic red team sweep: how detection holds up as the launderer gets more careful.
CLI:  python src/scenarios.py   -> data/redteam.csv
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import aml  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCHEMES = {
    "fan_in": "Smurfing: many accounts deposit into one collector",
    "fan_out": "Dispersion: one account sprays money out",
    "cycle": "Round trip: money loops back to where it started",
    "mule_chain": "Mule chain: money relayed hop after hop",
}


@dataclass
class Plan:
    scheme: str = "mule_chain"
    total: float = 500_000.0    # ₹ moved
    parties: int = 6            # senders / receivers / hops
    gap_h: float = 2.0          # hours between steps
    skim_pct: float = 1.0       # % kept by each mule per hop (chains / cycles)
    decoys: int = 0             # random cover transfers per involved account
    seed: int = 1


def _background(df: pd.DataFrame, start: pd.Timestamp, hours: float) -> pd.DataFrame:
    lo, hi = start - pd.Timedelta(days=3), start + pd.Timedelta(hours=hours) + pd.Timedelta(days=2)
    bg = df[(df.ts >= lo) & (df.ts <= hi)]
    if "typology" in bg.columns:
        bg = bg[bg.typology == "normal"]
    return bg


def build_scheme(plan: Plan, accounts: np.ndarray, start: pd.Timestamp) -> pd.DataFrame:
    rng = np.random.default_rng(plan.seed)
    n = max(2, int(plan.parties))
    acc = list(rng.choice(accounts, n + 1, replace=False))
    rows, t = [], start
    gap = pd.Timedelta(hours=plan.gap_h)
    keep = 1 - plan.skim_pct / 100

    def add(s, d, a, ts):
        rows.append(dict(src=s, dst=d, amount=round(float(a), 2), ts=ts))

    if plan.scheme == "fan_in":
        hub, senders = acc[0], acc[1:]
        for s in senders:
            add(s, hub, plan.total / n * rng.uniform(0.97, 1.03), t)
            t += gap
    elif plan.scheme == "fan_out":
        hub, receivers = acc[0], acc[1:]
        for d in receivers:
            add(hub, d, plan.total / n * rng.uniform(0.6, 1.4), t)
            t += gap
    elif plan.scheme == "cycle":
        loop = acc[:n]
        amt = plan.total
        for i in range(n):
            t += gap
            add(loop[i], loop[(i + 1) % n], amt, t)
            amt *= keep
    else:  # mule_chain
        chain = acc[: n + 1]
        amt = plan.total
        for i in range(n):
            t += gap
            add(chain[i], chain[i + 1], amt, t)
            amt *= keep
    main = pd.DataFrame(rows)
    main["role"] = "scheme"

    decoys = []
    involved = sorted(set(main.src) | set(main.dst))
    for a in involved:
        for _ in range(int(plan.decoys)):
            dst = rng.choice(accounts)
            ts = start + pd.Timedelta(hours=float(rng.uniform(-24, plan.gap_h * n + 24)))
            amt = float(rng.uniform(0.05, 0.6) * plan.total / n)
            if dst != a:
                decoys.append(dict(src=a, dst=dst, amount=round(amt, 2), ts=ts, role="decoy"))
    out = pd.concat([main, pd.DataFrame(decoys)], ignore_index=True) if decoys else main
    out.insert(0, "tx_id", [f"ATK{plan.seed:03d}{i:04d}" for i in range(len(out))])
    out["typology"] = "attack"
    out["is_laundering"] = (out.role == "scheme").astype(int)
    return out


def why_missed(plan: Plan) -> str:
    if plan.scheme in ("fan_in", "fan_out") and plan.parties < aml.FAN_MIN_PARTIES:
        return f"Only {plan.parties} counterparties. Fraudbusters looks for {aml.FAN_MIN_PARTIES} or more inside {aml.FAN_WINDOW_H} hours, so this stayed under the radar."
    if plan.scheme in ("fan_in", "fan_out") and plan.gap_h * plan.parties > aml.FAN_WINDOW_H:
        return f"The deposits were spread over {plan.gap_h * plan.parties:.0f} hours, wider than the {aml.FAN_WINDOW_H} hour window."
    if plan.scheme in ("cycle", "mule_chain") and plan.skim_pct > 10:
        return f"Each hop kept {plan.skim_pct:.0f}%, so amounts stopped matching hop to hop (Fraudbusters allows up to 10%)."
    if plan.scheme == "mule_chain" and plan.gap_h > aml.PATIENT_GAP_H:
        return f"Hops {plan.gap_h:.0f} hours apart are slower than the {aml.PATIENT_GAP_H} hour patient mule window."
    if plan.scheme == "cycle" and plan.gap_h > aml.CYCLE_MAX_GAP_H:
        return f"Hops {plan.gap_h:.0f} hours apart are slower than the {aml.CYCLE_MAX_GAP_H} hour cycle window."
    if plan.scheme == "mule_chain" and plan.parties < aml.CHAIN_MIN_HOPS:
        return f"Only {plan.parties} hops. A chain needs {aml.CHAIN_MIN_HOPS} or more."
    return "The scheme blended into normal traffic strongly enough to stay below the alert score."


def play(df: pd.DataFrame, plan: Plan) -> dict:
    """Inject the plan into real background traffic and run the full detector."""
    t0 = time.time()
    rng = np.random.default_rng(plan.seed)
    normal = df[df.typology == "normal"] if "typology" in df.columns else df
    start = normal.ts.iloc[int(rng.integers(len(normal) // 4, 3 * len(normal) // 4))]
    accounts = pd.concat([normal.src, normal.dst]).unique()
    attack = build_scheme(plan, accounts, start)
    bg = _background(df, start, plan.gap_h * plan.parties)
    world = pd.concat([bg, attack], ignore_index=True).sort_values("ts").reset_index(drop=True)
    alerts = aml.detect_all(world)
    scheme_tx = set(attack.loc[attack.role == "scheme", "tx_id"])
    best, best_cov = None, 0.0
    for a in alerts:
        cov = len(scheme_tx & set(a.tx_ids)) / len(scheme_tx)
        if cov > best_cov:
            best, best_cov = a, cov
    caught = best_cov >= 0.5
    return dict(caught=caught, coverage=best_cov, alert=best if caught else None, attack=attack, world=world,
                reason=None if caught else why_missed(plan), seconds=round(time.time() - t0, 2),
                background_tx=len(bg))


def sweep(df, scheme, param, values, trials=8, **fixed) -> pd.DataFrame:
    rows = []
    for v in values:
        hits = 0
        for k in range(trials):
            p = Plan(scheme=scheme, seed=1000 + k, **fixed)
            setattr(p, param, v)
            hits += play(df, p)["caught"]
        rows.append(dict(scheme=scheme, param=param, value=v, trials=trials, caught_rate=hits / trials))
        print(scheme, param, v, hits / trials, flush=True)
    return pd.DataFrame(rows)


def main():
    df = aml.load_transactions(os.path.join(ROOT, "data", "transactions.csv"))
    parts = [
        sweep(df, "mule_chain", "gap_h", [0.5, 2, 6, 12, 24, 48, 72], parties=5, skim_pct=1),
        sweep(df, "mule_chain", "skim_pct", [0, 2, 5, 10, 15, 20], parties=5, gap_h=2),
        sweep(df, "mule_chain", "decoys", [0, 2, 5, 10], parties=5, gap_h=2, skim_pct=1),
        sweep(df, "fan_in", "parties", [4, 6, 8, 12, 20], total=180_000, gap_h=1.5),
        sweep(df, "cycle", "gap_h", [2, 12, 24, 48, 72], parties=4, skim_pct=1),
    ]
    out = pd.concat(parts, ignore_index=True)
    out.to_csv(os.path.join(ROOT, "data", "redteam.csv"), index=False)
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
