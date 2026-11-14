# Fraudbusters code guide

A plain English walk through of the code, so anyone on the team can explain any part of it in the review.
The line numbers are where to look when the file is open on GitHub.

## The big picture

```
data/generate_synthetic.py      makes a fake bank: accounts with KYC, laundering AND genuine look-alikes
          │  data/transactions.csv, data/accounts.csv
          ▼
src/aml.py                      model 1, rules: graph + 4 shape detectors + risk score
          │  list of alerts
          ▼
src/features.py                 20 context features + 8 rule features per transfer
src/train_model.py              train/test split by date, Random Forest = hybrid model (rules + ML)
          │  models/fraudbusters_model.joblib, data/ml_results.json
          ▼
app/server.py                   backend: runs the brain once, answers the website's questions (API)
          │  JSON over http://localhost:8600/api/...
          ▼
app/web/app.js                  frontend: draws the queue, graph, replay, board and charts in the browser
```

Side pieces: `src/str_report.py` (PDF report), `src/ibm_model.py` (real data test), `tests/` (automatic checks).

---

## 1. `data/generate_synthetic.py`: the test data

We cannot get real bank data, so we create a realistic bank. `Bank` makes 3,000 accounts with **KYC details**
(account type: personal, business, merchant or NGO; opening date; owner, and some customers own 4 accounts) and
saves them to `data/accounts.csv`. Most transfers are **normal traffic**. Then we plant two kinds of cases:

* **200 laundering schemes** (`fan_in`, `fan_out`, `cycle`, `mule_chain`), labelled `is_laundering = 1`. Mules
  are more often newly opened accounts; smurf collectors move the money out within a day; a quarter of fan in
  cases use a charity as a front.
* **137 genuine look-alikes** with the SAME shapes, labelled `is_laundering = 0` (column `scenario` says which):
  `donation_drive` (and relief redistribution), `payroll`, `chit_fund`, `merchant_sales`, `own_account_sweep`.

This is what makes the problem realistic: the shape alone cannot tell them apart.

**If asked "why fake data?":** "Real bank data is confidential. Planted cases let us measure accuracy exactly.
We also tested on IBM's public benchmark, which is realistic data from a bank simulator."

## 2. `src/aml.py`: the detection engine (most important file)

**Top of the file (lines 21 to 32): the rule settings.** For example `FAN_MIN_PARTIES = 8` means "at least
8 different people", and `CHAIN_MAX_GAP_H = 6` means "a mule passes the money on within 6 hours". Changing a
number here changes how strict a detector is.

**`Alert` (line 43):** one suspicious case: its type, the accounts, the transactions, the amount, start and
end time, and how cleanly it matches the pattern (`structure`, 0 to 1).

**`build_graph` (line 77):** turns the table into a graph. Every account is a dot (node), every transfer is
an arrow (edge) carrying its amount and time.

**`_detect_fan` (line 96), used by `detect_fan_in` and `detect_fan_out`:**
For each account, look at everything it received (fan in) or sent (fan out) in time order. A **sliding window**
of 72 hours moves along the list: two pointers `i` and `j` mark its ends. If 8 or more different accounts fall
inside the window, we score it with `_fan_score` (line 86): more people, similar amounts, amounts just under
the ₹10,000 limit, and a big total all raise the score. The best window becomes an alert.

**`_OutIndex` (line 139):** a speed trick. For each account it keeps that account's outgoing transfers sorted
by time, so "where did this money go next?" (`next_hops`) is a quick binary search (`bisect`) instead of
scanning 41,000 rows.

**`detect_cycles` (line 164):** starting from every transfer, follow the money hop by hop (a **depth first
search** using a `stack`). A hop only counts if it happens within 48 hours and keeps 90 to 100 % of the amount.
If the path comes back to the account it started from after 3 or more hops, that is a round trip.

**`detect_mule_chains` (line 190):** the same "follow the money" search, but looking for the **longest
straight line** of fast hops (within 6 hours, 90 to 100 % kept), at least 3 hops long. It runs twice in
`detect_all`: once for fast mules, once for "patient" mules (48 hour hops, but 97 % or more passed on, 4 or more
hops). Shorter chains inside a longer one are removed.

