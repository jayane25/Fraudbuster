"""Fraudbusters detection engine: temporal money-flow graph -> laundering typology alerts.

Pipeline:  load_transactions -> build_graph -> detect_* -> score_alerts -> evaluate
CLI:       python src/aml.py [path/to/transactions.csv]

Input CSV columns: tx_id, src, dst, amount, ts  (+ optional is_laundering, typology for evaluation)
"""
from __future__ import annotations

import math
import os
import sys
from bisect import bisect_right
from dataclasses import dataclass, field

import networkx as nx
import numpy as np
import pandas as pd

# ---------------------------------------------------------------- config
FAN_WINDOW_H = 72          # sliding window for fan-in / fan-out bursts
FAN_MIN_PARTIES = 8        # distinct counterparties needed inside the window
FAN_MIN_SCORE = 0.55       # structure score needed to raise a fan alert
CHAIN_MAX_GAP_H = 6        # mule hop must leave within this many hours
CHAIN_MIN_HOPS = 3
CYCLE_MAX_GAP_H = 48
CYCLE_MAX_LEN = 6
PASS_RATIO = (0.90, 1.0)   # next hop keeps 90-100% of the incoming amount
PATIENT_GAP_H = 48         # patient mules: slower hops allowed...
PATIENT_RATIO = (0.97, 1.0)  # ...but the amount must be passed on almost exactly
PATIENT_MIN_HOPS = 4
REPORT_THRESHOLD = 10_000  # cash-transaction-report style limit used by the baseline

TYPOLOGY_LABEL = {
    "fan_in": "Fan in (smurfing)",
    "fan_out": "Fan out (dispersion)",
    "cycle": "Round trip cycle",
    "mule_chain": "Mule chain (relay)",
}
TYPOLOGY_WEIGHT = {"fan_in": 0.9, "fan_out": 0.85, "cycle": 1.0, "mule_chain": 0.95}


@dataclass
class Alert:
    typology: str
    accounts: list[str]
    tx_ids: list[str]
    total_amount: float
    start: pd.Timestamp
    end: pd.Timestamp
    structure: float                       # 0..1 how cleanly the pattern matches
    focal: str | None = None               # collector / disperser account
    path: list[str] = field(default_factory=list)
    risk: float = 0.0
    alert_id: str = ""

    @property
    def label(self) -> str:
        return TYPOLOGY_LABEL[self.typology]

    @property
    def hours(self) -> float:
        return max((self.end - self.start).total_seconds() / 3600, 0.0)


