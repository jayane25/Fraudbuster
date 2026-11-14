/* Fraudbusters: the beginner friendly layer.
   "Start here" page, a step by step guided tour, and plain language help for every piece of jargon.
   Loaded after app.js; app.js calls renderHome(), startTour(), hint() and HELP. */

const HELP = {
  fan_in: "Many people send money into ONE account in a short time. Criminals do this to split dirty money into small deposits that stay under the bank's alert limit (called smurfing).",
  fan_out: "ONE account suddenly sends money out to MANY accounts, to scatter it so it is hard to follow.",
  cycle: "Money travels through several accounts and comes back to where it started, to fake business activity (round tripping).",
  mule_chain: "Money is passed down a line of accounts within hours, almost untouched. The accounts in the middle are 'money mules'.",
  ml: "The AI model's verdict. It looks at the shape AND the context (who owns the accounts, how old they are, where the money goes next) and gives a laundering score from 0 to 100%.",
  risk: "How strongly the transfers match a suspicious shape, from 0 to 100. A high pattern risk can still be innocent, like a donation drive. The AI verdict checks that.",
  money: "The total amount of money moved in this pattern.",
  accounts: "How many bank accounts are involved.",
  transfers: "How many money transfers make up this pattern.",
  span: "How long the whole pattern took, from the first transfer to the last.",
  started: "When the first transfer of this pattern happened.",
  verdictFilter: "Show only the cases the AI thinks are laundering, or only the ones it thinks are genuine.",
};
const hint = (key) => `<span class="hint" tabindex="0" data-tip="${esc(HELP[key] || key)}">i</span>`;

const SHAPES = [
  ["fan_in", "Many to one", "Smurfing", "Lots of small deposits pile into one account.", "Genuine look alike: a donation drive to a charity, or a shop's sales."],
  ["fan_out", "One to many", "Dispersion", "One account scatters money to many accounts.", "Genuine look alike: a company paying salaries, or relief payments."],
  ["cycle", "Goes round", "Round tripping", "Money returns to where it started.", "Genuine look alike: a chit fund, where members pool money and take turns."],
  ["mule_chain", "Passed along", "Mule chain", "Money is relayed through accounts within hours.", "Genuine look alike: a person moving money between their own accounts."],
];

const GLOSSARY = [
  ["Alert", "A group of transfers that matched a suspicious shape. Each one is a case to check."],
  ["Pattern risk", "0 to 100: how strongly the transfers match a laundering shape."],
  ["AI verdict", "Likely laundering or likely genuine, decided by the machine learning model from the shape and the context."],
  ["Likely genuine", "The shape looked suspicious, but the context says it is normal, like a donation drive or payroll."],
  ["Collector", "The account where many deposits pile up."],
  ["Money mule", "An account that receives money and quickly passes it on, often rented out by criminals."],
  ["KYC", "Know Your Customer: what the bank knows about an account, like its owner, type and age."],
  ["Freeze request", "Asking for an account to be blocked so the money cannot move while it is investigated."],
  ["STR", "Suspicious Transaction Report: the report banks send to India's Financial Intelligence Unit."],
  ["Audit trail", "A record of every action taken on a case: who did what, and when."],
];

/* small diagrams of the four shapes */
function shapeSvg(kind) {
  const id = "ah" + kind, R = "#d64933", G = "#a9b2be";
  const dot = (x, y, r, c) => `<circle cx="${x}" cy="${y}" r="${r}" fill="${c}"/>`;
  const arrow = (x1, y1, x2, y2, r1, r2, c) => {
    const L = Math.hypot(x2 - x1, y2 - y1), ux = (x2 - x1) / L, uy = (y2 - y1) / L;
    return `<line x1="${x1 + ux * r1}" y1="${y1 + uy * r1}" x2="${x2 - ux * (r2 + 3)}" y2="${y2 - uy * (r2 + 3)}" stroke="${c}" stroke-width="1.8" marker-end="url(#${id})"/>`;
  };
  let body = "";
  if (kind === "fan_in" || kind === "fan_out") {
    const hub = kind === "fan_in" ? [92, 42] : [28, 42];
    const side = kind === "fan_in" ? [[22, 10], [14, 32], [14, 54], [22, 76], [52, 6], [52, 78]] : [[98, 10], [106, 32], [106, 54], [98, 76], [68, 6], [68, 78]];
    side.forEach(([x, y]) => { body += kind === "fan_in" ? arrow(x, y, ...hub, 5, 11, G) : arrow(...hub, x, y, 11, 5, G); });
    side.forEach(([x, y]) => { body += dot(x, y, 5, G); });
    body += dot(...hub, 11, R);
  } else if (kind === "cycle") {
    const p = [[60, 10], [96, 42], [60, 76], [24, 42]];
    p.forEach((a, i) => { body += arrow(...a, ...p[(i + 1) % 4], 8, 8, R); });
    p.forEach(([x, y], i) => { body += dot(x, y, 8, i ? G : R); });
  } else {
    const p = [[12, 56], [36, 26], [60, 56], [84, 26], [108, 56]];
    p.forEach((a, i) => { if (i < 4) body += arrow(...a, ...p[i + 1], 7, 7, R); });
    p.forEach(([x, y], i) => { body += dot(x, y, 7, i === 0 || i === 4 ? R : G); });
  }
  return `<svg viewBox="0 0 120 86" role="img" aria-label="${kind} diagram"><defs><marker id="${id}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="context-stroke"/></marker></defs>${body}</svg>`;
}

