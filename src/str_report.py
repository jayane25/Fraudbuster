"""One click Suspicious Transaction Report draft for an alert.
Follows the information an STR to FIU-IND carries (reporting entity, accounts, transactions, grounds of suspicion,
narrative). It is a DRAFT for the analyst to review and file through FINnet, not an official filing.
"""
from __future__ import annotations

import datetime as dt
import os

import pandas as pd
from fpdf import FPDF

import aml

# Unicode fonts (needed for the rupee sign), tried in order: Windows, Linux, macOS.
FONT_CANDIDATES = [
    ("C:/Windows/Fonts/calibri.ttf", "C:/Windows/Fonts/calibrib.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
]


def _find_font() -> tuple[str, str] | None:
    return next(((r, b) for r, b in FONT_CANDIDATES if os.path.exists(r) and os.path.exists(b)), None)


class _ReportPDF(FPDF):
    """FPDF that falls back to the built in Helvetica when no Unicode font is installed,
    swapping the few characters Helvetica cannot draw (rupee sign, bullet) for plain text."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        found = _find_font()
        self.unicode_font = found is not None
        if found:
            self.add_font("Cal", "", found[0])
            self.add_font("Cal", "B", found[1])

    def set_font(self, family=None, style="", size=0):
        if family == "Cal" and not self.unicode_font:
            family = "helvetica"
        super().set_font(family, style, size)

    def normalize_text(self, text):
        if not self.unicode_font:
            text = text.replace("₹", "Rs ").replace("•", "-")
        return super().normalize_text(text)

GROUNDS = {
    "fan_in": ["Many unrelated accounts paid one account inside a short window",
               "Amounts are similar and sit just below the reporting limit, consistent with structuring",
               "Collector account received far more counterparties than its usual pattern"],
    "fan_out": ["One account dispersed a large balance to many accounts inside a short window",
                "Rapid break up of funds is consistent with the layering stage"],
    "cycle": ["Funds left an account and returned to it through intermediaries",
              "Each hop kept almost the full amount, so there is no apparent economic purpose",
              "Round tripping can create false turnover to legitimise funds"],
    "mule_chain": ["Funds were forwarded through several accounts within hours of arrival",
                   "Intermediate accounts kept almost nothing, a common sign of money mule accounts",
                   "The chain distances the money from its source"],
}


def roles(a: aml.Alert, sub: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for acct in a.accounts:
        out_amt = sub.loc[sub.src == acct, "amount"].sum()
        in_amt = sub.loc[sub.dst == acct, "amount"].sum()
        if a.typology == "fan_in":
            role = "Collector" if acct == a.focal else "Depositor"
        elif a.typology == "fan_out":
            role = "Disperser" if acct == a.focal else "Receiver"
        elif a.typology == "cycle":
            role = "Origin and return" if a.path and acct == a.path[0] else "Loop intermediary"
        else:
            role = "Origin" if acct == a.path[0] else ("Final receiver" if acct == a.path[-1] else "Suspected mule")
        rows.append(dict(account=acct, role=role, received=round(in_amt, 2), sent=round(out_amt, 2)))
    return pd.DataFrame(rows)


def build(a: aml.Alert, df: pd.DataFrame, narrative: str | None = None, entity: str = "Demo Bank Ltd") -> tuple[str, bytes]:
    sub = df[df.tx_id.isin(a.tx_ids)].sort_values("ts")
    r = roles(a, sub)
    ref = f"STR-DRAFT-{a.alert_id}-{dt.date.today():%Y%m%d}"
    narrative = narrative or aml.template_summary(a, df)

    md = [f"# Suspicious Transaction Report (draft)", f"Reference: {ref}", f"Reporting entity: {entity}",
          f"Prepared: {dt.datetime.now():%d %b %Y %H:%M}", f"Pattern: {a.label}   Risk score: {a.risk:.0f}/100",
          "", "## Grounds of suspicion", *[f"* {g}" for g in GROUNDS[a.typology]],
          "", "## Narrative", narrative, "", "## Accounts", r.to_markdown(index=False) if hasattr(r, "to_markdown") else r.to_string(index=False),
          "", "## Transactions", sub[["tx_id", "src", "dst", "amount", "ts"]].to_string(index=False)]
    markdown = "\n".join(md)

    pdf = _ReportPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()
    W = pdf.w - 2 * pdf.l_margin

    pdf.set_font("Cal", "B", 18)
    pdf.cell(W, 9, "Suspicious Transaction Report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Cal", "", 10)
    pdf.set_text_color(200, 60, 45)
    pdf.cell(W, 5, "DRAFT for analyst review. File the final report through FIU-IND FINnet.", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)

    def kv(k, v):
        pdf.set_font("Cal", "B", 10); pdf.cell(42, 6, k)
        pdf.set_font("Cal", "", 10); pdf.multi_cell(W - 42, 6, str(v), new_x="LMARGIN", new_y="NEXT")

    kv("Reference", ref)
    kv("Reporting entity", entity)
    kv("Prepared on", f"{dt.datetime.now():%d %b %Y %H:%M}")
    kv("Detected pattern", a.label)
    kv("Risk score", f"{a.risk:.0f} / 100")
    kv("Period", f"{a.start:%d %b %Y %H:%M} to {a.end:%d %b %Y %H:%M}")
    kv("Value involved", f"₹{a.total_amount:,.0f}")

    def head(t):
        pdf.ln(3); pdf.set_font("Cal", "B", 13); pdf.cell(W, 7, t, new_x="LMARGIN", new_y="NEXT"); pdf.set_font("Cal", "", 10)

    head("Grounds of suspicion")
    for g in GROUNDS[a.typology]:
        pdf.multi_cell(W, 5.5, f"•  {g}", new_x="LMARGIN", new_y="NEXT")
    head("Narrative")
    pdf.multi_cell(W, 5.5, narrative, new_x="LMARGIN", new_y="NEXT")

    head("Accounts involved")
    with pdf.table(col_widths=(30, 30, 20, 20), text_align=("LEFT", "LEFT", "RIGHT", "RIGHT"), line_height=5.5) as t:
        t.row(["Account", "Role", "Received (₹)", "Sent (₹)"])
        for x in r.itertuples():
            t.row([x.account, x.role, f"{x.received:,.0f}", f"{x.sent:,.0f}"])

    head("Transactions")
    with pdf.table(col_widths=(22, 20, 20, 18, 26), text_align=("LEFT", "LEFT", "LEFT", "RIGHT", "LEFT"), line_height=5.5) as t:
        t.row(["Transaction", "From", "To", "Amount (₹)", "Date and time"])
        for x in sub.itertuples():
            t.row([x.tx_id, x.src, x.dst, f"{x.amount:,.0f}", f"{x.ts:%d %b %Y %H:%M}"])

    head("Recommended action")
    pdf.multi_cell(W, 5.5, "Review customer due diligence for every account above, freeze outgoing transfers pending review where "
                           "policy allows, and file this STR with FIU-IND within the statutory timeline.", new_x="LMARGIN", new_y="NEXT")
    return markdown, bytes(pdf.output())