# ---------------------------------------------------------------- loading / graph
def load_transactions(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = {"tx_id", "src", "dst", "amount", "ts"} - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing columns: {sorted(missing)}")
    df["ts"] = pd.to_datetime(df["ts"])
    df["amount"] = df["amount"].astype(float)
    return df.sort_values("ts").reset_index(drop=True)


def build_graph(df: pd.DataFrame) -> nx.MultiDiGraph:
    """Temporal, directed, weighted multigraph: one edge per transaction."""
    g = nx.MultiDiGraph()
    for r in df.itertuples(index=False):
        g.add_edge(r.src, r.dst, key=r.tx_id, amount=r.amount, ts=r.ts)
    return g


# ---------------------------------------------------------------- fan-in / fan-out
def _fan_score(amounts: np.ndarray, parties: int) -> float:
    """High when many parties move similar, sub-threshold or large coordinated sums."""
    cv = amounts.std() / (amounts.mean() + 1e-9)
    near_limit = np.mean((amounts >= 0.8 * REPORT_THRESHOLD) & (amounts < REPORT_THRESHOLD))
    party_term = min(parties / 20, 1.0)
    uniformity = max(0.0, 1 - cv)                      # structuring -> similar amounts
    big = min(amounts.sum() / 100_000, 1.0)            # dispersion of a large pot
    return 0.35 * party_term + 0.35 * max(near_limit, uniformity * 0.6) + 0.30 * big


def _detect_fan(df: pd.DataFrame, focal_col: str, other_col: str, typology: str) -> list[Alert]:
    alerts = []
    win = np.timedelta64(FAN_WINDOW_H, "h")
    for focal, grp in df.groupby(focal_col, sort=False):
        if grp[other_col].nunique() < FAN_MIN_PARTIES:
            continue
        ts = grp["ts"].to_numpy()
        others = grp[other_col].to_numpy()
        amts = grp["amount"].to_numpy()
        best = None
        j = 0
        for i in range(len(grp)):                      # two-pointer sliding window
            while ts[i] - ts[j] > win:
                j += 1
            if i - j + 1 < FAN_MIN_PARTIES:
                continue
            parties = len(set(others[j : i + 1]))
            if parties < FAN_MIN_PARTIES:
                continue
            s = _fan_score(amts[j : i + 1], parties)
            if best is None or s > best[0]:
                best = (s, j, i)
        if best and best[0] >= FAN_MIN_SCORE:
            s, j, i = best
            w = grp.iloc[j : i + 1]
            alerts.append(Alert(
                typology=typology, focal=focal,
                accounts=[focal] + sorted(set(w[other_col])),
                tx_ids=list(w["tx_id"]), total_amount=float(w["amount"].sum()),
                start=w["ts"].min(), end=w["ts"].max(), structure=float(min(s, 1.0)),
            ))
    return alerts


def detect_fan_in(df):
    return _detect_fan(df, "dst", "src", "fan_in")


def detect_fan_out(df):
    return _detect_fan(df, "src", "dst", "fan_out")


# ---------------------------------------------------------------- cycles & mule chains
class _OutIndex:
    """Per-account outgoing transactions sorted by time, for fast 'next hop' lookups."""

    def __init__(self, df):
        self.by_src = {}
        for src, g in df.groupby("src", sort=False):
            g = g.sort_values("ts")
            self.by_src[src] = (g["ts"].to_numpy(), list(g[["tx_id", "dst", "amount", "ts"]].itertuples(index=False, name=None)))

    def next_hops(self, acct, after, max_gap_h, amount, ratio=None):
        if acct not in self.by_src:
            return []
        times, rows = self.by_src[acct]
        lo = bisect_right(times, np.datetime64(after))
        hi = bisect_right(times, np.datetime64(after + pd.Timedelta(hours=max_gap_h)))
        r = ratio or PASS_RATIO
        lo_amt, hi_amt = r[0] * amount, r[1] * amount
        return [r for r in rows[lo:hi] if lo_amt <= r[2] <= hi_amt]


def _span(df_by_tx, txs):
    ts = [df_by_tx[t] for t in txs]
    return min(ts), max(ts)


def detect_cycles(df, idx: _OutIndex) -> list[Alert]:
    alerts, seen = [], set()
    ts_of = dict(zip(df.tx_id, df.ts))
    for r in df[["tx_id", "src", "dst", "amount", "ts"]].itertuples(index=False):
        stack = [(r.dst, r.ts, r.amount, [r.src, r.dst], [r.tx_id])]
        while stack:
            acct, t, amt, path, txs = stack.pop()
            if len(txs) >= CYCLE_MAX_LEN:
                continue
            for tx_id, dst, a, ts in idx.next_hops(acct, t, CYCLE_MAX_GAP_H, amt):
                if dst == path[0] and len(txs) + 1 >= 3:
                    key = frozenset(txs + [tx_id])
                    if key not in seen:
                        seen.add(key)
                        all_tx = txs + [tx_id]
                        start, end = _span(ts_of, all_tx)
                        alerts.append(Alert(
                            typology="cycle", accounts=path, path=path + [path[0]],
                            tx_ids=all_tx, total_amount=float(r.amount), start=start, end=end,
                            structure=float(min(1.0, 0.6 + 0.4 * a / r.amount)),
                        ))
                elif dst not in path:
                    stack.append((dst, ts, a, path + [dst], txs + [tx_id]))
    return alerts


def detect_mule_chains(df, idx: _OutIndex, exclude_tx: set, max_gap_h=None, ratio=None, min_hops=None) -> list[Alert]:
    """Maximal paths where money is forwarded fast and almost intact.
    Default tier: fast mules (<= CHAIN_MAX_GAP_H per hop). Patient tier: slower hops, stricter amount match, longer chain."""
    max_gap_h = max_gap_h or CHAIN_MAX_GAP_H
    min_hops = min_hops or CHAIN_MIN_HOPS
    ts_of = dict(zip(df.tx_id, df.ts))
    cand = df[~df.tx_id.isin(exclude_tx)]
    alerts, covered = [], set()
    for r in cand[["tx_id", "src", "dst", "amount", "ts"]].itertuples(index=False):
        if r.tx_id in covered:
            continue
        best = None
        stack = [(r.dst, r.ts, r.amount, [r.src, r.dst], [r.tx_id])]
        while stack:
            acct, t, amt, path, txs = stack.pop()
            nxt = [h for h in idx.next_hops(acct, t, max_gap_h, amt, ratio)
                   if h[1] not in path and h[0] not in exclude_tx]
            if not nxt and len(txs) >= min_hops and (best is None or len(txs) > len(best[1])):
                best = (path, txs)
            for tx_id, dst, a, ts in nxt[:5]:
                stack.append((dst, ts, a, path + [dst], txs + [tx_id]))
        if best:
            path, txs = best
            covered.update(txs)
            start, end = _span(ts_of, txs)
            hours = (end - start).total_seconds() / 3600
            speed = 1 - min(hours / (max_gap_h * len(txs)), 1)
            alerts.append(Alert(
                typology="mule_chain", accounts=path, path=path, tx_ids=txs,
                total_amount=float(r.amount), start=start, end=end,
                structure=float(min(1.0, 0.5 + 0.1 * len(txs) + 0.3 * speed)),
            ))
    # drop chains fully contained in a longer one
    alerts.sort(key=lambda a: -len(a.tx_ids))
    kept, used = [], set()
    for a in alerts:
        if not set(a.tx_ids) <= used:
            kept.append(a)
            used.update(a.tx_ids)
    return kept


# ---------------------------------------------------------------- scoring
def score_alerts(alerts: list[Alert]) -> list[Alert]:
    """Risk 0-100 = typology weight x (structure, money at stake, speed, breadth)."""
    for a in alerts:
        money = min(math.log10(max(a.total_amount, 1)) / 6, 1.0)      # 10 lakh -> 1.0
        speed = 1 - min(a.hours / 168, 1.0)                           # within a week
        breadth = min(len(a.accounts) / 15, 1.0)
        raw = 0.40 * a.structure + 0.25 * money + 0.20 * speed + 0.15 * breadth
        a.risk = round(100 * TYPOLOGY_WEIGHT[a.typology] * raw, 1)
    alerts.sort(key=lambda a: -a.risk)
    for i, a in enumerate(alerts, 1):
        a.alert_id = f"AL{i:04d}"
    return alerts


def detect_all(df: pd.DataFrame) -> list[Alert]:
    idx = _OutIndex(df)
    cycles = detect_cycles(df, idx)
    cycle_tx = {t for a in cycles for t in a.tx_ids}
    fast = detect_mule_chains(df, idx, cycle_tx)
    used = cycle_tx | {t for a in fast for t in a.tx_ids}
    patient = detect_mule_chains(df, idx, used, max_gap_h=PATIENT_GAP_H, ratio=PATIENT_RATIO, min_hops=PATIENT_MIN_HOPS)
    alerts = detect_fan_in(df) + detect_fan_out(df) + cycles + fast + patient
    return score_alerts(alerts)


def alerts_frame(alerts: list[Alert]) -> pd.DataFrame:
    return pd.DataFrame([{
        "alert_id": a.alert_id, "risk": a.risk, "typology": a.label,
        "accounts": len(a.accounts), "transactions": len(a.tx_ids),
        "amount": round(a.total_amount, 2), "hours": round(a.hours, 1),
        "start": a.start, "focal": a.focal or (a.path[0] if a.path else ""),
    } for a in alerts])


# ---------------------------------------------------------------- baseline & evaluation
def threshold_baseline(df: pd.DataFrame, amount_limit=REPORT_THRESHOLD, daily_count=10) -> pd.Series:
    """Classic rule engine, one transaction at a time:
    flag if amount >= limit OR sender made >= daily_count transfers that day."""
    day = df["ts"].dt.date
    per_day = df.groupby([df["src"], day])["tx_id"].transform("count")
    return (df["amount"] >= amount_limit) | (per_day >= daily_count)


def _prf(y_true, y_pred):
    tp = int((y_true & y_pred).sum()); fp = int((~y_true & y_pred).sum()); fn = int((y_true & ~y_pred).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f1, 3), "tp": tp, "fp": fp, "fn": fn}