function examples() {
  const xs = S.alerts.slice();
  const laund = xs.filter((a) => a.verdict === "laundering").sort((a, b) => b.ml - a.ml || b.risk - a.risk)[0] || xs[0];
  const gen = xs.filter((a) => a.verdict === "genuine" && a.typology === "fan_in").sort((a, b) => b.risk - a.risk)[0]
    || xs.filter((a) => a.verdict === "genuine").sort((a, b) => b.risk - a.risk)[0];
  return { laund: laund?.id, gen: gen?.id };
}

/* ------------------------------------------------------------ Start here page */
function renderHome() {
  const v = $("#view");
  const n = S.alerts.length, nl = S.alerts.filter((a) => a.verdict === "laundering").length, ng = S.alerts.filter((a) => a.verdict === "genuine").length;
  const ex = examples(), fmt = (x) => x.toLocaleString("en-IN");
  v.innerHTML = `<section class="page home">
    <div class="hero">
      <small>WELCOME TO FRAUDBUSTERS</small>
      <h1>Find money laundering hidden in everyday bank transfers.</h1>
      <p>Criminals hide dirty money by passing it through many bank accounts, so each single transfer looks innocent.
        Fraudbusters connects all the transfers into a map, spots the suspicious shapes, and then an AI model checks whether each one is
        <b class="t-red">really crime</b> or something <b class="t-green">genuine</b>, like a donation drive or a company paying salaries.</p>
      <div class="hero-cta">
        <button class="btn big primary" id="h-tour">▶ Take the 1 minute tour</button>
        ${ex.laund ? `<a class="btn big" href="#/cases/${ex.laund}">Open a laundering case</a>` : ""}
        ${ex.gen ? `<a class="btn big" href="#/cases/${ex.gen}">Open a genuine look alike</a>` : ""}
      </div>
    </div>

    <h2>What we found in this bank's data</h2>
    <div class="funnel">
      <div class="f-step"><b>${fmt(S.meta.transactions)}</b><span>bank transfers checked, between ${fmt(S.meta.accounts)} accounts</span></div>
      <div class="f-arrow">→</div>
      <div class="f-step"><b>${n}</b><span>matched a suspicious shape (alerts)</span></div>
      <div class="f-arrow">→</div>
      <div class="f-split">
        <a class="f-step red" href="#/cases" data-verdict="laundering"><b>${nl}</b><span>likely laundering: investigate these</span></a>
        <a class="f-step green" href="#/cases" data-verdict="genuine"><b>${ng}</b><span>likely genuine: donations, salaries, own accounts and more</span></a>
      </div>
    </div>

    <h2>How it works, in 3 steps</h2>
    <div class="steps3">
      <div><i>1</i><h3>Map the money</h3><p>Every bank account becomes a dot and every transfer an arrow, so you can see who paid whom and when.</p></div>
      <div><i>2</i><h3>Spot suspicious shapes</h3><p>Rules look for the four shapes laundering leaves behind (below) and give each a pattern risk from 0 to 100.</p></div>
      <div><i>3</i><h3>AI checks the context</h3><p>A machine learning model looks at who owns the accounts, how old they are and where the money goes next, then says
        <b class="t-red">likely laundering</b> or <b class="t-green">likely genuine</b>, with the reasons.</p></div>
    </div>

    <h2>The 4 suspicious shapes</h2>
    <div class="shapes4">${SHAPES.map(([k, name, alias, what, look]) => `
      <a class="shape" href="#/cases" data-typ="${k}">${shapeSvg(k)}<h3>${name}</h3><small>${alias}</small><p>${what}</p><p class="look">${look}</p></a>`).join("")}
    </div>

    <h2>Where to go next</h2>
    <div class="pages4">
      <a href="#/cases"><b>Cases</b><span>Open an alert, see the money map, replay it, read the AI's reasons and take action.</span></a>
      <a href="#/board"><b>Board</b><span>The team's to do list: drag cases from New to Closed.</span></a>
      <a href="#/overview"><b>Overview</b><span>The big picture: how many alerts, how much money, how far the team has got.</span></a>
      <a href="#/evidence"><b>Evidence</b><span>Proof it works: accuracy tested on a month of data the AI never saw.</span></a>
    </div>

    <h2>Words you will see</h2>
    <dl class="gloss">${GLOSSARY.map(([t, d]) => `<div><dt>${t}</dt><dd>${d}</dd></div>`).join("")}</dl>
  </section>`;
  $("#h-tour").onclick = () => startTour();
  $$(".f-split a", v).forEach((a) => (a.onclick = () => { S.verdict = a.dataset.verdict; S.typ.clear(); }));
  $$(".shape", v).forEach((a) => (a.onclick = () => { S.typ = new Set([a.dataset.typ]); S.verdict = ""; }));
}