**`score_alerts` (line 233):** gives each alert a **risk score from 0 to 100**:
`typology weight × (0.40 pattern strength + 0.25 money + 0.20 speed + 0.15 number of accounts)`. Then it sorts
them and names them AL0001, AL0002 and so on.

**`detect_all` (line 247):** runs everything in order. Cycles go first, and their transfers are excluded
from the mule search so the same money is not reported twice.

**`threshold_baseline` (line 268):** what a basic bank system does: flag any transfer of ₹10,000 or more, or
any account making 10 or more transfers a day. We compare against it.

**`evaluate` (line 284):** precision (of what we flagged, how much was really laundering), recall (of all
laundering, how much we caught) and F1 (one number combining both).

## 3. `app/server.py`: the backend

Built with **FastAPI**. When it starts, it loads the transactions and runs `aml.detect_all` **once** (from
line 38), keeping the alerts in memory so the website is fast.

Each `@app.get` or `@app.post` line is a web address the frontend can call:

* `alerts()` and `alert_detail()`: the alert list, and one alert with its graph nodes, roles (collector,
  depositor, mule) and transactions.
* `account()`: one account's transfers and any alerts it appears in (the inspector panel).
* `trace()` (line 199): **Follow money.** Starting from an account, follow the outgoing (or incoming) money
  up to 4 hops, keeping only transfers that carry at least half the amount within 72 hours.
* `update_case()` (line 240): change a case's status, assignee, notes or freeze. Every change is written to
  the **audit log** with who did it and when, and saved to `data/cases.json` (`_save`, line 103, writes a temp
  file first so a crash cannot corrupt it).
* `str_pdf()` (line 277): makes the STR PDF using `src/str_report.py`.
* `overview()` and `metrics()`: numbers for the charts.

## 4. `app/web/`: the frontend

* `index.html`: the page layout (left menu, search bar, three column case screen).
* `style.css`: the look.
* `app.js`: all the behaviour. Key functions:
  * `boot` (line 24) loads data, `route` (line 44) shows the right screen for the address (`#/cases`, `#/board` …).
  * `drawGraph` (line 199) draws the money trail with **Cytoscape.js** (a graph drawing library).
  * `applyReplay` / `togglePlay` (lines 236, 246): the ▶ replay. Edges appear one by one in time order.
  * `trace` (line 260): calls `/api/trace` and adds the new accounts to the graph (Follow money).
  * `updateCase` (line 176): sends status, notes and freeze changes to the backend.
  * `renderBoard` (line 382): the drag and drop board.
  * `renderOverview`, `renderEvidence` (lines 416, 456): charts with **Chart.js**.
  * `keys` (line 371): keyboard shortcuts J, K, Space, F, S.

The frontend never decides what is suspicious. It only shows what the backend sends.

* `guide.js`: the beginner layer. `renderHome` draws the **Start here** page (what the app does, 3 steps, the 4 shapes,
  a dictionary of terms); `startTour` runs the **guided tour** that highlights each part of the screen (open `#/tour`);
  `HELP` and `hint` add the little *i* help bubbles next to labels.

## 4b. The ML model: `src/features.py` and `src/train_model.py`

**Why:** the rules find *shapes*, but genuine activity has the same shapes (a donation drive looks like smurfing).
The ML model looks at the **context** and learns which is which.

**`graph_features` in `src/features.py` (line 88)** gives every transfer 20 numbers:
* the transfer: amount, just under ₹10,000, round amount, hour
* KYC: account type, account age, **same owner** (line 108)
* behaviour: bursts in the last 72 h, how similar the amounts are, **does the receiver move the money on within
  48 h** (line 117, pass through: mules and smurf collectors do, NGOs and shops do not), was the sender just paid,
  **have these two paid each other before** (line 125, recurring: payroll and chit funds repeat monthly)

The `_Index` class (line 63) makes these fast: it sorts (account, time) once, so "how much did X send between
t1 and t2" is two binary searches.

**`rule_features` (line 134): combining the two models.** What the rule detectors said becomes 8 more inputs:
did a rule fire, which shape, its risk and strength, are the accounts in any alert, threshold rule flags.

**`src/train_model.py`:**
1. **Split by date** (line 74): train = 1 Jan to 28 Feb → `data/train.csv`; test = March → `data/test.csv`.
   The last 2 weeks of train are validation, used only to pick the alert threshold.
