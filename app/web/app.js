/* FlowTrace Investigator Workspace (vanilla JS, Cytoscape, Chart.js) */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const api = async (p, opt) => { const r = await fetch(p, opt); if (!r.ok) throw new Error(await r.text()); return r.json(); };
const post = (p, body) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const inr = (n) => "₹" + Math.round(n).toLocaleString("en-IN");
const lakh = (n) => n >= 1e7 ? `₹${(n / 1e7).toFixed(2)} Cr` : n >= 1e5 ? `₹${(n / 1e5).toFixed(1)} L` : inr(n);
const fmtT = (iso) => new Date(iso).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const riskColor = (r) => r >= 80 ? "#d64933" : r >= 60 ? "#e07a3f" : r >= 40 ? "#c98a16" : "#8a96a6";
const TYPO_COLOR = { fan_in: "#d64933", fan_out: "#2d5f9a", cycle: "#7a4fb5", mule_chain: "#c98a16" };

const S = { meta: null, alerts: [], cur: null, detail: null, typ: new Set(), status: "", sort: "risk",
  me: localGet("ft-me") || "Ini", tab: "story", cy: null, sel: null, playing: null, charts: [] };

function localGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localSet(k, v) { try { localStorage.setItem(k, v); } catch {} }
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 2200); }

/* ------------------------------------------------------------ boot */
async function boot() {
  S.meta = await api("/api/meta");
  $("#me").innerHTML = S.meta.analysts.map((a) => `<option ${a === S.me ? "selected" : ""}>${a}</option>`).join("");
  $("#me").onchange = (e) => { S.me = e.target.value; localSet("ft-me", S.me); toast(`Signed in as ${S.me}`); };
  $("#top-meta").innerHTML = `<span><b>${S.meta.transactions.toLocaleString("en-IN")}</b> transfers</span>
    <span><b>${S.meta.accounts.toLocaleString("en-IN")}</b> accounts</span>
    <span>${fmtT(S.meta.period[0])} to ${fmtT(S.meta.period[1])}</span>`;
  await refreshAlerts();
  setupSearch();
  window.addEventListener("hashchange", route);
  document.addEventListener("keydown", keys);
  route();
}

async function refreshAlerts() {
  S.alerts = await api("/api/alerts");
  const n = S.alerts.filter((a) => a.status === "New").length;
  $("#nav-new").textContent = n || "";
}

function route() {
  const [, view = "cases", id] = location.hash.split("/");
  $$("nav a").forEach((a) => a.classList.toggle("on", a.dataset.view === view));
  S.charts.forEach((c) => c.destroy()); S.charts = [];
  stopPlay();
  if (view === "board") return renderBoard();
  if (view === "overview") return renderOverview();
  if (view === "evidence") return renderEvidence();
  renderCases(id);
}

/* ------------------------------------------------------------ search */
function setupSearch() {
  const q = $("#q"), hits = $("#hits");
  let t;
  q.oninput = () => {
    clearTimeout(t);
    t = setTimeout(async () => {
      if (!q.value.trim()) return hits.classList.remove("show");
      const r = await api("/api/search?q=" + encodeURIComponent(q.value));
      hits.innerHTML = r.length ? r.map((h, i) => `<div data-type="${h.type}" data-id="${esc(h.id)}" class="${i ? "" : "act"}"><small>${h.type}</small><span class="mono">${esc(h.label)}</span></div>`).join("")
        : `<div><small></small>No match</div>`;
      hits.classList.add("show");
    }, 150);
  };
  q.onkeydown = (e) => { if (e.key === "Enter") { const f = $(".hits div[data-id]"); f && f.click(); } if (e.key === "Escape") hits.classList.remove("show"); };
  hits.onclick = (e) => {
    const d = e.target.closest("div[data-id]"); if (!d) return;
    hits.classList.remove("show"); q.value = "";
    if (d.dataset.type === "alert") location.hash = `#/cases/${d.dataset.id}`;
    else openAccountAnywhere(d.dataset.id);
  };
  document.addEventListener("click", (e) => { if (!e.target.closest(".search")) hits.classList.remove("show"); });
}

async function openAccountAnywhere(acct) {
  const p = await api(`/api/accounts/${encodeURIComponent(acct)}`);
  const target = p.alerts[0] || (S.cur ?? S.alerts[0].id);
  if (location.hash !== `#/cases/${target}`) { location.hash = `#/cases/${target}`; await new Promise((r) => setTimeout(r, 350)); }
  await expandAccount(acct);
  selectAccount(acct);
}

