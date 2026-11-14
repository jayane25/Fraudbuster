"""FlowTrace investigation console.   Run:  streamlit run app/dashboard.py"""
import json
import os
import sys
import tempfile
import time

import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import aml  # noqa: E402
import consortium  # noqa: E402
import early_warning  # noqa: E402
import scenarios  # noqa: E402
import str_report  # noqa: E402

st.set_page_config(page_title="FlowTrace · AML Graph Intelligence", page_icon="🕸️", layout="wide")
DATA = os.path.join(ROOT, "data")
TYPO_COLOR = {"fan_in": "#7B3FC4", "fan_out": "#E07A1F", "cycle": "#D7263D", "mule_chain": "#1B998B"}
RED, GREY, INK = "#D64933", "#A9B2BE", "#0E1A2B"


# ------------------------------------------------------------------ cached data
@st.cache_data(show_spinner="Building money flow graph and running detectors…")
def run_pipeline(csv_bytes: bytes | None):
    if csv_bytes is None:
        df = aml.load_transactions(os.path.join(DATA, "transactions.csv"))
    else:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as f:
            f.write(csv_bytes)
        df = aml.load_transactions(f.name)
    return df, aml.detect_all(df)


@st.cache_data(show_spinner="Replaying every case transaction by transaction…")
def load_early(_df, key):
    p = os.path.join(DATA, "early_warning.csv")
    return pd.read_csv(p) if key == "bundled" and os.path.exists(p) else early_warning.compute(_df)


@st.cache_data(show_spinner="Running each bank alone, then the consortium…")
def load_consortium(_df, key):
    p = os.path.join(DATA, "consortium.json")
    if key == "bundled" and os.path.exists(p):
        return json.load(open(p, encoding="utf8"))
    return consortium.run(_df)


@st.cache_data
def load_json(name):
    p = os.path.join(DATA, name)
    return json.load(open(p, encoding="utf8")) if os.path.exists(p) else None


@st.cache_data
def load_csv(name):
    p = os.path.join(DATA, name)
    return pd.read_csv(p) if os.path.exists(p) else None


def graph_html(sub: pd.DataFrame, focal=None, color=RED, upto=None, height="440px", grey_roles=()):
    sub = sub.sort_values("ts").reset_index(drop=True)
    net = Network(height=height, width="100%", directed=True, bgcolor="#ffffff", font_color="#222")
    net.barnes_hut(gravity=-5000, spring_length=130)
    shown = sub if upto is None else sub.iloc[:upto]
    for acct in set(sub.src) | set(sub.dst):
        lit = acct in set(shown.src) | set(shown.dst)
        net.add_node(acct, label=acct, size=26 if acct == focal else 15,
                     color=(color if acct == focal else ("#5B6675" if lit else "#E3E7EC")), title=acct)
    for i, r in enumerate(shown.itertuples(index=False), 1):
        decoy = getattr(r, "role", "") in grey_roles
        newest = upto is not None and i == len(shown)
        net.add_edge(r.src, r.dst, label=f"₹{r.amount:,.0f}", title=f"{r.tx_id} · {r.ts:%d %b %H:%M}",
                     color=GREY if decoy else (INK if newest else color), width=4 if newest else 2, arrows="to")
    return net.generate_html()


# ------------------------------------------------------------------ sidebar
st.sidebar.title("🕸️ FlowTrace")
st.sidebar.caption("Graph based detection of layered money laundering networks · PS 2.1")
upload = st.sidebar.file_uploader("Transactions CSV (tx_id, src, dst, amount, ts)", type="csv")
data_key = "upload" if upload else "bundled"
df, alerts = run_pipeline(upload.getvalue() if upload else None)
min_risk = st.sidebar.slider("Minimum risk score", 0, 100, 40)
typos = st.sidebar.multiselect("Patterns", list(aml.TYPOLOGY_LABEL), default=list(aml.TYPOLOGY_LABEL),
                               format_func=aml.TYPOLOGY_LABEL.get)
shown = [a for a in alerts if a.risk >= min_risk and a.typology in typos]
has_llm = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
st.sidebar.caption("Claude case notes: " + ("on" if has_llm else "set ANTHROPIC_API_KEY to enable"))