def evaluate(df: pd.DataFrame, alerts: list[Alert], min_risk: float = 0.0) -> pd.DataFrame:
    """Transaction-level precision / recall / F1 for Fraudbusters vs the threshold baseline."""
    if "is_laundering" not in df.columns:
        raise ValueError("Dataset has no is_laundering label column")
    y = df["is_laundering"].astype(bool)
    flagged = {t for a in alerts if a.risk >= min_risk for t in a.tx_ids}
    ours = df["tx_id"].isin(flagged)
    base = threshold_baseline(df)
    rows = [{"system": "Threshold baseline", **_prf(y, base)},
            {"system": "Fraudbusters (graph)", **_prf(y, ours)}]
    return pd.DataFrame(rows).set_index("system")


def recall_by_typology(df, alerts) -> pd.DataFrame:
    flagged = {t for a in alerts for t in a.tx_ids}
    bad = df[df.is_laundering == 1].copy()
    bad["Fraudbusters"] = bad.tx_id.isin(flagged)
    bad["Baseline"] = threshold_baseline(df)[bad.index]
    return bad.groupby("typology")[["Fraudbusters", "Baseline"]].mean().round(3)


# ---------------------------------------------------------------- explanations
def template_summary(a: Alert, df: pd.DataFrame) -> str:
    amt = f"₹{a.total_amount:,.0f}"
    when = f"{a.start:%d %b %H:%M} to {a.end:%d %b %H:%M} ({a.hours:.0f} h)"
    if a.typology == "fan_in":
        return (f"{len(a.accounts) - 1} different accounts sent {amt} into {a.focal} within {when}. "
                f"Amounts are similar and sit just under the alert limit, "
                f"a classic structuring (smurfing) pattern where one collector gathers split deposits.")
    if a.typology == "fan_out":
        return (f"{a.focal} pushed {amt} out to {len(a.accounts) - 1} accounts within {when}. "
                f"Rapidly breaking one large balance into many smaller transfers is a layering step "
                f"that makes the money hard to follow.")
    trail = " → ".join(a.path)
    if a.typology == "cycle":
        sub = df[df.tx_id.isin(a.tx_ids)].sort_values("ts")
        back = sub.amount.iloc[-1]
        return (f"Money left {a.path[0]} and came back to it through {len(a.accounts) - 1} other accounts: {trail}. "
                f"{amt} went out and ₹{back:,.0f} returned within {when}. Round tripping like this creates fake "
                f"business activity to make dirty funds look legitimate.")
    return (f"{amt} was relayed through {len(a.tx_ids)} hops in {when}: {trail}. Each intermediate account forwarded "
            f"almost the full amount soon after receiving it, typical of money mule accounts used to "
            f"distance funds from their source.")