/* ------------------------------------------------------------ cases view */
function renderCases(id) {
  const v = $("#view");
  const fresh = !$(".cases", v);
  if (fresh) {
    S.detail = null; if (S.cy) { S.cy.destroy(); S.cy = null; }
    v.innerHTML = ""; v.append($("#tpl-cases").content.cloneNode(true));
    const typs = Object.entries(S.meta.typologies);
    $("#typ-chips").innerHTML = typs.map(([k, l]) => `<button class="chip ${S.typ.has(k) ? "on" : ""}" data-k="${k}">${esc(l.replace(/ \(.*/, ""))}</button>`).join("");
    $("#typ-chips").onclick = (e) => { const b = e.target.closest(".chip"); if (!b) return; S.typ.has(b.dataset.k) ? S.typ.delete(b.dataset.k) : S.typ.add(b.dataset.k); b.classList.toggle("on"); drawQueue(); };
    $("#f-status").innerHTML += S.meta.statuses.map((s) => `<option>${s}</option>`).join("");
    $("#f-status").value = S.status; $("#f-sort").value = S.sort;
    $("#f-status").onchange = (e) => { S.status = e.target.value; drawQueue(); };
    $("#f-sort").onchange = (e) => { S.sort = e.target.value; drawQueue(); };
    $("#queue").onclick = (e) => { const li = e.target.closest("li"); if (li) location.hash = `#/cases/${li.dataset.id}`; };
    $("#case-tabs").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; S.tab = b.dataset.tab; $$("#case-tabs button").forEach((x) => x.classList.toggle("on", x === b)); drawTab(); };
    $("#g-fit").onclick = () => S.cy && S.cy.animate({ fit: { padding: 50 }, duration: 300 });
    $("#g-reset").onclick = () => S.detail && drawGraph(S.detail);
    $("#play").onclick = togglePlay;
    $("#scrub").oninput = (e) => applyReplay(+e.target.value);
  }
  drawQueue();
  const target = id || S.cur || filtered()[0]?.id;
  if (target && (target !== S.cur || !S.detail)) openCase(target);
  else if (target) markQueue();
}

function filtered() {
  let xs = S.alerts.filter((a) => (!S.typ.size || S.typ.has(a.typology)) && (!S.status || a.status === S.status));
  const k = S.sort;
  xs.sort((a, b) => k === "start" ? b.start.localeCompare(a.start) : b[k] - a[k]);
  return xs;
}

function drawQueue() {
  const xs = filtered();
  $("#queue-count").textContent = `${xs.length} of ${S.alerts.length}`;
  $("#queue").innerHTML = xs.map((a) => `<li data-id="${a.id}">
      <div class="ring" style="--p:${a.risk};--c:${riskColor(a.risk)}"><span>${Math.round(a.risk)}</span></div>
      <div class="q-main"><b>${a.id}</b><div>${esc(a.label)}</div><small>${a.accounts} accounts · ${a.tx} transfers · ${a.hours} h</small></div>
      <div class="q-side"><span class="amt">${lakh(a.amount)}</span><span class="pill s-${a.status}">${a.status}</span>
        ${a.assignee ? `<span class="avatar" title="${esc(a.assignee)}">${esc(a.assignee[0])}</span>` : ""}</div></li>`).join("");
  markQueue();
}
function markQueue() {
  $$("#queue li").forEach((li) => li.classList.toggle("on", li.dataset.id === S.cur));
  const on = $("#queue li.on"); on && on.scrollIntoView({ block: "nearest" });
}

async function openCase(id) {
  S.cur = id; markQueue(); stopPlay();
  const d = await api(`/api/alerts/${id}`);
  if (S.cur !== id || !$("#case-head")) return;
  S.detail = d; S.sel = null;
  drawHead(); drawGraph(d); drawTab(); drawInspector();
}

function drawHead() {
  const d = S.detail, c = d.case;
  const opt = (xs, v, blank) => (blank ? `<option value="">${blank}</option>` : "") + xs.map((x) => `<option ${x === v ? "selected" : ""}>${x}</option>`).join("");
  $("#case-head").innerHTML = `
    <div class="case-title"><h1>${esc(d.label)}</h1><span class="mono">${d.id}</span>
      <span class="pill s-${c.status}">${c.status}</span></div>
    <div class="case-actions">
      <select id="c-status" title="Case status">${opt(S.meta.statuses, c.status)}</select>
      <select id="c-assign" title="Assignee">${opt(S.meta.analysts, c.assignee, "Unassigned")}</select>
      <button class="btn danger" id="c-escalate">Escalate</button>
      <a class="btn primary" id="c-str" href="/api/alerts/${d.id}/str.pdf" target="_blank" style="text-decoration:none">STR draft</a>
    </div>
    <div class="case-facts">
      <span>Risk<b style="color:${riskColor(d.risk)}">${Math.round(d.risk)} / 100</b></span>
      <span>Money moved<b>${inr(d.amount)}</b></span>
      <span>Accounts<b>${d.accounts}</b></span>
      <span>Transfers<b>${d.tx}</b></span>
      <span>Time span<b>${d.hours} h</b></span>
      <span>Started<b>${fmtT(d.start)}</b></span>
    </div>`;
  $("#c-status").onchange = (e) => updateCase({ status: e.target.value }, `Status set to ${e.target.value}`);
  $("#c-assign").onchange = (e) => updateCase({ assignee: e.target.value }, e.target.value ? `Assigned to ${e.target.value}` : "Unassigned");
  $("#c-escalate").onclick = () => updateCase({ status: "Escalated" }, "Escalated to the MLRO");
  $("#c-str").onclick = () => setTimeout(async () => { S.detail.case = (await api(`/api/alerts/${d.id}`)).case; if (S.tab === "notes") drawTab(); }, 900);
}