st.title("Money trail investigation console")
k1, k2, k3, k4 = st.columns(4)
k1.metric("Transactions analysed", f"{len(df):,}")
k2.metric("Accounts", f"{pd.concat([df.src, df.dst]).nunique():,}")
k3.metric("Alerts (filtered)", f"{len(shown)}", f"{sum(a.risk >= 70 for a in shown)} high risk")
k4.metric("Money in flagged flows", f"₹{sum(a.total_amount for a in shown):,.0f}")

tabs = st.tabs(["🚨 Alerts", "🎯 Beat FlowTrace", "⏱️ Early warning", "🏦 Bank consortium", "📊 Evaluation", "🧭 How it works"])

# ================================================================== Alerts + replay + STR
with tabs[0]:
    if not shown:
        st.info("No alerts at this risk level. Lower the minimum risk score in the sidebar.")
    else:
        table = aml.alerts_frame(shown)
        left, right = st.columns([5, 7])
        with left:
            st.subheader("Ranked suspicious clusters")
            st.dataframe(table[["alert_id", "risk", "typology", "accounts", "transactions", "amount"]],
                         hide_index=True, use_container_width=True, height=560,
                         column_config={"risk": st.column_config.ProgressColumn("risk", min_value=0, max_value=100, format="%.0f"),
                                        "amount": st.column_config.NumberColumn("amount (₹)", format="%.0f")})
        with right:
            pick = st.selectbox("Investigate alert", table.alert_id,
                                format_func=lambda i: f"{i} · {next(a for a in shown if a.alert_id == i).label}")
            a = next(x for x in shown if x.alert_id == pick)
            sub = df[df.tx_id.isin(a.tx_ids)].sort_values("ts")
            c1, c2, c3 = st.columns(3)
            c1.metric("Risk", f"{a.risk:.0f}/100")
            c2.metric("Pattern", a.label.split(" (")[0])
            c3.metric("Time span", f"{a.hours:.0f} h")
            n = len(sub)
            focal = a.focal or (a.path[0] if a.path else None)
            rc1, rc2 = st.columns([4, 1])
            step = rc1.slider("Replay the money trail, one transfer at a time", 1, n, n, key=f"replay_{pick}")
            play = rc2.button("▶ Play", use_container_width=True)
            box = st.empty()
            if play:
                for k in range(1, n + 1):
                    with box.container():
                        components.html(graph_html(sub, focal, TYPO_COLOR[a.typology], upto=k), height=460)
                        st.caption(f"Step {k} of {n} · {sub.ts.iloc[k - 1]:%d %b %Y %H:%M}")
                    time.sleep(0.7 if n < 12 else 0.35)
            else:
                with box.container():
                    components.html(graph_html(sub, focal, TYPO_COLOR[a.typology], upto=step), height=460)
                    st.caption(f"Step {step} of {n} · {sub.ts.iloc[step - 1]:%d %b %Y %H:%M}")

        st.subheader(f"Money trail · {a.alert_id}")
        st.markdown(f"**What happened:** {aml.template_summary(a, df)}")
        b1, b2 = st.columns(2)
        note = None
        if has_llm and b2.button("✨ Write investigator note with Claude"):
            with st.spinner("Asking Claude…"):
                note = aml.llm_summary(a, df)
            if note:
                st.success(note)
            else:
                st.warning("Claude note unavailable; the rule based summary is used.")
        _, pdf = str_report.build(a, df, narrative=note)
        b1.download_button("📄 Download STR draft (PDF)", pdf, file_name=f"STR_draft_{a.alert_id}.pdf",
                           mime="application/pdf", type="primary", use_container_width=True)
        with st.expander("Transactions in this alert"):
            st.dataframe(sub[["tx_id", "src", "dst", "amount", "ts"]], hide_index=True, use_container_width=True)