def llm_summary(a: Alert, df: pd.DataFrame) -> str | None:
    """Optional analyst-style narrative from Claude. Returns None if no credentials or on any API error."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return None
    try:
        import anthropic
    except ImportError:
        return None
    sub = df[df.tx_id.isin(a.tx_ids)].sort_values("ts")[["tx_id", "src", "dst", "amount", "ts"]]
    prompt = (
        "You are an AML investigator writing an alert note for a bank compliance analyst.\n"
        f"Detected typology: {a.label}. Risk score: {a.risk}/100.\n"
        f"Transactions (CSV):\n{sub.to_csv(index=False)}\n"
        "Write 4-6 plain-language sentences: describe the money trail step by step, why it matches the typology, "
        "and what the analyst should check next. Use ₹ for amounts. No headings, no bullet points."
    )
    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model="claude-opus-5-5",
            max_tokens=2000,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": prompt}],
        )
        if resp.stop_reason == "refusal":
            return None
        return "".join(b.text for b in resp.content if b.type == "text").strip() or None
    except anthropic.APIError:
        return None


# ---------------------------------------------------------------- CLI
def main():
    if hasattr(sys.stdout, "reconfigure"):          # Windows terminals default to cp1252, which has no rupee sign
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "data", "transactions.csv")
    df = load_transactions(path)
    alerts = detect_all(df)
    print(f"{len(df):,} transactions -> {len(alerts)} alerts")
    print(alerts_frame(alerts).head(10).to_string(index=False))
    if "is_laundering" in df.columns:
        print("\nTransaction-level metrics:\n", evaluate(df, alerts).to_string())
        print("\nRecall by typology:\n", recall_by_typology(df, alerts).to_string())
    if alerts:
        print("\nTop alert:", template_summary(alerts[0], df))


if __name__ == "__main__":
    main()
