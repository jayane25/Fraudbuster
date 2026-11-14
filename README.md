# Fraudbusters

**Graph based detection of layered money laundering networks**
Team **Yenrimedia** · Build Beyond '26, BMSCE · Problem statement **PS 2.1**
Ini Shetty · Jayane Muralimaran · Deekshith R

![Fraudbusters Investigator Workspace](docs/screenshot.png)

## The problem

Launderers hide money by *layering* it: splitting it into many small deposits, passing it through mule
accounts, or sending it round in loops. Each single transfer looks harmless, so rule engines that check one
transaction at a time ("is it above ₹10,000?") miss the scheme. The crime is only visible in the **connections**.

**The harder part: genuine activity has the same shapes.** A donation drive looks like smurfing (many → one),
payroll and relief payments look like dispersion (one → many), a chit fund pays a pooled pot onward, and a customer
moving money through their own accounts looks like a mule chain. Shape detection alone drowns investigators in
false alarms. Fraudbusters separates the two with a hybrid ML model and explains every verdict.

## Our approach

**Step 1: rule based shape detectors (graph).**

Every account is a node and every transfer is a time stamped edge in a **directed graph**. Laundering
typologies become shapes we can search for:

| Typology | Shape | Rule (constants at the top of [`src/aml.py`](src/aml.py)) |
|---|---|---|
| Fan in (smurfing / structuring) | many → one | 8 or more distinct senders into one account within 72 h, similar or just under limit amounts |
| Fan out (dispersion) | one → many | 8 or more distinct receivers from one account within 72 h, large total |
| Cycle (round tripping) | loop | money returns to its origin in 3 to 6 hops, each hop within 48 h, 90 to 100 % of the amount kept |
| Mule chain | line | 3 or more hops, each forwarded within 6 h, 90 to 100 % kept (plus a "patient mule" tier: 48 h hops, 97 % kept, 4 or more hops) |

Every alert gets a **risk score from 0 to 100**:
typology weight × (0.40 pattern strength + 0.25 money at stake + 0.20 speed + 0.15 breadth).

**Step 2: context features (graph + KYC)** in [`src/features.py`](src/features.py), 20 per transfer:
account type (NGO, business, merchant, personal), account age, same owner, bursts in the last 72 h, how similar the
amounts are, **does the receiver move the money on within 48 h (pass through)**, was the sender just paid,
**have these two paid each other before (recurring)**, and reciprocity.

**Step 3: hybrid ML model** in [`src/train_model.py`](src/train_model.py): a **Random Forest** (300 decision trees
that vote) learns from the 20 context features **plus 8 rule features** (did a rule fire, which shape, its risk and
strength, accounts in any alert, threshold rule flags). The rule engine's answers become inputs to the ML model.

**Step 4: explainable verdict.** Every alert gets *Likely laundering* or *Likely genuine* with plain English
evidence, for example ✓ "registered NGO, open for 8 years", ✓ "kept the money: none of it moved on within 48 hours",
✓ "every account belongs to the same customer", ⚠ "100% of the money collected left within 48 hours".

```
transactions + KYC → graph → 4 rule detectors ─┐
                         → 20 context features ─┴→ hybrid Random Forest → verdict + evidence → Investigator Workspace
```

### Train and test data

Split by **date**, never shuffled (a bank trains on the past and runs on the future):

| Set | Period | File | Transfers | Laundering |
|---|---|---|---|---|
| Train | 1 Jan to 28 Feb 2026 | `data/train.csv` | 31,480 | 1,884 |
| Validation | 15 to 28 Feb (inside train, picks the alert threshold only) | | | |
| Test | 1 to 31 Mar 2026, never seen in training | `data/test.csv` | 15,937 | 671 |

Train features are built from train data only. Retrain: `.venv/Scripts/python src/train_model.py` (about 30 s), which
writes the two files, `models/fraudbusters_model.joblib` and `data/ml_results.json`.

## Run it

**Windows:** double click `run.bat` (or run it from a terminal). It sets everything up the first time and opens
http://localhost:8600.
**Mac / Linux:** `./run.sh`