# ================================================================== Beat FlowTrace
with tabs[1]:
    st.subheader("You are the launderer. Try to get money past FlowTrace.")
    st.caption("Design a scheme. We drop it into real background traffic from the dataset and run the full detector on it, live.")
    if "score" not in st.session_state:
        st.session_state.score = {"tries": 0, "caught": 0}
    g1, g2 = st.columns([4, 7])
    with g1:
        with st.form("attack"):
            scheme = st.selectbox("Scheme", list(scenarios.SCHEMES), format_func=scenarios.SCHEMES.get, index=3)
            total = st.number_input("Money to move (₹)", 50_000, 50_000_000, 900_000, step=50_000)
            parties = st.slider("Accounts or hops used", 2, 25, 6)
            gap = st.slider("Hours between steps", 0.25, 96.0, 2.0, step=0.25)
            skim = st.slider("Cut kept by each mule (%)", 0.0, 25.0, 1.0, step=0.5)
            decoys = st.slider("Decoy transfers per account", 0, 15, 0)
            seed = st.number_input("Random seed", 1, 9999, 7)
            go = st.form_submit_button("🚀 Launch scheme", type="primary", use_container_width=True)
        score_box = st.empty()
    with g2:
        if go:
            plan = scenarios.Plan(scheme=scheme, total=float(total), parties=parties, gap_h=gap, skim_pct=skim,
                                  decoys=decoys, seed=int(seed))
            with st.spinner("Hiding your transfers in real traffic and scanning…"):
                r = scenarios.play(df, plan)
            st.session_state.score["tries"] += 1
            st.session_state.score["caught"] += int(r["caught"])
            if r["caught"]:
                al = r["alert"]
                st.error(f"🚨 CAUGHT in {r['seconds']} s. Flagged as **{al.label}** with risk {al.risk:.0f}/100, "
                         f"covering {r['coverage']:.0%} of your transfers, hidden among {r['background_tx']:,} real ones.")
            else:
                st.success(f"😈 You got past FlowTrace this time. {r['reason']}")
            atk = r["attack"]
            hub = atk[atk.role == "scheme"].src.mode().iloc[0]
            components.html(graph_html(atk, hub, RED if r["caught"] else GREY, grey_roles=("decoy",)), height=460)
            st.caption("Red: your scheme. Grey: decoy transfers.")
        else:
            st.info("Pick a scheme on the left and launch it. Try making the mules slower, skimming more, or adding decoys.")
    s = st.session_state.score
    score_box.metric("Judge score", f"{s['tries'] - s['caught']} escaped of {s['tries']}", f"FlowTrace caught {s['caught']}")

    rt = load_csv("redteam.csv")
    if rt is not None:
        st.subheader("We attacked ourselves first")
        st.caption("Automatic red team: each point is 8 schemes injected into real traffic. Detection rate as the launderer gets more careful.")
        names = {"gap_h": "Hours between hops", "skim_pct": "Cut kept per hop (%)", "decoys": "Decoy transfers per account",
                 "parties": "Number of depositors"}
        cols = st.columns(3)
        for i, ((sch, par), g) in enumerate(rt.groupby(["scheme", "param"], sort=False)):
            fig = px.line(g, x="value", y="caught_rate", markers=True, height=260,
                          title=f"{aml.TYPOLOGY_LABEL[sch].split(' (')[0]}: {names.get(par, par)}")
            fig.update_traces(line_color=RED)
            fig.update_layout(yaxis_range=[-0.05, 1.05], yaxis_tickformat=".0%", margin=dict(l=0, r=0, t=40, b=0),
                              xaxis_title=None, yaxis_title="caught")
            cols[i % 3].plotly_chart(fig, use_container_width=True)

# ================================================================== Early warning
with tabs[2]:
    st.subheader("How early do we raise the alarm?")
    st.caption("Every laundering case is replayed transfer by transfer. We record the first moment FlowTrace flags it.")
    if "is_laundering" not in df.columns:
        st.info("Needs a labelled dataset.")
    else:
        ew = load_early(df, data_key)
        got = ew.dropna(subset=["caught_step"])
        e1, e2, e3 = st.columns(3)
        e1.metric("Cases flagged during the replay", f"{len(got)} of {len(ew)}")
        e2.metric("Median share of scheme done when flagged", f"{got.pct_complete.median():.0f}%")
        e3.metric("Money still in motion when flagged", f"₹{got.money_still_moving.sum():,.0f}")
        summ = early_warning.summary(ew).reset_index()
        summ["typology"] = summ.typology.map(lambda t: aml.TYPOLOGY_LABEL.get(t, t))
        st.dataframe(summ.rename(columns={"typology": "pattern", "median_pct_complete": "median % complete when flagged",
                                          "median_hours_early": "median hours before last transfer"}),
                     hide_index=True, use_container_width=True)
        fig = px.histogram(got, x="pct_complete", color="typology", nbins=20, height=320,
                           labels={"pct_complete": "% of the scheme completed when FlowTrace raised the alert"},
                           color_discrete_map=TYPO_COLOR)
        fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), legend_title_text="")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("A round trip can only be confirmed when the loop closes, so cycles are flagged at the final hop by design.")

