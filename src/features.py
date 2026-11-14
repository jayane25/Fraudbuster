"""Features for the Fraudbusters ML model, and plain language "genuine or laundering?" signals for an alert.

Every transaction is described by numbers the model can learn from:
  * the transfer itself: amount, just under the reporting limit, round amount, hour
  * KYC (what a bank knows about each account): account type, how long it has been open, same owner
  * graph behaviour around the transfer: bursts of incoming or outgoing transfers, how similar the amounts are,
    whether the receiver moves the money on within 48 hours (pass through), whether the sender had just been paid,
    whether the same people have paid each other before (recurring, like payroll)
  * HYBRID only: what our rule detectors in aml.py said (in an alert? which shape? its risk) and the classic
    threshold rule flags. This is how the rule based model and the ML model are combined.

History features only look backwards in time. The two pass through features look 48 hours ahead, so a transfer
is scored once 48 hours have passed (the early warning replay shows most schemes are still caught in time).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import aml

KINDS = {"individual": 0, "business": 1, "merchant": 2, "ngo": 3}
KIND_NAME = {v: k for k, v in KINDS.items()}
TYPO_CODE = {"fan_in": 1, "fan_out": 2, "cycle": 3, "mule_chain": 4}
BIG = 10**10            # account code * BIG + seconds gives one sortable key per (account, time)
H = 3600

GRAPH_FEATURES = [
    "log_amount", "near_limit", "round_amount", "hour",
    "src_kind", "dst_kind", "src_age_days", "dst_age_days", "same_owner",
    "dst_in_72h", "dst_in_72h_cv", "src_out_72h", "src_in_72h",
    "dst_forward_48h", "dst_next_out_h", "src_paid_before_48h",
    "pair_before", "reverse_before", "dst_senders_so_far", "src_payees_so_far",
]
RULE_FEATURES = [
    "rule_hit", "rule_shape", "rule_risk", "rule_structure", "src_in_alert", "dst_in_alert",
    "over_limit", "busy_sender",
]
HYBRID_FEATURES = GRAPH_FEATURES + RULE_FEATURES

LABELS = {
    "log_amount": "Amount", "near_limit": "Just under ₹10,000 limit", "round_amount": "Round amount",
    "hour": "Hour of day", "src_kind": "Sender account type", "dst_kind": "Receiver account type",
    "src_age_days": "Sender account age", "dst_age_days": "Receiver account age", "same_owner": "Same owner",
    "dst_in_72h": "Receiver: transfers in last 72 h", "dst_in_72h_cv": "Receiver: how different the amounts are",
    "src_out_72h": "Sender: payments in last 72 h", "src_in_72h": "Sender: money received in last 72 h",
    "dst_forward_48h": "Receiver moves money on within 48 h", "dst_next_out_h": "Hours until receiver pays out",
    "src_paid_before_48h": "Sender was just paid (pass through)", "pair_before": "Same pair paid before (recurring)",
    "reverse_before": "Receiver has paid the sender before", "dst_senders_so_far": "Receiver: distinct senders",
    "src_payees_so_far": "Sender: distinct payees", "rule_hit": "Rule detector alert", "rule_shape": "Rule shape",
    "rule_risk": "Rule risk score", "rule_structure": "Rule pattern strength", "src_in_alert": "Sender in a rule alert",
    "dst_in_alert": "Receiver in a rule alert", "over_limit": "Threshold rule: ≥ ₹10,000",
    "busy_sender": "Threshold rule: 10+ a day",
}


def load_accounts(path: str) -> pd.DataFrame:
    acc = pd.read_csv(path)
    acc["opened"] = pd.to_datetime(acc["opened"])
    return acc.set_index("account")


class _Index:
    """Sorted (account, time) keys with running sums, so 'how much did X send between t1 and t2' is two binary searches."""

    def __init__(self, acct: np.ndarray, secs: np.ndarray, amt: np.ndarray):
        key = acct.astype(np.int64) * BIG + secs
        order = np.argsort(key, kind="stable")
        self.key = key[order]
        a = amt[order]
        self.cs = np.r_[0.0, np.cumsum(a)]
        self.cs2 = np.r_[0.0, np.cumsum(a * a)]

    def window(self, acct, lo, hi, lo_incl=True, hi_incl=True):
        base = acct.astype(np.int64) * BIG
        i = np.searchsorted(self.key, base + lo, "left" if lo_incl else "right")
        j = np.searchsorted(self.key, base + hi, "right" if hi_incl else "left")
        return j - i, self.cs[j] - self.cs[i], self.cs2[j] - self.cs2[i]

    def next_after(self, acct, t):
        base = acct.astype(np.int64) * BIG
        i = np.searchsorted(self.key, base + t, "right")
        nxt = self.key[np.minimum(i, len(self.key) - 1)]
        ok = (i < len(self.key)) & (nxt < base + BIG)
        return np.where(ok, nxt - base - t, np.nan)


def graph_features(df: pd.DataFrame, accounts: pd.DataFrame) -> pd.DataFrame:
    """Graph + KYC features for every row of df (df = all history available, sorted by time)."""
    d = df[["tx_id", "src", "dst", "amount", "ts"]].reset_index(drop=True)
    codes, _ = pd.factorize(pd.concat([d.src, d.dst]), sort=True)
    s, r = codes[: len(d)], codes[len(d):]
    secs = (d.ts.astype("int64") // 10**9).to_numpy()
    amt = d.amount.to_numpy(float)
    out_idx, in_idx = _Index(s, secs, amt), _Index(r, secs, amt)

    X = pd.DataFrame(index=d.index)
    X["log_amount"] = np.log1p(amt)
    X["near_limit"] = ((amt >= 0.8 * aml.REPORT_THRESHOLD) & (amt < aml.REPORT_THRESHOLD)).astype(int)
    X["round_amount"] = (np.mod(amt, 100) == 0).astype(int)
    X["hour"] = d.ts.dt.hour

    kind = accounts["kind"].map(KINDS)
    X["src_kind"] = d.src.map(kind).fillna(0).astype(int).to_numpy()
    X["dst_kind"] = d.dst.map(kind).fillna(0).astype(int).to_numpy()
    X["src_age_days"] = (d.ts - d.src.map(accounts["opened"])).dt.days.fillna(3650).to_numpy()
    X["dst_age_days"] = (d.ts - d.dst.map(accounts["opened"])).dt.days.fillna(3650).to_numpy()
    X["same_owner"] = (d.src.map(accounts["holder"]) == d.dst.map(accounts["holder"])).astype(int).to_numpy()

    n, sm, sq = in_idx.window(r, secs - 72 * H, secs)                      # receiver's incoming burst
    X["dst_in_72h"] = n
    mean = sm / np.maximum(n, 1)
    X["dst_in_72h_cv"] = np.sqrt(np.maximum(sq / np.maximum(n, 1) - mean**2, 0)) / np.maximum(mean, 1)
    X["src_out_72h"] = out_idx.window(s, secs - 72 * H, secs)[0]           # sender's outgoing burst
    X["src_in_72h"] = in_idx.window(s, secs - 72 * H, secs)[0]
    fwd = out_idx.window(r, secs, secs + 48 * H, lo_incl=False)[1]          # receiver passes money on?
    X["dst_forward_48h"] = np.clip(fwd / np.maximum(amt, 1), 0, 5)
    X["dst_next_out_h"] = np.nan_to_num(out_idx.next_after(r, secs) / H, nan=24 * 30).clip(0, 24 * 30)
    back = in_idx.window(s, secs - 48 * H, secs, hi_incl=False)[1]          # sender had just been paid?
    X["src_paid_before_48h"] = np.clip(back / np.maximum(amt, 1), 0, 5)

    pair = s.astype(np.int64) * (codes.max() + 1) + r
    rev = r.astype(np.int64) * (codes.max() + 1) + s
    pair_idx = _Index(pair, secs, amt)
    X["pair_before"] = pair_idx.window(pair, 0, secs, hi_incl=False)[0]
    X["reverse_before"] = pair_idx.window(rev, 0, secs, hi_incl=False)[0]
    first = ~d.duplicated(["src", "dst"])
    X["dst_senders_so_far"] = first.groupby(d.dst).cumsum().to_numpy()
    X["src_payees_so_far"] = first.groupby(d.src).cumsum().to_numpy()
    X.index = df.index
    return X


def rule_features(df: pd.DataFrame, alerts: list[aml.Alert]) -> pd.DataFrame:
    """What the rule based model says about each transaction."""
    best: dict[str, aml.Alert] = {}
    accts: set[str] = set()
    for a in alerts:
        accts.update(a.accounts)
        for t in a.tx_ids:
            if t not in best or a.risk > best[t].risk:
                best[t] = a
    hit = df.tx_id.map(best)
    X = pd.DataFrame(index=df.index)
    X["rule_hit"] = hit.notna().astype(int)
    X["rule_shape"] = hit.map(lambda a: TYPO_CODE[a.typology] if isinstance(a, aml.Alert) else 0)
    X["rule_risk"] = hit.map(lambda a: a.risk if isinstance(a, aml.Alert) else 0.0)
    X["rule_structure"] = hit.map(lambda a: a.structure if isinstance(a, aml.Alert) else 0.0)
    X["src_in_alert"] = df.src.isin(accts).astype(int)
    X["dst_in_alert"] = df.dst.isin(accts).astype(int)
    X["over_limit"] = (df.amount >= aml.REPORT_THRESHOLD).astype(int)
    day = df.ts.dt.date
    X["busy_sender"] = (df.groupby([df.src, day]).tx_id.transform("count") >= 10).astype(int)
    return X


def build(df: pd.DataFrame, accounts: pd.DataFrame, alerts: list[aml.Alert] | None = None) -> pd.DataFrame:
    """All features (graph + rule). Runs the rule detectors if alerts are not given."""
    alerts = aml.detect_all(df) if alerts is None else alerts
    return pd.concat([graph_features(df, accounts), rule_features(df, alerts)], axis=1)


# ------------------------------------------------------------------ explanations for one alert
def signals(a: aml.Alert, df: pd.DataFrame, accounts: pd.DataFrame, X: pd.DataFrame) -> list[dict]:
    """Plain language evidence for and against laundering. Each item: text + side ('genuine' or 'laundering')."""
    sub = df[df.tx_id.isin(a.tx_ids)]
    f = X.loc[sub.index]
    out = []

    def add(side, text):
        out.append({"side": side, "text": text})

    key = a.focal or (a.path[0] if a.path else a.accounts[0])
    if key in accounts.index:
        k, opened = accounts.at[key, "kind"], accounts.at[key, "opened"]
        age = (a.start - opened).days
        name = {"ngo": "a registered NGO", "business": "a registered business", "merchant": "a registered merchant",
                "individual": "a personal account"}[k]
        if k != "individual" and age >= 365:
            add("genuine", f"{key} is {name}, open for {age // 365} years")
        elif age < 180:
            add("laundering", f"{key} is {name} opened only {age} days earlier")
        elif k == "individual" and a.typology in ("fan_in", "fan_out"):
            add("laundering", f"{key} is a personal account, not a registered business or charity, "
                              f"yet it {'collects from' if a.typology == 'fan_in' else 'pays out to'} many people")
        else:
            add("genuine", f"{key} is {name}, open for {age // 30} months")

    young = np.mean([(a.start - accounts.at[x, "opened"]).days < 180 for x in a.accounts if x in accounts.index])
    if young >= 0.4:
        add("laundering", f"{young:.0%} of the accounts involved were opened in the last 6 months")
    elif young <= 0.15:
        add("genuine", "Almost all accounts involved are long standing customers")

    if a.typology == "fan_in":
        recv = sub.amount.sum()
        out48 = df[(df.src == a.focal) & (df.ts > a.end) & (df.ts <= a.end + pd.Timedelta(hours=48))]
        moved = out48.amount.sum()
        share = min(moved / max(recv, 1), 1.0)
        back = out48[out48.dst.isin(sub.src)].amount.sum() / max(moved, 1)
        if share >= 0.6 and back >= 0.6:
            add("genuine", f"The money went back to someone who had paid in: a rotating savings pool (chit fund), "
                           f"not an exit to new accounts")
        elif share >= 0.6:
            add("laundering", f"{share:.0%} of the money collected left {a.focal} within 48 hours")
        else:
            add("genuine", f"{a.focal} kept the money: " + ("none of it" if share < 0.005 else f"only {share:.0%}")
                + " moved on within 48 hours")
    if a.typology == "fan_out":
        before = df[(df.dst == a.focal) & (df.ts < a.start) & (df.ts >= a.start - pd.Timedelta(hours=48))]
        if before.amount.sum() >= 0.6 * sub.amount.sum() and before.src.nunique() <= 2:
            n = before.src.nunique()
            add("laundering", f"{a.focal} received the whole sum from {n} account{'s' if n > 1 else ''} just before splitting it")
        else:
            add("genuine", f"{a.focal} built up its balance over time, not from one sudden deposit")
        relay = (f["dst_forward_48h"] >= 0.8).mean()
        if relay >= 0.3:
            add("laundering", f"{relay:.0%} of the receivers passed the money on within 48 hours")
        else:
            add("genuine", "Receivers kept the money (did not pass it on)")
    if a.typology in ("mule_chain", "cycle"):
        owners = {accounts.at[x, "holder"] for x in a.accounts if x in accounts.index}
        if len(owners) == 1:
            add("genuine", "Every account in the chain belongs to the same customer (moving their own money)")
        else:
            add("laundering", f"Money passed through {len(owners)} different customers almost untouched")

    if (f["pair_before"] > 0).mean() >= 0.5:
        add("genuine", "The same people have paid each other before (recurring, like salary or a chit fund)")
    if f["near_limit"].mean() >= 0.5:
        add("laundering", "Most amounts sit just under the ₹10,000 reporting limit")
    elif f["round_amount"].mean() >= 0.7 and a.typology == "fan_in":
        add("genuine", "Amounts are round figures, typical of donations, ticket prices, fees or contributions")
    return out
