You are building a polished, SaaS-quality explainer website for **Fraudbusters**, our hackathon project. Build it as a single self-contained web page (one `index.html` with inline CSS and JS; charts may use Chart.js from cdnjs). It must look like the landing page of a serious fintech / RegTech product: clean, confident, modern, fully responsive (works at phone width), with a light and a dark theme.

Its job: a judge or a bank's compliance head opens it and, in 3 minutes, understands (1) the problem, (2) what the product does, (3) exactly how it works end to end, including the ML, and (4) the proof that it works. Accuracy matters more than hype: **use only the facts and numbers below. Do not invent features, customers, numbers, logos, testimonials or certifications.** If the code repository is available in your workspace (https://github.com/jayane25/Fraudbuster), read `README.md`, `docs/CODE_GUIDE.md` and `data/ml_results.json` and treat them as the source of truth if anything differs from this brief.

---

## 1. Project facts

* Product: **Fraudbusters**: graph-based detection of layered money laundering, with an investigator workspace.
* Team **Yenrimedia**: Ini Shetty, Jayane Muralimaran, Deekshith R.
* Hackathon: Build Beyond '26, BMSCE. Problem statement PS 2.1: graph-based detection of layered money laundering networks.
* Code: https://github.com/jayane25/Fraudbuster

## 2. The problem (section: "Why laundering slips through")

Criminals hide illicit money by **layering** it: moving it through many accounts so its origin is lost. There are four common shapes:

| Typology | What happens | Graph shape |
|---|---|---|
| Fan in (smurfing / structuring) | Many people deposit small sums, just under the ₹10,000 reporting limit, into one collector account | Star, many → one |
| Fan out (dispersion) | One account suddenly splits a lump sum to many accounts, which pass it on again | Star, one → many |
| Round tripping (cycle) | Money goes A → B → C → back to A to fake business turnover | Loop |
| Mule chain | Money hops through accounts that forward it within hours, almost untouched | Line |

Banks' rule engines check **one transaction at a time** ("is it ≥ ₹10,000?", "10+ payments a day?"). Each transfer looks innocent; the crime only shows in the **connections**.

**The second, harder problem, and our key differentiator:** genuine activity has the *same shapes*.

| Genuine activity | Looks like |
|---|---|
| Donation drive to an NGO (often a fixed "suggested amount") | Fan in |
| Relief redistribution: an NGO pays many beneficiaries equal amounts | Fan out |
| Payroll: a business pays the same staff every month | Fan out |
| Chit fund: members pay a fixed sum, the organiser pays the pot to one member | Fan in plus payout |
| Merchant / event sales (one ticket price) | Fan in |
| A customer moving money through their own 3 or 4 accounts in minutes | Mule chain |

Shape-only detection drowns investigators in false alarms on these. Fraudbusters separates them.

## 3. How it works end to end (section: "How Fraudbusters works", as a step-by-step pipeline diagram)

1. **Transactions become a graph.** Every account is a node; every transfer is a time-stamped, directed, weighted edge (Python, NetworkX).
2. **Rule-based shape detectors** (our first model, `src/aml.py`) search the graph:
   * Fan in / fan out: a 72-hour sliding window (two pointers) per account; 8+ distinct counterparties; score rises for similar amounts, amounts just under ₹10,000, and a large total.
   * Cycles: depth-first "follow the money" search; each hop within 48 h keeping 90–100 % of the amount; the money returns to its origin in 3–6 hops.
   * Mule chains: the longest line of hops each forwarded within 6 h, keeping 90–100 %; plus a "patient mule" tier (hops within 48 h, 97 %+ kept, 4+ hops).
   * A time-sorted index with binary search makes "where did this money go next?" instant.
   * Each alert gets a **risk score 0–100** = typology weight × (40 % pattern strength + 25 % money at stake + 20 % speed + 15 % breadth).