async function updateCase(body, msg) {
  const c = await post(`/api/cases/${S.cur}`, { ...body, by: S.me });
  S.detail.case = c;
  const a = S.alerts.find((x) => x.id === S.cur); if (a) { a.status = c.status; a.assignee = c.assignee; a.notes = c.notes.length; }
  $("#nav-new").textContent = S.alerts.filter((x) => x.status === "New").length || "";
  drawHead(); drawQueue(); if (S.tab === "notes") drawTab();
  if (msg) toast(msg);
  return c;
}

/* ------------------------------------------------------------ graph */
const ROLE_COLOR = { Collector: "#d64933", Disperser: "#2d5f9a", "Origin and return": "#7a4fb5", Origin: "#d64933",
  "Final receiver": "#0e1a2b", "Suspected mule": "#c98a16", "Loop intermediary": "#9b7bc8", Depositor: "#e8a090", Receiver: "#86a7cf" };

function nodeData(n, extra = {}) {
  return { data: { id: n.id, label: n.id, role: n.role || "Context", color: ROLE_COLOR[n.role] || "#c3cad4",
    size: n.role && /Collector|Disperser|Origin|Final/.test(n.role) ? 46 : n.role ? 32 : 24, frozen: n.frozen ? 1 : 0, ...extra } };
}
function edgeData(t, inAlert, order) {
  return { data: { id: t.tx_id, source: t.src, target: t.dst, amount: t.amount, label: lakh(t.amount), ts: t.ts,
    w: Math.max(1.5, Math.min(7, Math.log10(t.amount) - 2)), flagged: t.alert ? 1 : 0, inAlert: inAlert ? 1 : 0, order: order ?? -1 } };
}

function drawGraph(d) {
  const els = [...d.nodes.map((n) => nodeData(n)), ...d.transactions.map((t, i) => edgeData(t, true, i))];
  if (S.cy) S.cy.destroy();
  const layout = d.typology === "cycle" ? { name: "circle", padding: 60 }
    : d.typology === "mule_chain" ? { name: "breadthfirst", directed: true, padding: 50, spacingFactor: 1.3, roots: [d.path[0]] }
    : { name: "concentric", padding: 50, minNodeSpacing: 34, concentric: (n) => (n.id() === d.focal ? 10 : 1), levelWidth: () => 1 };
  S.cy = cytoscape({
    container: $("#graph"), elements: els, layout, wheelSensitivity: 0.25, minZoom: 0.2, maxZoom: 3,
    style: [
      { selector: "node", style: { "background-color": "data(color)", width: "data(size)", height: "data(size)", label: "data(label)",
        "font-family": "IBM Plex Mono, Consolas, monospace", "font-size": 10, color: "#0e1a2b", "text-valign": "bottom", "text-margin-y": 5,
        "border-width": 2, "border-color": "#fff", "text-background-color": "#fff", "text-background-opacity": 0.8, "text-background-padding": 2 } },
      { selector: "node[frozen = 1]", style: { "border-width": 5, "border-color": "#2d5f9a", "border-style": "double", label: (n) => "❄ " + n.id() } },
      { selector: "node:selected", style: { "overlay-color": "#d64933", "overlay-opacity": 0.15, "overlay-padding": 8 } },
      { selector: "edge", style: { width: "data(w)", "line-color": "#c3cad4", "target-arrow-color": "#c3cad4", "target-arrow-shape": "triangle",
        "curve-style": "bezier", "arrow-scale": 0.9, label: "", "font-size": 9, color: "#5b6675", "text-rotation": "autorotate",
        "text-background-color": "#fafbfc", "text-background-opacity": 1, "text-background-padding": 1 } },
      { selector: "edge[flagged = 1]", style: { "line-color": "#d64933", "target-arrow-color": "#d64933", label: "data(label)" } },
      { selector: "edge[inAlert = 0][flagged = 0]", style: { "line-style": "dashed", opacity: 0.75 } },
      { selector: "edge.trace", style: { "line-color": "#2d5f9a", "target-arrow-color": "#2d5f9a", label: "data(label)", "line-style": "solid", opacity: 1 } },
      { selector: ".hidden", style: { display: "none" } },
      { selector: "edge.newest", style: { "line-color": "#0e1a2b", "target-arrow-color": "#0e1a2b", width: 6 } },
    ],
  });
  S.cy.one("layoutstop", () => S.cy.fit(undefined, 50));
  if (!S.ro) S.ro = new ResizeObserver(() => { if (S.cy) { S.cy.resize(); S.cy.fit(undefined, 50); } });
  S.ro.disconnect(); S.ro.observe($("#graph"));
  S.cy.on("tap", "node", (e) => selectAccount(e.target.id()));
  S.cy.on("tap", (e) => { if (e.target === S.cy) { S.sel = null; drawInspector(); } });
  S.cy.on("dbltap", "node", (e) => expandAccount(e.target.id()));
  const roles = [...new Set(d.nodes.map((n) => n.role))];
  $("#legend").innerHTML = roles.map((r) => `<span><i style="background:${ROLE_COLOR[r] || "#c3cad4"}"></i>${r}</span>`).join("") +
    `<span><i style="background:#c3cad4"></i>Context</span>`;
  const n = d.transactions.length;
  $("#scrub").max = n; $("#scrub").value = n; applyReplay(n);
}