/* ------------------------------------------------------------ guided tour */
const TOUR = [
  ["#queue", "The alert queue", "Every suspicious pattern the system found. The most suspicious are at the top. Click any alert to open it."],
  ["#queue li.on .verdict", "The AI verdict", "Red means likely laundering, green means likely genuine, with a score. The rules find unusual shapes; the AI checks whether it is really crime."],
  ["#case-head", "The case at a glance", "A one line summary in plain words, the verdict, how much money, how many accounts and when. Hover any little i for an explanation."],
  [".graph-wrap", "The money map", "Each dot is a bank account and each arrow a transfer. The big red dot is where the money collected. Scroll to zoom, drag to move, click a dot for details."],
  [".replay", "Replay what happened", "Press ▶ to watch the transfers appear in the order they really happened."],
  ["#tab-body", "Why the AI decided", "The evidence in plain words: ✓ is a sign it is genuine, ! is a sign of laundering. The Transactions tab lists every transfer."],
  ["#inspector", "Look at any account", "Click a dot in the map and its details appear here. You can follow where the money went next, or request a freeze."],
  [".case-actions", "Take action", "Set the case status, assign a teammate, escalate, or download a ready made STR report for the Financial Intelligence Unit."],
  ['nav a[data-view="evidence"]', "Does it work?", "The Evidence page shows how accurate the system is, tested on a month the AI never saw. Click Start here any time to see the guide again."],
];
let TI = -1;

function waitFor(fn, ms = 9000) {
  return new Promise((res) => { const t0 = Date.now(); (function poll() { if (fn()) return res(true); if (Date.now() - t0 > ms) return res(false); setTimeout(poll, 80); })(); });
}

async function startTour(first = 1) {
  const ex = examples();
  const onCase = location.hash.startsWith("#/cases/") && S.detail;
  if (!onCase) { S.typ.clear(); S.verdict = ""; S.status = ""; location.hash = `#/cases/${ex.laund}`; }
  await waitFor(() => $("#case-head .case-title") && S.detail && $("#queue li.on"));
  if (S.tab !== "story") $('#case-tabs button[data-tab="story"]')?.click();
  await new Promise((r) => setTimeout(r, 450));
  $("#tour").classList.add("show");
  showStep(Math.min(Math.max(first, 1), TOUR.length) - 1);
}
function endTour() { TI = -1; $("#tour").classList.remove("show"); }

function showStep(i) {
  if (i < 0) return;
  if (i >= TOUR.length) return endTour();
  const [sel, title, text] = TOUR[i];
  const el = $(sel);
  if (!el) return showStep(i + (i >= TI ? 1 : -1));
  TI = i;
  el.scrollIntoView({ block: "nearest", inline: "nearest" });
  const r = el.getBoundingClientRect(), pad = 6;
  const hole = $("#tour .tour-hole");
  Object.assign(hole.style, { left: r.left - pad + "px", top: r.top - pad + "px", width: r.width + 2 * pad + "px", height: r.height + 2 * pad + "px" });
  const pop = $("#tour .tour-pop");
  pop.innerHTML = `<small>Step ${i + 1} of ${TOUR.length}</small><h3>${title}</h3><p>${text}</p>
    <div class="tour-btns"><button class="btn ghost" data-t="skip">Skip tour</button><span></span>
      ${i ? `<button class="btn" data-t="back">Back</button>` : ""}<button class="btn primary" data-t="next">${i === TOUR.length - 1 ? "Finish" : "Next"}</button></div>`;
  const W = innerWidth, H = innerHeight, pw = 330, ph = pop.offsetHeight || 190, gap = 16;
  let x, y;
  if (r.right + gap + pw < W) { x = r.right + gap; y = r.top; }
  else if (r.left - gap - pw > 0) { x = r.left - gap - pw; y = r.top; }
  else if (r.bottom + gap + ph < H) { x = r.left; y = r.bottom + gap; }
  else { x = r.left; y = r.top - gap - ph; }
  pop.style.left = Math.max(12, Math.min(x, W - pw - 12)) + "px";
  pop.style.top = Math.max(12, Math.min(y, H - ph - 12)) + "px";
  pop.querySelector('[data-t="next"]').focus();
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("#tour [data-t]"); if (!b) return;
  if (b.dataset.t === "next") showStep(TI + 1);
  else if (b.dataset.t === "back") showStep(TI - 1);
  else endTour();
});
document.addEventListener("keydown", (e) => {
  if (TI < 0) return;
  if (["Escape", "ArrowRight", "ArrowLeft", "Enter", " ", "j", "k", "f", "s"].includes(e.key)) { e.stopPropagation(); e.preventDefault(); }
  if (e.key === "Escape") endTour();
  if (e.key === "ArrowRight" || e.key === "Enter") showStep(TI + 1);
  if (e.key === "ArrowLeft") showStep(TI - 1);
}, true);
window.addEventListener("resize", () => { if (TI >= 0) showStep(TI); });
document.getElementById("tour-btn").onclick = () => startTour();