3. **Context features (graph + KYC).** For every transfer we compute 20 features (`src/features.py`):
   * The transfer: amount, just under the limit, round amount, hour.
   * KYC (what a bank already knows): account type (individual, business, merchant, NGO), account age, whether sender and receiver have the same owner.
   * Graph behaviour: bursts in the last 72 h, how similar the amounts are, **does the receiver move the money on within 48 h (pass-through)**, was the sender just paid, **have these two paid each other before (recurring)**, and has the receiver paid the sender before (reciprocity).
4. **Hybrid ML model: rules + machine learning combined.** A **Random Forest** (300 decision trees that vote, scikit-learn) learns from the 20 context features **plus 8 rule features**: whether a rule detector fired, which shape, its risk and pattern strength, whether the accounts appear in any alert, and the classic threshold-rule flags. So the rules' knowledge and the ML's judgment are combined in one model.
5. **Honest train / test split.** We split by date, never shuffled, because a bank trains on the past and runs on the future:
   * Train: 1 Jan – 28 Feb 2026 (31,480 transfers, 1,884 laundering).
   * The last two weeks of train are held out to pick the alert threshold.
   * Test: 1 – 31 Mar 2026 (15,937 transfers, 671 laundering), never seen in training.
   * Train features are built from train data only.
   * Files: `data/train.csv` and `data/test.csv`. The model is saved to `models/fraudbusters_model.joblib`.
6. **Explainable verdict per alert.** Every alert shows "Likely laundering" or "Likely genuine" with the model score, plus plain-English evidence with ✓ (genuine) or ⚠ (laundering) markers. For example:
   * ✓ "ACC00806 is a registered NGO, open for 8 years"
   * ✓ "ACC00806 kept the money: none of it moved on within 48 hours"
   * ✓ "Every account in the chain belongs to the same customer"
   * ✓ "The money went back to someone who had paid in: a rotating savings pool (chit fund)"
   * ⚠ "100 % of the money collected left the account within 48 hours"
   * ⚠ "67 % of the accounts involved were opened in the last 6 months"
   * ⚠ "Most amounts sit just under the ₹10,000 reporting limit"
7. **Act on it** in the investigator workspace (next section).

**The one-line idea to feature prominently:** *"The shape tells you something unusual happened. The context (who the accounts are, where the money goes next, whether it recurs) tells you whether it is crime."*

## 4. Product features (section: feature grid with small UI mock illustrations built in HTML/CSS, not screenshots)

The Investigator Workspace is a web app: FastAPI backend, vanilla JavaScript frontend, Cytoscape.js graph, Chart.js charts. It includes:

* **Alert queue** ranked by ML score, with a verdict badge per alert and filters by pattern, status and verdict.
* **Interactive money-trail graph** with roles: collector, depositor, mule, origin, final receiver.
* **Replay**: watch the scheme unfold transfer by transfer in time order.
* **Follow the money**: from any account, trace forward or back up to 4 hops beyond the alert.
* **Genuine or laundering? evidence panel**: the ML verdict with its plain-English reasons.
* **Freeze requests, notes, assignment, and a full audit trail.** Every action is logged with analyst and time, as regulators require.
* **One-click STR draft PDF**: a Suspicious Transaction Report in the information format used by FIU-IND (a draft for the analyst, not an official filing).
* **Case board**: drag cases across New → Investigating → Escalated → Reported → Closed.
* **Overview and Evidence dashboards.**
* Research extras: a red-team game ("beat the detector"), an early-warning replay (how early a scheme is caught), and a privacy-safe bank consortium simulation (banks share salted SHA-256 hashed account IDs).

## 5. Results (section: "Proof", with Chart.js charts)

**Test month (March 2026, never seen in training), transaction level:**

| System | Precision | Recall | F1 |
|---|---|---|---|
| Threshold rules (≥ ₹10,000 or 10+ payments a day) | 0.18 | 0.53 | 0.26 |
| Graph shape rules only | 0.35 | 0.66 | 0.46 |
| ML only (Random Forest, no rule features) | 0.86 | 0.75 | 0.80 |
| **Hybrid: rules + ML (Fraudbusters)** | **0.89** | **0.87** | **0.88** |

The hybrid's PR-AUC is 0.93.