function applyReplay(k) {
  if (!S.cy || !S.detail) return;
  const txs = S.detail.transactions;
  S.cy.edges("[inAlert = 1]").forEach((e) => { const o = e.data("order"); e.toggleClass("hidden", o >= k); e.toggleClass("newest", o === k - 1 && k < txs.length); });
  const live = new Set(); txs.slice(0, k).forEach((t) => { live.add(t.src); live.add(t.dst); });
  const alertNodes = new Set(S.detail.nodes.map((n) => n.id));
  S.cy.nodes().forEach((n) => { if (alertNodes.has(n.id())) n.style("opacity", k === 0 || live.has(n.id()) ? 1 : 0.18); });
  const moved = txs.slice(0, k).reduce((s, t) => s + t.amount, 0);
  $("#scrub-label").textContent = k ? `${k}/${txs.length} · ${fmtT(txs[k - 1].ts)} · ${lakh(moved)} moved` : `Start · drag or press play`;
}
function togglePlay() {
  if (S.playing) return stopPlay();
  const s = $("#scrub"); if (+s.value >= +s.max) s.value = 0;
  $("#play").textContent = "❚❚";
  S.playing = setInterval(() => { if (+s.value >= +s.max) return stopPlay(); s.value = +s.value + 1; applyReplay(+s.value); }, Math.max(180, 3200 / Math.max(+s.max, 1)));
}
function stopPlay() { clearInterval(S.playing); S.playing = null; const p = $("#play"); if (p) p.textContent = "▶"; }

async function expandAccount(acct) {
  const p = await api(`/api/accounts/${encodeURIComponent(acct)}?limit=12`);
  addTx(p.transactions, null);
  relayout(acct);
  toast(`Showing ${p.shown} of ${p.total} transfers for ${acct}`);
}
async function trace(acct, direction) {
  const since = direction === "out" ? S.detail.start : S.detail.end;
  const r = await api(`/api/trace?acct=${encodeURIComponent(acct)}&direction=${direction}&since=${encodeURIComponent(since)}`);
  const added = addTx(r.transactions, "trace");
  relayout(acct);
  toast(r.transactions.length ? `${direction === "out" ? "Followed the money forward" : "Traced back towards the source"}: ${r.accounts.length - 1} accounts, ${r.transactions.length} transfers` : "No onward transfers in the window");
  return added;
}
function addTx(txs, cls) {
  const cy = S.cy; let added = 0;
  cy.batch(() => {
    txs.forEach((t) => {
      [t.src, t.dst].forEach((id) => { if (!cy.$id(id).length) { cy.add({ ...nodeData({ id }), position: { ...(cy.$id(t.src).position() || { x: 0, y: 0 }) } }); added++; } });
      let e = cy.$id(t.tx_id);
      if (!e.length) e = cy.add(edgeData(t, false));
      if (cls) e.addClass(cls);
    });
  });
  return added;
}
function relayout(focus) {
  S.cy.layout({ name: "cose", animate: true, animationDuration: 500, padding: 40, nodeRepulsion: 9000, idealEdgeLength: 90,
    fit: true, randomize: false }).run();
  if (focus) S.cy.$id(focus).select();
}