2. **`make_model` (line 46):** a Random Forest, 300 decision trees that each see a random sample and vote.
   `class_weight` handles the fact that laundering is rare.
3. Trains two models: **ML only** (20 features) and **Hybrid** (28 features), then compares four systems on March.
4. Saves the hybrid to `models/fraudbusters_model.joblib` and all numbers to `data/ml_results.json`.

**Results on March (never seen in training):** F1 threshold rules 0.26, graph rules 0.46, ML only 0.80,
**hybrid 0.88**. The hybrid catches 53 of 55 laundering schemes and wrongly flags **0 of 91** genuine look-alike
cases (graph rules alone: 39 of 91).

**`signals` (line 164): explaining the verdict** in plain English, ✓ genuine or ⚠ laundering, for example
"registered NGO, open for 8 years", "kept the money: none of it moved on within 48 hours", "every account belongs
to the same customer", "the money went back to someone who had paid in (chit fund)".

**In the app:** `app/server.py` loads the model at start up (lines 54 to 80), gives every alert a score and a
verdict, and `app/web/app.js` shows the verdict badge in the queue and the evidence in the Story tab (line 342).

## 5. `src/str_report.py`: the STR PDF

Builds a Suspicious Transaction Report draft in the format FIU-IND expects: reporting bank, grounds of
suspicion (`GROUNDS`, a ready made list per typology), a narrative, the accounts with their roles (`roles`),
and the transactions. `_ReportPDF` picks a font that exists on the computer (Windows, Linux or Mac) so the
rupee sign prints.

## 6. `src/ibm_model.py`: the real data test

IBM's data behaves differently from ours (hops days apart, many schemes with no clear shape), so our fixed
rules caught only 8 %. This file instead **learns** from the data:

* `features` (line 44) describes every transfer with graph facts about the sender and receiver: how many
  different accounts they pay or get paid by, whether money comes in and goes straight out (pass through),
  which payment method (ACH), round amounts, time of day.
* A **gradient boosting** model (many small decision trees, from scikit-learn) learns from 1 to 6 Sep.
* 7 Sep is used to choose the alert cut off; 8 to 10 Sep is the **exam**: days the model never saw.
* Features are computed separately for each period (line 93), so no information from the exam days leaks
  into training.

Result: 42 % of its alerts are real laundering (threshold rules: 0.15 %), with 1,556 alerts instead of 560,549.

## 7. `tests/`: automatic checks

Run `python -m pytest tests`. 21 checks:

* `test_detectors.py` builds tiny hand made cases, for example 12 people sending ₹9,500 to one account, and
  checks the right detector fires. It also checks look alikes that **should not** fire: too few senders, hops
  too slow, a loop that loses half the money, normal payments.
* `test_ml.py` checks the train and test files never overlap in time, no feature contains the label, history
  features never look into the future, and the trained model scores a donation drive below smurfing.
* `test_api.py` starts the backend and checks the home page, alert list, case update with audit log, and the
  STR PDF. It writes to a temporary file, never to the real `cases.json`.

---

## Questions judges may ask about the code

| Question | Answer |
|---|---|
| Why a graph? | Layering is about connections between accounts. A graph lets us follow money across many hops, which row by row rules cannot do. |
| How fast is it? | About 41,000 transactions are analysed in a few seconds. The sliding window and the sorted index (`_OutIndex`) avoid comparing every pair. |
| Why rules and a model? | Rules find the shapes and give clear reasons; the model reads the context and removes false alarms. Our hybrid feeds the rule results into the model, F1 0.88 vs 0.46 for rules alone. |
| How do you tell a donation from smurfing? | Context: a registered, long standing NGO that keeps the money vs a new personal account that empties itself within hours, plus recurring payments, same owner, and where the money goes next. |
| Is the test fair? | We split by date: trained on Jan and Feb, tested on March, which the model never saw. Train features use train data only. |
| What libraries? | pandas (tables), NetworkX (graph), scikit-learn (model), FastAPI (backend), Cytoscape.js and Chart.js (frontend), fpdf2 (PDF). |
| How do you know it works? | Measured precision, recall and F1 against planted cases, tested on IBM's public benchmark on unseen days, plus 21 automatic tests. |
| Where can it be beaten? | Hops more than 48 hours apart, or fewer than 8 smurfs. Our red team (`src/scenarios.py`) shows this openly. |