**Separating genuine from laundering (March, case level):**
* Laundering schemes caught: **53 of 55** by the hybrid, against 46 by the graph rules alone.
* Genuine look-alike cases wrongly flagged: **0 of 91** by the hybrid, against **39 of 91** by the graph rules alone.
* At transaction level, false alarms on genuine look-alikes fell from **44 %** (graph rules) to **1.3 %** (hybrid).

**Real-format benchmark:** on IBM's public AML dataset (HI-Small, Kaggle; trained on 1–6 Sep, tested on unseen 8–10 Sep 2022, 1.32 M transfers, 0.11 % laundering), a graph-feature gradient-boosting model reaches **F1 0.42 against 0.003** for threshold rules. **42 % of its alerts are real laundering against 0.15 %**, so an analyst reviews **1,556 alerts instead of 560,549**. Our hand-tuned rules alone only caught 8 % there; we say that openly, and it is why we moved to learned graph features.

**Dataset:** 47,417 synthetic transfers across 3,000 accounts with KYC details over Jan–Mar 2026. It contains 200 planted laundering schemes (2,555 transfers) and 137 genuine look-alike cases. Synthetic data is used because real bank data is confidential, and the planted labels make accuracy measurable.

## 6. Honesty and limits (a short, credible section; judges value this)

* Synthetic data follows our own assumptions. For example, mule accounts are more often newly opened, so account age is a strong feature. It must be re-validated on a bank's real KYC data.
* Two pass-through features look 48 hours ahead, so a transfer is scored once 48 hours have passed.
* Known blind spots from our red team: hops spaced more than 48 h apart, and fewer than 8 smurfs.
* Next steps: graph neural networks, a graph database (Neo4j) or PostgreSQL at scale, live streaming detection, analyst login, and integration with core banking and FIU-IND reporting.

## 7. Tech stack (a compact logo-free badge row)

Python · pandas · NumPy · NetworkX · scikit-learn (Random Forest, gradient boosting) · FastAPI · Uvicorn · Pydantic · HTML · CSS · JavaScript · Cytoscape.js · Chart.js · fpdf2 · Streamlit · pytest · Git/GitHub. Storage is CSV and JSON files for the prototype; PostgreSQL or Neo4j is planned for production.

## 8. Page structure (suggested order)

1. **Hero:** product name, one-line value proposition, and an animated hero graphic. For example, a small SVG graph where a "star" pattern lights up red (laundering) while a similar star lights up green (donation drive), labelled "Same shape. Different story." Two buttons: "See how it works" (scrolls) and "View the code" (GitHub link).
2. **The problem:** the four typologies as four small animated graph diagrams.
3. **The hard part: genuine look-alikes:** side-by-side cards (donation drive vs smurfing, payroll vs dispersion, own-account sweep vs mule chain, chit fund vs collector).
4. **How it works:** a 7-step pipeline diagram, each step expandable for detail.
5. **Inside the hybrid model:** the feature groups and the rule features flowing into the Random Forest; the train / validation / test timeline as a horizontal bar.
6. **Product tour:** the workspace features as a feature grid with HTML/CSS mock UI cards (queue item with a verdict badge, evidence list with ✓ / ⚠, case board columns).
7. **Proof:** the results tables as charts: grouped bars for the four systems; "39 of 91 → 0 of 91" false alarms as a big-number comparison; the IBM numbers.
8. **Honesty and limits**, then **Roadmap.**
9. **Team and footer:** team names, hackathon, GitHub link.

## 9. Design and quality bar

* Typography-led and restrained, like a modern fintech site. Pick one accent colour (for example a confident red for "laundering" with a green for "genuine") on a near-neutral palette. Use generous whitespace, sharp hierarchy, and subtle motion: reveal on scroll, and the graph animations. Respect `prefers-reduced-motion`.
* Accessible: semantic HTML, sufficient contrast in both themes, alt text and aria labels on diagrams.
* No placeholder text, no lorem ipsum, no fake logos, no stock-photo people, and no invented metrics.
* Use the Indian number format where natural (₹1,35,000; "5.6 lakh").
* When done, open the page, check every section at desktop and phone width in both themes, fix anything broken, and then summarise what you built.