/* ------------------------------------------------------------ inspector */
async function selectAccount(acct) {
  S.sel = acct;
  S.cy && (S.cy.$(":selected").unselect(), S.cy.$id(acct).select());
  const p = await api(`/api/accounts/${encodeURIComponent(acct)}`);
  if (S.sel !== acct) return;
  drawInspector(p);
}
function drawInspector(p) {
  const el = $("#inspector"); if (!el) return;
  const d = S.detail;
  if (!p) {
    const top = d ? d.nodes.slice().sort((a, b) => (b.sent + b.received) - (a.sent + a.received)).slice(0, 6) : [];
    el.innerHTML = `<h3>Account inspector</h3><div class="empty">Click any account in the graph to inspect it.<br>Double click to pull in its other transfers.</div>
      ${d ? `<h3>Key accounts in this case</h3><ul class="mini">${top.map((n) => `<li><span class="acct" data-a="${n.id}">${n.id}</span><span>${esc(n.role)}</span></li>`).join("")}</ul>` : ""}`;
    el.onclick = (e) => { const a = e.target.closest("[data-a]"); a && selectAccount(a.dataset.a); };
    return;
  }
  const role = d?.nodes.find((n) => n.id === p.account)?.role || "Outside this alert";
  const frozenHere = d?.case.frozen.includes(p.account);
  el.innerHTML = `
    <h3>Account</h3>
    <div class="insp-id">${esc(p.account)} ${p.frozen ? '<span class="frozen-tag">FROZEN</span>' : ""}</div>
    <span class="role" style="background:${ROLE_COLOR[role] ? ROLE_COLOR[role] + "22" : "var(--mist)"};color:${ROLE_COLOR[role] || "var(--slate)"}">${esc(role)}</span>
    <div class="kv">
      <div><small>Received</small><b>${lakh(p.received)}</b></div><div><small>Sent</small><b>${lakh(p.sent)}</b></div>
      <div><small>Payers</small><b>${p.payers}</b></div><div><small>Payees</small><b>${p.payees}</b></div>
      <div><small>Transfers in</small><b>${p.in_tx}</b></div><div><small>Transfers out</small><b>${p.out_tx}</b></div>
    </div>
    <div class="insp-actions">
      <button class="btn" id="i-back">◀ Trace source</button>
      <button class="btn" id="i-fwd">Follow money ▶</button>
      <button class="btn wide" id="i-expand">Show all its transfers</button>
      <button class="btn wide ${frozenHere ? "" : "danger"}" id="i-freeze">${frozenHere ? "Lift freeze" : "Request freeze"}</button>
    </div>
    ${p.alerts.length ? `<h3>Appears in ${p.alerts.length} alert${p.alerts.length > 1 ? "s" : ""}</h3><div class="alert-links">${p.alerts.map((a) => `<a data-al="${a}">${a}</a>`).join("")}</div>` : ""}
    <h3>Largest and flagged transfers</h3>
    <ul class="mini">${p.transactions.slice().sort((a, b) => b.amount - a.amount).slice(0, 10).map((t) =>
      `<li class="${t.alert ? "flag" : ""}"><span>${t.src === p.account ? "→ " + esc(t.dst) : "← " + esc(t.src)}</span><span>${lakh(t.amount)}</span></li>`).join("")}</ul>
    <p style="color:var(--slate);font-size:12px">Active ${fmtT(p.first)} to ${fmtT(p.last)}</p>`;
  $("#i-fwd").onclick = () => trace(p.account, "out");
  $("#i-back").onclick = () => trace(p.account, "in");
  $("#i-expand").onclick = () => expandAccount(p.account);
  $("#i-freeze").onclick = async () => {
    await updateCase(frozenHere ? { unfreeze: p.account } : { freeze: p.account }, frozenHere ? `Freeze lifted on ${p.account}` : `Freeze requested on ${p.account}`);
    S.cy.$id(p.account).data("frozen", frozenHere ? 0 : 1);
    selectAccount(p.account);
  };
  el.onclick = (e) => { const a = e.target.closest("[data-al]"); if (a) location.hash = `#/cases/${a.dataset.al}`; };
}

/* ------------------------------------------------------------ tabs */
function drawTab() {
  const d = S.detail, el = $("#tab-body"); if (!d || !el) return;
  if (S.tab === "story") {
    el.innerHTML = `<div class="story"><p>${esc(d.summary)}</p><h3 style="font-size:13px;margin:0 0 6px">Grounds of suspicion</h3>
      <ul class="grounds">${d.grounds.map((g) => `<li>${esc(g)}</li>`).join("")}</ul></div>`;
  } else if (S.tab === "tx") {
    el.innerHTML = `<table class="tx"><thead><tr><th>#</th><th>Time</th><th>From</th><th>To</th><th class="r">Amount</th><th>Transfer</th></tr></thead><tbody>
      ${d.transactions.map((t, i) => `<tr><td>${i + 1}</td><td>${fmtT(t.ts)}</td><td><span class="acct" data-a="${t.src}">${t.src}</span></td>
      <td><span class="acct" data-a="${t.dst}">${t.dst}</span></td><td class="r">${inr(t.amount)}</td><td class="mono">${t.tx_id}</td></tr>`).join("")}</tbody></table>`;
    el.onclick = (e) => { const a = e.target.closest("[data-a]"); a && selectAccount(a.dataset.a); };
  } else {
    const c = d.case;
    el.innerHTML = `<div class="notes-grid"><div>
        <div class="note-form"><textarea id="note" placeholder="Add an investigation note as ${esc(S.me)}"></textarea>
        <button class="btn primary" id="add-note" style="margin-top:6px">Add note</button></div>
        ${c.notes.slice().reverse().map((n) => `<div class="note"><small>${esc(n.by)} · ${esc(n.t)}</small><div>${esc(n.text)}</div></div>`).join("") || `<p style="color:var(--slate)">No notes yet.</p>`}
      </div><div><h3 style="font-size:13px;margin:0 0 6px">Audit trail</h3>
        <ul class="log">${c.log.slice().reverse().map((l) => `<li><small>${esc(l.t)} · ${esc(l.by)}</small> ${esc(l.event)}</li>`).join("") || `<li style="color:var(--slate)">Nothing recorded yet. Every action here is logged.</li>`}</ul>
      </div></div>`;
    $("#add-note").onclick = async () => { const v = $("#note").value; if (!v.trim()) return; await updateCase({ note: v }, "Note saved"); };
  }
}

