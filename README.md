# Fraudbusters — Graph-Based Detection of Layered Money Laundering Networks (PS 2.1)

**Team Yenrimedia · App: Fraudbusters · Build Beyond '26, BMSCE · PS 2.1** · Slides: [docs/FlowTrace_v4_IBM.pptx](docs/FlowTrace_v4_IBM.pptx)

Transactions → temporal directed weighted graph → typology detectors → risk-ranked clusters → plain-language money trail.

## Run

```bash
cd aml-graph-detector
.venv/Scripts/python data/generate_synthetic.py      # labelled dataset -> data/transactions.csv
.venv/Scripts/python src/aml.py                      # CLI: alerts + precision/recall/F1 vs baseline
.venv/Scripts/streamlit run app/dashboard.py         # investigation dashboard on http://localhost:8501
```

**Investigator Workspace (main app):** a full web app on the same engine.

```bash
.venv/Scripts/python -m pip install fastapi "uvicorn[standard]"
.venv/Scripts/python -m uvicorn app.server:app --port 8600   # open http://localhost:8600
```

Cases (alert queue, interactive money trail graph, replay, account inspector, follow the money forward and back,
freeze requests, notes, audit trail, STR PDF), Board (drag cases between statuses), Overview, Evidence (synthetic + IBM).
Case state is saved in `data/cases.json`; `POST /api/reset` clears it. Keys: J / K next and previous case, Space replay, F freeze, S STR.

Optional: set `ANTHROPIC_API_KEY` to get Claude-written investigator notes in the dashboard.

Precompute the proof tabs (the dashboard reads these, and computes them itself if missing):

```bash
.venv/Scripts/python src/scenarios.py       # red team sweep      -> data/redteam.csv
.venv/Scripts/python src/early_warning.py   # early warning replay -> data/early_warning.csv
.venv/Scripts/python src/consortium.py      # silo vs consortium   -> data/consortium.json
.venv/Scripts/python src/ibm.py --days 3    # IBM AML benchmark, rules only -> data/ibm_results.json (needs Kaggle files in data/)
.venv/Scripts/python src/ibm_model.py       # IBM profile: graph features + scorer -> data/ibm_model_results.json (~90 s)
```

IBM results (HI-Small, trained 1 to 6 Sep, tested on unseen 8 to 10 Sep, 1.32M tx, 0.11% laundering):

| System | Precision | Recall | F1 | Flagged |
|---|---|---|---|---|
| Threshold baseline | 0.0015 | 0.57 | 0.003 | 560,549 |
| Fraudbusters rules only, untuned (first 3 days) | 0.0025 | 0.08 | 0.005 | 37,552 |
| Fraudbusters IBM profile | 0.42 | 0.43 | 0.42 | 1,556 |

Dashboard tabs: Alerts (replay + STR PDF), Beat Fraudbusters (live game + red team), Early warning, Bank consortium, Evaluation (synthetic + IBM), How it works.

## Layout

| Path | What |
|---|---|
| `data/generate_synthetic.py` | Normal traffic + injected fan-in, fan-out, cycle and mule-chain cases (labelled) |
| `src/aml.py` | Graph build, 4 detectors, risk scoring, threshold baseline, evaluation, summaries |
| `app/dashboard.py` | Streamlit UI: ranked alerts, interactive money-trail graph, metrics vs baseline |

## Detectors

| Typology | Rule (see constants at top of `src/aml.py`) |
|---|---|
| Fan-in (structuring) | ≥ 8 distinct senders into one account inside 72 h, similar / just-under-limit amounts |
| Fan-out (dispersion) | ≥ 8 distinct receivers from one account inside 72 h, large total |
| Cyclic round-trip | Money returns to origin in 3–6 hops, each hop ≤ 48 h, 90–100 % of amount kept |
| Mule chain | ≥ 3 hops, each forwarded ≤ 6 h after arrival, 90–100 % of amount kept |

Risk (0–100) = typology weight × (0.40 pattern strength + 0.25 money at stake + 0.20 speed + 0.15 breadth).

## Current results (synthetic, seed 7, 41,564 tx / 1,650 laundering)

| System | Precision | Recall | F1 |
|---|---|---|---|
| Threshold baseline (amount ≥ ₹10k or ≥ 10 tx/day) | 0.41 | 0.49 | 0.45 |
| Fraudbusters | 0.86 | 0.93 | 0.90 |

## Next steps

- Validate on a public labelled set (IBM "Transactions for Anti Money Laundering" on Kaggle) — synthetic data matches our own assumptions, so real data is the honest test.
- Merge overlapping alerts into multi-typology clusters; tune thresholds per dataset.