# ================================================================== Consortium
with tabs[3]:
    st.subheader("One bank sees a fragment. Together, banks see the ring.")
    st.caption("Accounts are spread across three simulated banks. Siloed: each bank runs FlowTrace only on transfers touching its "
               "own customers. Consortium: banks share transfers with account numbers replaced by salted SHA-256 pseudonyms.")
    if "is_laundering" not in df.columns:
        st.info("Needs a labelled dataset.")
    else:
        cs = load_consortium(df, data_key)
        m1, m2, m3 = st.columns(3)
        m1.metric("Laundering cases spanning 2+ banks", f"{cs['cases_spanning_banks_pct']:.0f}%")
        m2.metric("Recall, banks working alone", f"{cs['siloed']['recall']:.0%}")
        m3.metric("Recall, privacy safe consortium", f"{cs['consortium']['recall']:.0%}",
                  f"+{(cs['consortium']['recall'] - cs['siloed']['recall']) * 100:.0f} points")
        rb = pd.DataFrame(cs["recall_by_typology"])
        rb["pattern"] = rb.typology.map(lambda t: aml.TYPOLOGY_LABEL.get(t, t))
        long = rb.melt(id_vars=["pattern"], value_vars=["siloed", "consortium"], var_name="setup", value_name="recall")
        fig = px.bar(long, x="pattern", y="recall", color="setup", barmode="group", text_auto=".0%", height=340,
                     color_discrete_map={"siloed": GREY, "consortium": RED})
        fig.update_layout(yaxis_tickformat=".0%", yaxis_range=[0, 1.1], margin=dict(l=0, r=0, t=10, b=0), legend_title_text="")
        st.plotly_chart(fig, use_container_width=True)
        st.markdown("**What actually leaves the bank:** only pseudonyms, amounts and times. Never names or account numbers.")
        st.dataframe(pd.DataFrame(cs["example"])[["tx_id", "src", "src_shared", "dst", "dst_shared", "amount"]].rename(
            columns={"src": "sender (stays in bank)", "src_shared": "sender (shared)", "dst": "receiver (stays in bank)",
                     "dst_shared": "receiver (shared)"}), hide_index=True, use_container_width=True)
        st.caption("Inspired by BIS Innovation Hub Project Aurora (2023), which found network analysis across institutions "
                   "detected far more laundering than siloed monitoring.")