/* ------------------------------------------------------------ keyboard */
function keys(e) {
  if (e.target.matches("input, textarea, select") || !$(".cases")) return;
  const xs = filtered(), i = xs.findIndex((a) => a.id === S.cur);
  if (e.key === "j" && i < xs.length - 1) location.hash = `#/cases/${xs[i + 1].id}`;
  if (e.key === "k" && i > 0) location.hash = `#/cases/${xs[i - 1].id}`;
  if (e.key === "s" && S.cur) $("#c-str").click(), window.open(`/api/alerts/${S.cur}/str.pdf`, "_blank");
  if (e.key === "f" && S.sel) $("#i-freeze")?.click();
  if (e.key === " " && S.detail) { e.preventDefault(); togglePlay(); }
}

/* ------------------------------------------------------------ board */
async function renderBoard() {
  await refreshAlerts();
  const v = $("#view");
  v.innerHTML = `<section class="board">${S.meta.statuses.map((s) => {
    const xs = S.alerts.filter((a) => a.status === s).sort((a, b) => b.risk - a.risk);
    return `<div class="col" data-s="${s}"><h4>${s}<span>${xs.length}</span></h4><div class="cards">${xs.slice(0, 60).map((a) => `
      <div class="card" draggable="true" data-id="${a.id}" style="--c:${TYPO_COLOR[a.typology]}">
        <div class="top-line"><b>${a.id}</b><span class="ring" style="--p:${a.risk};--c:${riskColor(a.risk)};width:30px;height:30px;font-size:11px"><span>${Math.round(a.risk)}</span></span></div>
        <div class="lbl">${esc(a.label)}</div><small>${lakh(a.amount)} · ${a.accounts} accounts${a.assignee ? " · " + esc(a.assignee) : ""}</small></div>`).join("")}
      ${xs.length > 60 ? `<small style="color:var(--slate);text-align:center">and ${xs.length - 60} more</small>` : ""}</div></div>`;
  }).join("")}</section>`;
  $$(".card", v).forEach((c) => {
    c.ondragstart = (e) => e.dataTransfer.setData("text/plain", c.dataset.id);
    c.ondblclick = () => (location.hash = `#/cases/${c.dataset.id}`);
  });
  $$(".col", v).forEach((col) => {
    col.ondragover = (e) => { e.preventDefault(); col.classList.add("drop"); };
    col.ondragleave = () => col.classList.remove("drop");
    col.ondrop = async (e) => {
      e.preventDefault(); col.classList.remove("drop");
      const id = e.dataTransfer.getData("text/plain");
      await post(`/api/cases/${id}`, { status: col.dataset.s, by: S.me });
      toast(`${id} moved to ${col.dataset.s}`);
      if (S.cur === id) S.detail = null;
      renderBoard();
    };
  });
}

/* ------------------------------------------------------------ overview */
Chart.defaults.font.family = "IBM Plex Sans, Segoe UI, sans-serif";
Chart.defaults.color = "#5b6675";
const chart = (el, cfg) => { const c = new Chart(el, cfg); S.charts.push(c); return c; };

