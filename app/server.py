"""FlowTrace Investigator Workspace: API + web app.

Run:  .venv/Scripts/python -m uvicorn app.server:app --port 8600     (from aml-graph-detector/)
Open: http://localhost:8600

Reuses the detection engine in src/ (aml.py, str_report.py). Case state (status, notes, frozen accounts,
audit trail) is kept in data/cases.json so it survives restarts.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import threading
from collections import defaultdict

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import aml  # noqa: E402
import str_report  # noqa: E402

DATA = os.path.join(ROOT, "data")
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
CASES_PATH = os.path.join(DATA, "cases.json")
STATUSES = ["New", "Investigating", "Escalated", "Reported", "Closed"]
ANALYSTS = ["Ini", "Jayane", "Deekshith"]

# ------------------------------------------------------------------ load once
DF = aml.load_transactions(os.path.join(DATA, "transactions.csv"))
ALERTS = aml.detect_all(DF)
BY_ID = {a.alert_id: a for a in ALERTS}
TX = DF.set_index("tx_id")
FLAGGED_TX = {t: a.alert_id for a in ALERTS for t in a.tx_ids}
ACCT_ALERTS: dict[str, list[str]] = defaultdict(list)
for a in ALERTS:
    for acct in a.accounts:
        ACCT_ALERTS[acct].append(a.alert_id)
OUT = {k: g.sort_values("ts") for k, g in DF.groupby("src")}
INN = {k: g.sort_values("ts") for k, g in DF.groupby("dst")}
TYPO_LABEL = aml.TYPOLOGY_LABEL
EMPTY = DF.iloc[0:0]

_lock = threading.Lock()


def _load_cases() -> dict:
    if os.path.exists(CASES_PATH):
        with open(CASES_PATH, encoding="utf8") as f:
            return json.load(f)
    return {}


CASES = _load_cases()


def _now() -> str:
    return dt.datetime.now().strftime("%d %b %H:%M")


def case(aid: str) -> dict:
    return CASES.get(aid) or {"status": "New", "assignee": None, "notes": [], "frozen": [], "log": []}


def _save():
    tmp = CASES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf8") as f:
        json.dump(CASES, f, indent=1)
    os.replace(tmp, CASES_PATH)


def frozen_accounts() -> set:
    return {x for c in CASES.values() for x in c.get("frozen", [])}


# ------------------------------------------------------------------ serialisers
def tx_row(r) -> dict:
    return {"tx_id": r.tx_id, "src": r.src, "dst": r.dst, "amount": round(float(r.amount), 2),
            "ts": pd.Timestamp(r.ts).isoformat(), "alert": FLAGGED_TX.get(r.tx_id)}


def alert_brief(a: aml.Alert) -> dict:
    c = case(a.alert_id)
    return {"id": a.alert_id, "typology": a.typology, "label": TYPO_LABEL[a.typology], "risk": a.risk,
            "accounts": len(a.accounts), "tx": len(a.tx_ids), "amount": round(a.total_amount, 2),
            "start": a.start.isoformat(), "end": a.end.isoformat(), "hours": round(a.hours, 1),
            "focal": a.focal or (a.path[0] if a.path else a.accounts[0]),
            "status": c["status"], "assignee": c["assignee"], "notes": len(c["notes"])}


def profile(acct: str) -> dict:
    o = OUT.get(acct, EMPTY); i = INN.get(acct, EMPTY)
    both = pd.concat([o, i])
    return {"account": acct, "sent": round(float(o.amount.sum()), 2), "received": round(float(i.amount.sum()), 2),
            "out_tx": int(len(o)), "in_tx": int(len(i)), "payees": int(o.dst.nunique()), "payers": int(i.src.nunique()),
            "first": both.ts.min().isoformat() if len(both) else None, "last": both.ts.max().isoformat() if len(both) else None,
            "alerts": ACCT_ALERTS.get(acct, []), "frozen": acct in frozen_accounts()}


# ------------------------------------------------------------------ app
app = FastAPI(title="FlowTrace Investigator Workspace")


@app.get("/api/meta")
def meta():
    return {"statuses": STATUSES, "analysts": ANALYSTS, "typologies": TYPO_LABEL,
            "transactions": int(len(DF)), "accounts": int(pd.concat([DF.src, DF.dst]).nunique()),
            "period": [DF.ts.min().isoformat(), DF.ts.max().isoformat()]}


@app.get("/api/alerts")
def alerts():
    return [alert_brief(a) for a in ALERTS]


@app.get("/api/alerts/{aid}")
def alert_detail(aid: str):
    a = BY_ID.get(aid)
    if not a:
        raise HTTPException(404, "No such alert")
    sub = DF[DF.tx_id.isin(a.tx_ids)].sort_values("ts")
    roles = str_report.roles(a, sub)
    fz = frozen_accounts()
    nodes = [{"id": r.account, "role": r.role, "received": r.received, "sent": r.sent, "frozen": r.account in fz,
              "alerts": len(ACCT_ALERTS.get(r.account, []))} for r in roles.itertuples()]
    return {**alert_brief(a), "path": a.path, "structure": round(a.structure, 2),
            "summary": aml.template_summary(a, DF), "grounds": str_report.GROUNDS[a.typology],
            "transactions": [tx_row(r) for r in sub.itertuples()], "nodes": nodes, "case": case(aid)}


@app.get("/api/accounts/{acct}")
def account(acct: str, limit: int = 14):
    if acct not in OUT and acct not in INN:
        raise HTTPException(404, "Unknown account")
    rows = pd.concat([OUT.get(acct, EMPTY), INN.get(acct, EMPTY)])
    flagged = rows[rows.tx_id.isin(FLAGGED_TX)]
    top = rows[~rows.tx_id.isin(FLAGGED_TX)].nlargest(max(limit - len(flagged), 4), "amount")
    show = pd.concat([flagged, top]).drop_duplicates("tx_id").sort_values("ts")
    return {**profile(acct), "transactions": [tx_row(r) for r in show.itertuples()],
            "shown": int(len(show)), "total": int(len(rows))}


@app.get("/api/search")
def search(q: str):
    q = q.strip().upper()
    if not q:
        return []
    hits = [{"type": "alert", "id": a.alert_id, "label": f"{a.alert_id} · {TYPO_LABEL[a.typology]}"}
            for a in ALERTS if q in a.alert_id][:5]
    accts = sorted({x for x in list(OUT) + list(INN) if q in x.upper()})[:8]
    hits += [{"type": "account", "id": x, "label": x} for x in accts]
    return hits


@app.get("/api/trace")
def trace(acct: str, since: str | None = None, direction: str = "out", hops: int = 4, window_h: float = 72,
          min_share: float = 0.5):
    """Follow the money: from acct, take transfers after `since` within window_h hours that carry at least
    min_share of the amount that arrived, hop by hop. direction=in walks backwards towards the source."""
    t0 = pd.Timestamp(since) if since else (DF.ts.min() if direction == "out" else DF.ts.max())
    frontier = [(acct, t0, None)]
    edges, seen = [], {acct}
    for _ in range(max(1, min(hops, 6))):
        nxt = []
        for node, t, amt in frontier:
            g = (OUT if direction == "out" else INN).get(node)
            if g is None:
                continue
            if direction == "out":
                g = g[(g.ts >= t) & (g.ts <= t + pd.Timedelta(hours=window_h))]
            else:
                g = g[(g.ts <= t) & (g.ts >= t - pd.Timedelta(hours=window_h))]
            if amt:
                g = g[g.amount >= amt * min_share]
            for r in g.nlargest(4, "amount").itertuples():
                edges.append(tx_row(r))
                other = r.dst if direction == "out" else r.src
                if other not in seen:
                    seen.add(other)
                    nxt.append((other, r.ts, r.amount))
        frontier = nxt
        if not frontier:
            break
    return {"start": acct, "direction": direction, "transactions": edges, "accounts": sorted(seen)}


class CaseUpdate(BaseModel):
    status: str | None = None
    assignee: str | None = None
    note: str | None = None
    freeze: str | None = None
    unfreeze: str | None = None
    by: str | None = None


@app.post("/api/cases/{aid}")
def update_case(aid: str, u: CaseUpdate):
    if aid not in BY_ID:
        raise HTTPException(404, "No such alert")
    who = u.by or "Analyst"
    with _lock:
        c = case(aid)
        if u.status and u.status in STATUSES and u.status != c["status"]:
            c["log"].append({"t": _now(), "by": who, "event": f"Status {c['status']} to {u.status}"})
            c["status"] = u.status
        if u.assignee is not None and u.assignee != (c["assignee"] or ""):
            c["assignee"] = u.assignee or None
            c["log"].append({"t": _now(), "by": who, "event": f"Assigned to {u.assignee}" if u.assignee else "Unassigned"})
        if u.note and u.note.strip():
            c["notes"].append({"t": _now(), "by": who, "text": u.note.strip()[:2000]})
            c["log"].append({"t": _now(), "by": who, "event": "Note added"})
        if u.freeze and u.freeze not in c["frozen"]:
            c["frozen"].append(u.freeze)
            c["log"].append({"t": _now(), "by": who, "event": f"Freeze requested on {u.freeze}"})
            if c["status"] == "New":
                c["status"] = "Investigating"
        if u.unfreeze and u.unfreeze in c["frozen"]:
            c["frozen"].remove(u.unfreeze)
            c["log"].append({"t": _now(), "by": who, "event": f"Freeze lifted on {u.unfreeze}"})
        CASES[aid] = c
        _save()
    return c


@app.post("/api/reset")
def reset_cases():
    with _lock:
        CASES.clear()
        _save()
    return {"ok": True}


@app.get("/api/alerts/{aid}/str.pdf")
def str_pdf(aid: str):
    a = BY_ID.get(aid)
    if not a:
        raise HTTPException(404, "No such alert")
    _, pdf = str_report.build(a, DF)
    with _lock:
        c = case(aid)
        c["log"].append({"t": _now(), "by": "Analyst", "event": "STR draft generated"})
        CASES[aid] = c
        _save()
    return Response(bytes(pdf), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="STR_{aid}.pdf"'})


def _json(name):
    p = os.path.join(DATA, name)
    if os.path.exists(p):
        with open(p, encoding="utf8") as f:
            return json.load(f)
    return None


@app.get("/api/overview")
def overview():
    briefs = [alert_brief(a) for a in ALERTS]
    by_typ = defaultdict(lambda: {"alerts": 0, "amount": 0.0})
    for b in briefs:
        by_typ[b["label"]]["alerts"] += 1
        by_typ[b["label"]]["amount"] += b["amount"]
    by_day = defaultdict(int)
    for a in ALERTS:
        by_day[a.start.strftime("%Y-%m-%d")] += 1
    status = {s: sum(1 for b in briefs if b["status"] == s) for s in STATUSES}
    bands = {"Critical 80+": 0, "High 60 to 79": 0, "Medium 40 to 59": 0, "Low under 40": 0}
    for b in briefs:
        r = b["risk"]
        k = "Critical 80+" if r >= 80 else "High 60 to 79" if r >= 60 else "Medium 40 to 59" if r >= 40 else "Low under 40"
        bands[k] += 1
    return {"alerts": len(briefs), "money": round(sum(b["amount"] for b in briefs), 2),
            "frozen": len(frozen_accounts()), "status": status, "by_typology": by_typ,
            "by_day": dict(sorted(by_day.items())), "bands": bands,
            "top": sorted(briefs, key=lambda b: -b["risk"])[:6]}


@app.get("/api/metrics")
def metrics():
    lab = "is_laundering" in DF
    return {"synthetic": aml.evaluate(DF, ALERTS).reset_index().to_dict("records") if lab else None,
            "by_typology": aml.recall_by_typology(DF, ALERTS).reset_index().to_dict("records") if lab else None,
            "ibm_model": _json("ibm_model_results.json"), "ibm_rules": _json("ibm_results.json"),
            "consortium": _json("consortium.json")}


app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"))
