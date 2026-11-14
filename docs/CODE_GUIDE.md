# Fraudbusters code guide

A plain English walk through of the code, so anyone on the team can explain any part of it in the review.
The line numbers are where to look when the file is open on GitHub.

## The big picture

```
data/generate_synthetic.py      makes fake bank transactions with planted laundering cases
          │  data/transactions.csv
          ▼
src/aml.py                      the brain: graph + 4 detectors + risk score
          │  list of alerts
          ▼
app/server.py                   backend: runs the brain once, answers the website's questions (API)
          │  JSON over http://localhost:8600/api/...
          ▼
app/web/app.js                  frontend: draws the queue, graph, replay, board and charts in the browser
```

Side pieces: `src/str_report.py` (PDF report), `src/ibm_model.py` (real data test), `tests/` (automatic checks).

---

## 1. `data/generate_synthetic.py`: the test data

We cannot get real bank data, so we create it. Most rows are **normal traffic** (salaries, shopping, friends
paying each other, `normal_traffic`). Then we **plant** 160 laundering schemes with the functions `fan_in`,
`fan_out`, `cycle` and `mule_chain`. Each planted row is labelled `is_laundering = 1` with its `typology`, so
we can measure how many we catch.

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
line 36), keeping the alerts in memory so the website is fast.

Each `@app.get` or `@app.post` line is a web address the frontend can call:

* `alerts()` and `alert_detail()`: the alert list, and one alert with its graph nodes, roles (collector,
  depositor, mule) and transactions.
* `account()`: one account's transfers and any alerts it appears in (the inspector panel).
* `trace()` (line 162): **Follow money.** Starting from an account, follow the outgoing (or incoming) money
  up to 4 hops, keeping only transfers that carry at least half the amount within 72 hours.
* `update_case()` (line 203): change a case's status, assignee, notes or freeze. Every change is written to
  the **audit log** with who did it and when, and saved to `data/cases.json` (`_save`, line 71, writes a temp
  file first so a crash cannot corrupt it).
* `str_pdf()` (line 240): makes the STR PDF using `src/str_report.py`.
* `overview()` and `metrics()`: numbers for the charts.

## 4. `app/web/`: the frontend

* `index.html`: the page layout (left menu, search bar, three column case screen).
* `style.css`: the look.
* `app.js`: all the behaviour. Key functions:
  * `boot` (line 21) loads data, `route` (line 41) shows the right screen for the address (`#/cases`, `#/board` …).
  * `drawGraph` (line 190) draws the money trail with **Cytoscape.js** (a graph drawing library).
  * `applyReplay` / `togglePlay` (lines 227, 237): the ▶ replay. Edges appear one by one in time order.
  * `trace` (line 251): calls `/api/trace` and adds the new accounts to the graph (Follow money).
  * `updateCase` (line 167): sends status, notes and freeze changes to the backend.
  * `renderBoard` (line 364): the drag and drop board.
  * `renderOverview`, `renderEvidence` (lines 398, 437): charts with **Chart.js**.
  * `keys` (line 353): keyboard shortcuts J, K, Space, F, S.

The frontend never decides what is suspicious. It only shows what the backend sends.

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

Run `python -m pytest tests`. 16 checks:

* `test_detectors.py` builds tiny hand made cases, for example 12 people sending ₹9,500 to one account, and
  checks the right detector fires. It also checks look alikes that **should not** fire: too few senders, hops
  too slow, a loop that loses half the money, normal payments.
* `test_api.py` starts the backend and checks the home page, alert list, case update with audit log, and the
  STR PDF. It writes to a temporary file, never to the real `cases.json`.

---

## Questions judges may ask about the code

| Question | Answer |
|---|---|
| Why a graph? | Layering is about connections between accounts. A graph lets us follow money across many hops, which row by row rules cannot do. |
| How fast is it? | About 41,000 transactions are analysed in a few seconds. The sliding window and the sorted index (`_OutIndex`) avoid comparing every pair. |
| Why rules and a model? | Rules give a clear reason for each alert (needed for regulators). The model handles messy real data. Banks need both. |
| What libraries? | pandas (tables), NetworkX (graph), scikit-learn (model), FastAPI (backend), Cytoscape.js and Chart.js (frontend), fpdf2 (PDF). |
| How do you know it works? | Measured precision, recall and F1 against planted cases, tested on IBM's public benchmark on unseen days, plus 16 automatic tests. |
| Where can it be beaten? | Hops more than 48 hours apart, or fewer than 8 smurfs. Our red team (`src/scenarios.py`) shows this openly. |