async function renderOverview() {
  const o = await api("/api/overview");
  const v = $("#view");
  const typ = Object.entries(o.by_typology);
  const open = o.status.New + o.status.Investigating + o.status.Escalated;
  v.innerHTML = `<section class="page">
    <h2>Overview</h2><p class="sub">Everything FlowTrace found in this dataset, and where the team is with it.</p>
    <div class="kpis">
      <div class="kpi hot"><small>Open alerts</small><b>${open}</b><em>${o.status.New} not yet touched</em></div>
      <div class="kpi"><small>Money in flagged flows</small><b>${lakh(o.money)}</b><em>across ${o.alerts} alerts</em></div>
      <div class="kpi"><small>Critical risk</small><b>${o.bands["Critical 80+"]}</b><em>score 80 or above</em></div>
      <div class="kpi"><small>Freeze requests</small><b>${o.frozen}</b><em>accounts held for review</em></div>
    </div>
    <div class="panels">
      <div class="panel"><h3>Alerts by pattern</h3><p class="cap">Count of alerts and the money each pattern moved</p><div class="chart"><canvas id="c1"></canvas></div></div>
      <div class="panel"><h3>Risk bands</h3><p class="cap">How urgent the queue is</p><div class="chart"><canvas id="c2"></canvas></div></div>
      <div class="panel"><h3>When the schemes started</h3><p class="cap">Alerts by start day</p><div class="chart"><canvas id="c3"></canvas></div></div>
      <div class="panel"><h3>Team progress</h3><p class="cap">Cases by status</p><div class="chart"><canvas id="c4"></canvas></div></div>
      <div class="panel wide"><h3>Highest risk right now</h3><p class="cap">Click to open the case</p>
        <table class="tx top-list"><thead><tr><th>Alert</th><th>Pattern</th><th class="r">Risk</th><th class="r">Amount</th><th>Accounts</th><th>Status</th></tr></thead><tbody>
        ${o.top.map((a) => `<tr data-id="${a.id}"><td class="mono">${a.id}</td><td>${esc(a.label)}</td><td class="r">${Math.round(a.risk)}</td><td class="r">${inr(a.amount)}</td><td>${a.accounts}</td><td><span class="pill s-${a.status}">${a.status}</span></td></tr>`).join("")}</tbody></table></div>
    </div></section>`;
  $$(".top-list tr[data-id]").forEach((r) => (r.onclick = () => (location.hash = `#/cases/${r.dataset.id}`)));
  const colors = typ.map(([l]) => TYPO_COLOR[Object.keys(S.meta.typologies).find((k) => S.meta.typologies[k] === l)] || "#a9b2be");
  chart($("#c1"), { type: "bar", data: { labels: typ.map(([l]) => l.replace(/ \(.*/, "")), datasets: [
      { label: "Alerts", data: typ.map(([, x]) => x.alerts), backgroundColor: colors, borderRadius: 4, yAxisID: "y" },
      { label: "Money (₹ lakh)", data: typ.map(([, x]) => +(x.amount / 1e5).toFixed(1)), type: "line", borderColor: "#0e1a2b", backgroundColor: "#0e1a2b", yAxisID: "y1" }] },
    options: { maintainAspectRatio: false, plugins: { legend: { position: "bottom" } }, scales: { y: { grid: { color: "#eceff3" } }, y1: { position: "right", grid: { display: false } } } } });
  chart($("#c2"), { type: "doughnut", data: { labels: Object.keys(o.bands), datasets: [{ data: Object.values(o.bands), backgroundColor: ["#d64933", "#e07a3f", "#c98a16", "#a9b2be"], borderWidth: 0 }] },
    options: { maintainAspectRatio: false, cutout: "62%", plugins: { legend: { position: "right" } } } });
  chart($("#c3"), { type: "bar", data: { labels: Object.keys(o.by_day).map((d) => new Date(d).toLocaleDateString("en-IN", { day: "2-digit", month: "short" })),
      datasets: [{ label: "Alerts", data: Object.values(o.by_day), backgroundColor: "#0e1a2b", borderRadius: 3 }] },
    options: { maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { grid: { color: "#eceff3" } }, x: { grid: { display: false } } } } });
  chart($("#c4"), { type: "bar", data: { labels: Object.keys(o.status), datasets: [{ data: Object.values(o.status),
      backgroundColor: ["#2d5f9a", "#c98a16", "#d64933", "#5c3f99", "#a9b2be"], borderRadius: 4 }] },
    options: { indexAxis: "y", maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { grid: { color: "#eceff3" } }, y: { grid: { display: false } } } } });
}