Manual steps, if you prefer (Python 3.11 or newer):

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt        # Mac/Linux: .venv/bin/python
.venv/Scripts/python -m uvicorn app.server:app --port 8600     # open http://localhost:8600
```

Run the tests: `.venv/Scripts/python -m pytest tests`

### Guided code tour (for presenting the code)

Install the **CodeTour** extension (VS Code recommends it when you open the folder). Then open the **CodeTour**
panel at the bottom of the Explorer sidebar and click ▶ next to *Fraudbusters: code walkthrough for the judges*.
It walks through 22 steps, jumping to each important line with an explanation and buttons that run the code.

### Run it in VS Code

1. **File > Open Folder…** and pick this folder. Install the recommended **Python** extension when VS Code asks.
2. First time on a new laptop: **Terminal > Run Task… > Setup: create .venv and install packages** (or double click `run.bat` once).
3. Open **Run and Debug** (Ctrl+Shift+D), choose a configuration, press **F5**:

| Configuration | What it runs |
|---|---|
| 1. Fraudbusters Workspace | the web app; the browser opens http://localhost:8600 |
| 2. Detection engine in the terminal | `src/aml.py`: prints the alerts and precision / recall / F1 vs the baseline |
| 3. Research dashboard | the Streamlit dashboard on http://localhost:8501 |
| 4. Regenerate synthetic data | `data/generate_synthetic.py` |
| 5. IBM model | `src/ibm_model.py` (needs the Kaggle files) |
| 6. Run all tests | the 21 tests in `tests/` |
| 7. Train and test the ML model | `src/train_model.py`: train / test split, Random Forest, hybrid, results |

Tip: click left of a line number in `src/aml.py` (for example inside `detect_cycles`) to set a red breakpoint, then run
configuration 2: VS Code pauses there and you can inspect the money trail step by step.

### What you can do in the Workspace

* **ML verdicts:** every alert is marked *Likely laundering* or *Likely genuine* by the hybrid model, with a
  *Genuine or laundering?* evidence list in the Story tab. Filter the queue by verdict; it is sorted by ML score.
* **Cases:** alert queue, interactive money trail graph, ▶ replay of the scheme in time order,
  account inspector, *Follow money* forward and back, freeze requests, notes and a full audit trail.
* **STR draft:** one click PDF of a Suspicious Transaction Report for FIU-IND (a draft for the analyst, not a filing).
* **Board:** drag cases between New, Investigating, Escalated, Reported and Closed.
* **Overview** and **Evidence:** charts of alerts, money at risk, and accuracy on synthetic and IBM data.
* Keys: J / K next and previous case, Space replay, F freeze, S STR draft.

There is also a Streamlit research dashboard (`streamlit run app/dashboard.py`) with the red team game,
early warning replay and bank consortium simulation.

## Results

**Our dataset:** 47,417 transfers, 3,000 accounts with KYC details, Jan to Mar 2026: 200 planted laundering
schemes (2,555 transfers) and 137 genuine look-alike cases (donation drives, relief redistribution, payroll, chit
funds, merchant sales, own account transfers). Results on the **test month (March), never seen in training**:

| System | Precision | Recall | F1 |
|---|---|---|---|
| Threshold rules (amount ≥ ₹10,000 or 10+ payments a day) | 0.18 | 0.53 | 0.26 |
| Graph shape rules only (`aml.py`) | 0.35 | 0.66 | 0.46 |
| ML only (Random Forest, no rule features) | 0.86 | 0.75 | 0.80 |
| **Hybrid: rules + ML (Fraudbusters)** | **0.89** | **0.87** | **0.88** |

**Genuine or laundering?** (March, counted per case)

| | Graph rules only | Hybrid |
|---|---|---|
| Laundering schemes caught | 46 of 55 | **53 of 55** |
| Genuine look-alike cases wrongly flagged | 39 of 91 | **0 of 91** |
| Genuine look-alike transfers flagged | 44 % | **1.3 %** |

Full numbers, per scenario results and feature importance: [`data/ml_results.json`](data/ml_results.json).

**Real format data: IBM AML benchmark** (HI Small, Kaggle). Trained on 1 to 6 Sep, threshold picked on 7 Sep,
tested on unseen 8 to 10 Sep: 1.32 million transactions, only 0.11 % laundering.

| System | Precision | Recall | F1 | Alerts to review |
|---|---|---|---|---|
| Threshold baseline | 0.0015 | 0.57 | 0.003 | 560,549 |
| Fraudbusters rules only, untuned (first 3 days) | 0.0025 | 0.08 | 0.005 | 37,552 |
| **Fraudbusters IBM profile** (graph features + gradient boosting) | **0.42** | **0.43** | **0.42** | **1,556** |

Our hand tuned rules did not transfer to IBM's data (hops are days apart and many schemes follow no named shape),
so we built [`src/ibm_model.py`](src/ibm_model.py): per account graph features (unique senders and receivers,
pass through behaviour, payment rail) feeding a gradient boosted classifier, computed separately for each time
period so nothing leaks from the test days. 42 % of its alerts are real laundering, against 0.15 % for threshold
rules: an analyst checks 1,556 alerts instead of 560,549.

## Project structure

| Path | What it does |
|---|---|
| [`src/aml.py`](src/aml.py) | **Detection engine:** loads transactions, builds the graph, runs the 4 detectors, scores risk, evaluates against the baseline |
| [`src/features.py`](src/features.py) | **ML features** (graph, KYC, rule outputs) and the plain English *genuine or laundering?* evidence |
| [`src/train_model.py`](src/train_model.py) | **Train / test split, Random Forest, hybrid model**, evaluation of all four systems |
| [`models/fraudbusters_model.joblib`](models/) | The trained hybrid model the Workspace loads |
| `data/train.csv`, `data/test.csv`, `data/accounts.csv` | Training month(s), test month, and KYC details per account |
| [`src/str_report.py`](src/str_report.py) | Builds the STR draft PDF for an alert |
| [`src/ibm_model.py`](src/ibm_model.py) | Graph features + learned scorer on the IBM benchmark |
| [`src/ibm.py`](src/ibm.py) | Runs the plain rules on IBM data |
| [`src/scenarios.py`](src/scenarios.py) | Red team: design a scheme and see if it gets caught |
| [`src/early_warning.py`](src/early_warning.py) | How early in a scheme we raise the alarm |
| [`src/consortium.py`](src/consortium.py) | Banks sharing hashed account IDs vs working alone |
| [`app/server.py`](app/server.py) | **Backend:** FastAPI server, the Workspace's API |
| [`app/web/`](app/web/) | **Frontend:** `index.html`, `style.css`, `app.js` (Cytoscape.js graph, Chart.js charts) |
| [`app/dashboard.py`](app/dashboard.py) | Streamlit research dashboard |
| [`data/generate_synthetic.py`](data/generate_synthetic.py) | Generates the bank: accounts with KYC, normal traffic, laundering and genuine look-alikes |
| [`tests/`](tests/) | Detector, ML (split, leakage, donation vs smurfing) and API tests |
| [`docs/CODE_GUIDE.md`](docs/CODE_GUIDE.md) | Plain English walk through of the code |
| [`docs/EXPLAINER_PROMPT.md`](docs/EXPLAINER_PROMPT.md) | Brief for building the product explainer website |

### API

| Method | Path | Returns |
|---|---|---|
| GET | `/api/alerts` | all alerts with pattern risk, ML score, verdict and case status |
| GET | `/api/alerts/{id}` | one case: graph nodes and roles, transactions with ML scores, evidence signals, grounds, case state |
| GET | `/api/accounts/{acct}` | an account's transfers and alerts |
| GET | `/api/trace` | follow money forward or back from an account |
| POST | `/api/cases/{id}` | change status, assignee, notes, freeze (written to the audit log) |
| GET | `/api/alerts/{id}/str.pdf` | STR draft PDF |
| GET | `/api/overview`, `/api/metrics` | dashboard numbers |

### Reproduce the extra results

```bash
.venv/Scripts/python data/generate_synthetic.py   # data/transactions.csv + data/accounts.csv
.venv/Scripts/python src/train_model.py           # train.csv, test.csv, model, data/ml_results.json (about 30 s)
.venv/Scripts/python src/aml.py                   # alerts + precision / recall / F1 in the terminal
.venv/Scripts/python src/scenarios.py             # data/redteam.csv
.venv/Scripts/python src/early_warning.py         # data/early_warning.csv
.venv/Scripts/python src/consortium.py            # data/consortium.json
.venv/Scripts/python src/ibm_model.py             # data/ibm_model_results.json (needs the Kaggle files in data/, about 90 s)
```

IBM data: download `HI-Small_Trans.csv` and `HI-Small_Patterns.txt` from Kaggle
(`ealtman2019/ibm-transactions-for-anti-money-laundering-aml`) into `data/`. They are too large for the repo.

## Limits and next steps

* Synthetic data follows our own assumptions, which is why we test on IBM's benchmark too. For example mule accounts
  are more often newly opened, so account age is a strong feature; it must be re-checked on a bank's real KYC data.
* Two pass through features look 48 hours ahead, so a transfer is scored once 48 hours have passed.
* The red team shows where we can be beaten: hops more than 48 h apart, or fewer than 8 smurfs.
* Next: graph neural networks, run the IBM model inside the Workspace, analyst logins, a live transaction stream, and AI written case notes
  (`llm_summary` in `src/aml.py`, needs `ANTHROPIC_API_KEY`).

Slides: [docs/FlowTrace_v4_IBM.pptx](docs/FlowTrace_v4_IBM.pptx)