# ================================================================== Evaluation
with tabs[4]:
    st.subheader("Precision, recall and F1 against a threshold baseline")
    if "is_laundering" in df.columns:
        m = aml.evaluate(df, alerts, min_risk=min_risk)
        st.caption(f"Labelled dataset in use. A transaction counts as flagged if it sits in an alert with risk ≥ {min_risk}. "
                   f"Baseline: amount ≥ {aml.REPORT_THRESHOLD:,} or sender ≥ 10 transfers in a day.")
        st.dataframe(m, use_container_width=True)
        long = m[["precision", "recall", "f1"]].reset_index().melt(id_vars="system", var_name="metric")
        fig = px.bar(long, x="metric", y="value", color="system", barmode="group", text_auto=".2f",
                     color_discrete_sequence=[GREY, RED], height=320)
        fig.update_layout(yaxis_range=[0, 1.05], margin=dict(l=0, r=0, t=10, b=0), legend_title_text="")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(aml.recall_by_typology(df, alerts), use_container_width=True)

    st.subheader("Reality check: the IBM AML public benchmark")
    im = load_json("ibm_model_results.json")
    if im:
        mm = {m["system"]: m for m in im["metrics"]}
        fm, bm = mm["FlowTrace IBM profile"], mm["Threshold baseline"]
        st.caption(f"{im['test_transactions']:,} unseen transactions, only {im['test_laundering']:,} "
                   f"({im['base_rate']:.2%}) laundering. {im['split']}. Each period's graph features are built from that "
                   "period alone, so nothing from the test days leaks into training. Data: Altman et al. 2023, IBM Research.")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("F1 on unseen days", f"{fm['f1']:.2f}", f"baseline {bm['f1']:.3f}", delta_color="off")
        k2.metric("Alerts that are real laundering", f"{fm['precision']:.0%}", f"baseline {bm['precision']:.2%}", delta_color="off")
        k3.metric("Laundering caught", f"{fm['recall']:.0%}", f"baseline {bm['recall']:.0%}", delta_color="off")
        k4.metric("Analyst queue", f"{im['flagged']:,}", f"baseline {im['baseline_flagged']:,}", delta_color="off")
        long = pd.DataFrame(im["metrics"]).melt(id_vars="system", value_vars=["precision", "recall", "f1"], var_name="metric")
        fig = px.bar(long, x="metric", y="value", color="system", barmode="group", text_auto=".2f",
                     color_discrete_sequence=[GREY, RED], height=300)
        fig.update_layout(yaxis_range=[0, 1.05], margin=dict(l=0, r=0, t=10, b=0), legend_title_text="")
        st.plotly_chart(fig, use_container_width=True)
        st.markdown(
            "**How we got here.** Our first run used the synthetic tuned rules unchanged and caught only 8% "
            "(details below). Real launderers wait days between hops, and about 87% of IBM laundering moves over ACH "
            "against about 12% of all traffic. So for real data FlowTrace turns the graph into features (fan in and "
            "fan out breadth, relay behaviour, payment rail mix, second hop reach) and a gradient boosted scorer ranks "
            "every transfer. Trained on earlier days, tested on later days."
        )
    ibm = load_json("ibm_results.json")
    if ibm:
        if im:
            st.markdown("##### First attempt: synthetic tuned rules, no retraining")
        base = ibm["laundering_tx"] / ibm["transactions"]
        mets = {m["system"]: m for m in ibm["metrics"]}
        ft = next(v for k, v in mets.items() if k != "Threshold baseline")
        bl = mets.get("Threshold baseline")

        def flagged(m):
            return m["tp"] + m["fp"]

        for m in mets.values():  # exact precision from counts (json value is rounded to 3 places)
            m["precision"] = m["tp"] / max(flagged(m), 1)

        st.caption(f"{ibm['transactions']:,} real format transactions over {ibm['period']}, of which only "
                   f"{ibm['laundering_tx']:,} ({base:.3%}) are laundering. Same rules as the synthetic run, no retraining. "
                   "Data: Altman et al. 2023, IBM Research.")
        c1, c2, c3 = st.columns(3)
        c1.metric("Alert hit rate vs random", f"{ft['precision'] / base:.1f}×",
                  f"baseline {bl['precision'] / base:.1f}×" if bl else None, delta_color="off")
        if bl:
            c2.metric("Fewer transactions to review", f"{flagged(bl) / max(flagged(ft), 1):.0f}× fewer",
                      f"{flagged(ft):,} vs {flagged(bl):,}", delta_color="off")
        c3.metric("Laundering caught (recall)", f"{ft['recall']:.0%}",
                  f"baseline {bl['recall']:.0%}" if bl else None, delta_color="off")
        st.markdown(
            "**What real data taught us.** Rules tuned on our own synthetic data do not transfer as is. "
            "In IBM's data, launderers wait days between hops (our relay rule allowed 48 hours), move money across "
            "currencies (our rule expected each hop to keep 90 to 100% of the amount), and more than half of the "
            "laundering follows no named pattern. The threshold baseline flags about half of all traffic, so its "
            "recall is not usable in practice. Even untuned, FlowTrace concentrated real laundering into a far smaller "
            "queue, and the IBM profile above is what we built from these lessons."
        )
        with st.expander("Full numbers"):
            st.dataframe(pd.DataFrame(ibm["metrics"]).set_index("system"), use_container_width=True)
            st.dataframe(pd.DataFrame(ibm["by_pattern"]), hide_index=True, use_container_width=True)
    else:
        st.info("Not run yet. Download HI-Small_Trans.csv and HI-Small_Patterns.txt from Kaggle "
                "(ealtman2019/ibm-transactions-for-anti-money-laundering-aml) into data/, then run: python src/ibm.py --days 3")

# ================================================================== How it works
with tabs[5]:
    st.markdown("""
1. **Transaction data**: bank transfers (`tx_id, src, dst, amount, ts`).
2. **Graph construction**: accounts become nodes, every transfer a dated, weighted, directed edge.
3. **Pattern detection**: time windowed detectors for **fan in** (smurfing), **fan out** (dispersion), **round trip cycles**,
   and **mule chains** in two tiers: fast relays within 6 hours, and patient relays within 48 hours that pass on 97 to 100% of the money.
4. **Risk ranking**: each cluster scored 0 to 100 on pattern strength, money at stake, speed and breadth.
5. **Investigation**: replayable money trail, plain words summary (optionally by Claude), and a one click STR draft.
6. **Proof**: precision, recall and F1 against a threshold baseline, a red team of injected attacks, an early warning replay,
   a bank consortium comparison, and the public IBM AML benchmark.
""")