/* ------------------------------------------------------------ evidence */
async function renderEvidence() {
  const m = await api("/api/metrics");
  const v = $("#view");
  const im = m.ibm_model, ft = im?.metrics.find((x) => x.system !== "Threshold baseline"), bl = im?.metrics.find((x) => x.system === "Threshold baseline");
  const syn = m.synthetic || [];
  const sf = syn.find((x) => x.system !== "Threshold baseline"), sb = syn.find((x) => x.system === "Threshold baseline");
  v.innerHTML = `<section class="page">
    <h2>Evidence</h2><p class="sub">Does it actually work? Tested on our labelled data and on IBM's public benchmark.</p>
    ${im ? `<div class="callout">
      <div class="lead"><b>Real data, unseen days.</b> On IBM's AML benchmark FlowTrace was trained on 1 to 6 Sep and tested on 8 to 10 Sep 2022:
      ${im.test_transactions.toLocaleString("en-IN")} transfers, only ${(im.base_rate * 100).toFixed(2)}% laundering.</div>
      <div><small>Analyst queue</small><b>${im.flagged.toLocaleString("en-IN")}</b><em>threshold rules: ${im.baseline_flagged.toLocaleString("en-IN")}</em></div>
      <div><small>Alerts that are real laundering</small><b>${Math.round(ft.tp / (ft.tp + ft.fp) * 100)}%</b><em>threshold rules: ${(bl.precision * 100).toFixed(2)}%</em></div>
      <div><small>Laundering caught</small><b>${Math.round(ft.recall * 100)}%</b><em>threshold rules: ${Math.round(bl.recall * 100)}%</em></div>
      <div><small>Better than random</small><b>${Math.round(im.lift_vs_random)}×</b><em>F1 ${ft.f1.toFixed(2)} vs ${bl.f1.toFixed(3)}</em></div>
    </div>` : ""}
    <div class="panels">
      <div class="panel"><h3>IBM benchmark (real format data)</h3><p class="cap">Precision, recall and F1 on unseen days</p><div class="chart"><canvas id="e1"></canvas></div></div>
      <div class="panel"><h3>Our labelled dataset</h3><p class="cap">${sf ? `41,564 transfers, 160 planted cases. F1 ${sf.f1} vs ${sb.f1}` : ""}</p><div class="chart"><canvas id="e2"></canvas></div></div>
      <div class="panel"><h3>Caught by pattern</h3><p class="cap">Share of laundering transfers flagged, labelled dataset</p><div class="chart"><canvas id="e3"></canvas></div></div>
      <div class="panel"><h3>Banks working together</h3><p class="cap">Recall when each bank works alone versus a privacy safe consortium</p><div class="chart"><canvas id="e4"></canvas></div></div>
      ${m.ibm_rules ? `<div class="panel wide"><h3>What did not work, and what we learned</h3><p class="cap" style="font-size:13.5px;line-height:1.55;color:var(--ink)">
        Our first IBM run used the rules tuned on our own data, unchanged. They caught only ${Math.round(m.ibm_rules.metrics.find((x) => x.system !== "Threshold baseline").recall * 100)}% of laundering:
        real launderers wait days between hops, and about 87% of IBM laundering moves over ACH against about 12% of all traffic.
        So for real data FlowTrace turns the graph into features (fan in and fan out breadth, relay behaviour, payment rail mix, second hop reach)
        and a gradient boosted scorer ranks every transfer. Each period's features come only from that period, so nothing leaks from the test days.</p></div>` : ""}
    </div></section>`;
  const grouped = (el, rows, labels, keys, names) => chart(el, { type: "bar", data: { labels, datasets: rows.map((r, i) => ({
      label: names[i], data: keys.map((k) => r[k]), backgroundColor: i ? "#d64933" : "#a9b2be", borderRadius: 4 })) },
    options: { maintainAspectRatio: false, plugins: { legend: { position: "bottom" } }, scales: { y: { min: 0, max: 1, grid: { color: "#eceff3" } }, x: { grid: { display: false } } } } });
  if (im) grouped($("#e1"), [bl, ft], ["Precision", "Recall", "F1"], ["precision", "recall", "f1"], ["Threshold rules", "FlowTrace"]);
  if (sf) grouped($("#e2"), [sb, sf], ["Precision", "Recall", "F1"], ["precision", "recall", "f1"], ["Threshold rules", "FlowTrace"]);
  if (m.by_typology) {
    const L = { fan_in: "Fan in", fan_out: "Fan out", cycle: "Round trip", mule_chain: "Mule chain" };
    const rows = m.by_typology;
    chart($("#e3"), { type: "bar", data: { labels: rows.map((r) => L[r.typology] || r.typology), datasets: [
        { label: "Threshold rules", data: rows.map((r) => r.Baseline), backgroundColor: "#a9b2be", borderRadius: 4 },
        { label: "FlowTrace", data: rows.map((r) => r.FlowTrace), backgroundColor: "#d64933", borderRadius: 4 }] },
      options: { maintainAspectRatio: false, plugins: { legend: { position: "bottom" } }, scales: { y: { min: 0, max: 1, grid: { color: "#eceff3" } }, x: { grid: { display: false } } } } });
  }
  if (m.consortium) {
    const L = { fan_in: "Fan in", fan_out: "Fan out", cycle: "Round trip", mule_chain: "Mule chain" };
    const rb = m.consortium.recall_by_typology;
    chart($("#e4"), { type: "bar", data: { labels: rb.map((r) => L[r.typology]), datasets: [
        { label: "Each bank alone", data: rb.map((r) => r.siloed), backgroundColor: "#a9b2be", borderRadius: 4 },
        { label: "Consortium", data: rb.map((r) => r.consortium), backgroundColor: "#d64933", borderRadius: 4 }] },
      options: { maintainAspectRatio: false, plugins: { legend: { position: "bottom" } }, scales: { y: { min: 0, max: 1, grid: { color: "#eceff3" } }, x: { grid: { display: false } } } } });
  }
}

boot().catch((e) => { $("#view").innerHTML = `<div class="empty">Could not start: ${esc(e.message)}</div>`; });
