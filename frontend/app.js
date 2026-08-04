"use strict";

/* ============================ utilities ============================ */
const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path, opts) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" }, ...opts,
  });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.status === 204 ? null : r.json();
}

function clock(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour12: false });
}
function ago(iso) {
  if (!iso) return "—";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${s | 0}s`;
  if (s < 3600) return `${(s / 60) | 0}m`;
  if (s < 86400) return `${(s / 3600) | 0}h`;
  return `${(s / 86400) | 0}d`;
}
const CAT = {
  tpms: "tpms", entertainment: "entertainment", phone: "phone",
  wearable: "wearable", unknown: "unknown",
};
const catLabel = { tpms: "TPMS", entertainment: "Infotainment", phone: "Phone",
  wearable: "Wearable", unknown: "Unknown" };

/* ============================ state ============================ */
const App = {
  view: "dashboard",
  status: null,
  feed: [],            // recent live events (cap 60)
  activity: new Array(48).fill(0),
  tick: 0,
  ws: null,
  wsRetry: 0,
};

/* ============================ websocket ============================ */
function connectWS() {
  const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
  const ws = new WebSocket(url);
  App.ws = ws;
  ws.onopen = () => { App.wsRetry = 0; setConn(true); };
  ws.onclose = () => { setConn(false); scheduleReconnect(); };
  ws.onerror = () => ws.close();
  ws.onmessage = (e) => {
    let msg; try { msg = JSON.parse(e.data); } catch { return; }
    if (msg.type === "hello") { (msg.payload.history || []).forEach(handleEvent); return; }
    handleEvent(msg);
  };
}
function scheduleReconnect() {
  App.wsRetry = Math.min(App.wsRetry + 1, 6);
  setTimeout(connectWS, 500 * App.wsRetry);
}
function setConn(on) {
  const c = $("#conn");
  c.classList.toggle("on", on); c.classList.toggle("off", !on);
  $("#conn-label").textContent = on ? "live" : "reconnecting…";
}

let refreshTimer = null;
function scheduleStatusRefresh() {
  if (refreshTimer) return;
  refreshTimer = setTimeout(() => { refreshTimer = null; refreshStatus(); }, 700);
}

function handleEvent(msg) {
  const { type, payload } = msg;
  switch (type) {
    case "sighting.new":
      pushFeed({ ...payload, ts: payload.ts, evt: "sighting" });
      App.activity[App.activity.length - 1]++;
      break;
    case "signal.new":
      pushFeed({ evt: "newsig", kind: payload.kind, identifier: payload.identifier,
        category: payload.category, ts: payload.last_seen });
      scheduleStatusRefresh();
      break;
    case "detection.new":
      pushFeed({ evt: "detection", identifier: payload.plate_text || "capture",
        kind: "event", category: "entertainment", ts: payload.ts });
      if (App.view === "detections") renderView();
      scheduleStatusRefresh();
      break;
    case "vehicle.new":
    case "vehicle.update":
      if (App.view === "vehicles") scheduleViewRefresh();
      scheduleStatusRefresh();
      break;
    case "notification.new":
      toast(payload);
      bumpAlerts();
      if (App.view === "notifications") renderView();
      break;
    case "suggestion.new":
      pushFeed({ evt: "suggestion", identifier: `suggest: ${payload.a?.identifier} ↔ ${payload.b?.identifier || "?"}`,
        kind: "event", category: "unknown", ts: payload.created_at });
      scheduleStatusRefresh();
      if (App.view === "review") scheduleViewRefresh();
      break;
    case "suggestion.update":
    case "suggestion.resolved":
      scheduleStatusRefresh();
      if (App.view === "review") scheduleViewRefresh();
      break;
    case "service.status":
      mergeService(payload); renderSidebarServices();
      if (App.view === "dashboard") renderServices();
      if (App.view === "settings") renderSettingsServices();
      break;
    case "plate_bound":
    case "log":
    default: break;
  }
  if (App.view === "dashboard") { renderFeed(); }
}

function pushFeed(row) {
  App.feed.unshift(row);
  if (App.feed.length > 60) App.feed.pop();
}

/* sparkline shift */
setInterval(() => {
  App.activity.push(0);
  if (App.activity.length > 48) App.activity.shift();
  if (App.view === "dashboard") renderSpark();
}, 1500);

/* ============================ status ============================ */
async function refreshStatus() {
  try { App.status = await api("/api/system/status"); } catch { return; }
  renderTopStats(); renderSidebarServices();
  const unacked = App.status.counts.unacked_notifications;
  const pill = $("#nav-alerts");
  pill.textContent = unacked; pill.dataset.zero = unacked === 0 ? "1" : "0";
  const pend = App.status.counts.pending_suggestions || 0;
  const rp = $("#nav-review");
  if (rp) { rp.textContent = pend; rp.dataset.zero = pend === 0 ? "1" : "0"; }
  $("#mock-badge").classList.toggle("hidden", !App.status.mock_mode);
  if (App.view === "dashboard") { renderTiles(); renderServices(); renderCatBars(); }
}
setInterval(refreshStatus, 5000);

function renderTopStats() {
  const c = App.status.counts;
  $("#topstats").innerHTML = [
    ["signals", c.signals], ["vehicles", c.vehicles],
    ["plates", c.plates], ["sightings", c.sightings],
  ].map(([k, v]) => `<span class="chip"><b>${v}</b><span class="k">${k}</span></span>`).join("");
}

function mergeService(snap) {
  if (!App.status) return;
  const list = App.status.services;
  const i = list.findIndex(s => s.name === snap.name);
  if (i >= 0) list[i] = snap; else list.push(snap);
}

function renderSidebarServices() {
  if (!App.status) return;
  $("#svc-mini").innerHTML = App.status.services.map(s => `
    <div class="svcmini ${s.status}"><span class="d"></span>${esc(s.name)}</div>`).join("");
}

let alertsBump = 0;
function bumpAlerts() {
  const pill = $("#nav-alerts");
  pill.textContent = (+pill.textContent || 0) + 1; pill.dataset.zero = "0";
}

/* ============================ router ============================ */
const NAV = ["dashboard", "signals", "vehicles", "review", "detections", "notifications", "settings"];
function go(view) {
  App.view = view;
  $$(".navitem").forEach(n => n.classList.toggle("active", n.dataset.view === view));
  location.hash = view;
  renderView();
}
function renderView() {
  ({ dashboard: viewDashboard, signals: viewSignals, vehicles: viewVehicles,
     review: viewReview, detections: viewDetections, notifications: viewNotifications,
     settings: viewSettings }[App.view] || viewDashboard)();
}
let viewRefreshTimer = null;
function scheduleViewRefresh() {
  if (viewRefreshTimer) return;
  viewRefreshTimer = setTimeout(() => { viewRefreshTimer = null; renderView(); }, 800);
}

/* ============================ dashboard ============================ */
function viewDashboard() {
  $("#main").innerHTML = `
    <div class="view-head"><h1>Live Console</h1>
      <span class="sub">real-time passive capture &amp; correlation</span></div>
    <div class="grid tiles" id="tiles"></div>
    <div class="dash" style="margin-top:16px">
      <div style="display:flex;flex-direction:column;gap:16px">
        <div class="card pad">
          <h3>Activity <span class="spacer"></span><span class="sec-note">sightings / 1.5s</span></h3>
          <svg class="spark" id="spark" viewBox="0 0 300 70" preserveAspectRatio="none"></svg>
        </div>
        <div class="card pad">
          <h3>Live feed</h3>
          <div class="feed" id="feed"></div>
        </div>
      </div>
      <div style="display:flex;flex-direction:column;gap:16px">
        <div class="card pad">
          <h3>Capture services</h3>
          <div id="services"></div>
        </div>
        <div class="card pad">
          <h3>Signal categories</h3>
          <div class="catbars" id="catbars"></div>
        </div>
      </div>
    </div>`;
  renderTiles(); renderSpark(); renderFeed(); renderServices(); renderCatBars();
}

function renderTiles() {
  const t = $("#tiles"); if (!t || !App.status) return;
  const c = App.status.counts;
  const tiles = [
    ["signals", c.signals, ""], ["vehicles", c.vehicles, "accent"],
    ["plates bound", c.plates, "crit"], ["pending review", c.pending_suggestions || 0, "accent"],
    ["new (24h)", c.new_signals_24h, ""], ["unacked", c.unacked_notifications, "crit"],
  ];
  t.innerHTML = tiles.map(([l, v, cls]) =>
    `<div class="card tile ${cls}"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");
}

function renderSpark() {
  const svg = $("#spark"); if (!svg) return;
  const a = App.activity, n = a.length, W = 300, H = 70, max = Math.max(4, ...a);
  const pts = a.map((v, i) => [i * (W / (n - 1)), H - (v / max) * (H - 8) - 4]);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  const fill = `${line} L${W},${H} L0,${H} Z`;
  svg.innerHTML = `<path class="fill" d="${fill}"/><path class="stroke" d="${line}"/>`;
}

function renderFeed() {
  const f = $("#feed"); if (!f) return;
  f.innerHTML = App.feed.map(r => {
    const kind = r.kind || "event";
    const rssi = r.rssi != null ? `${r.rssi} dBm` : "";
    const label = r.evt === "newsig" ? "NEW " + esc(r.identifier)
      : r.evt === "detection" ? "📷 " + esc(r.identifier)
      : r.evt === "suggestion" ? "⚖ " + esc(r.identifier) : esc(r.identifier);
    const tag = r.evt === "suggestion" ? "sug" : r.evt === "detection" ? "cam" : esc(kind);
    return `<div class="feed-row ${r.evt === "newsig" ? "newsig" : ""}">
      <span class="t">${r.ts ? clock(r.ts) : ""}</span>
      <span class="kind-tag k-${kind}">${tag}</span>
      <span class="id mono"><span class="cat-dot c-${CAT[r.category] || "unknown"}"></span> ${label}</span>
      <span class="rssi">${rssi}</span></div>`;
  }).join("") || `<div class="empty">waiting for capture events…</div>`;
}

function renderServices() {
  const el = $("#services"); if (!el || !App.status) return;
  el.innerHTML = App.status.services.map(s => `
    <div class="svc-row">
      <span class="status-dot ${s.status}"></span>
      <div style="flex:1">
        <div class="svc-name">${esc(s.name)}
          <span class="sec-note">· ${esc(s.status)}${s.stats?.emitted ? " · " + s.stats.emitted + " emitted" : ""}</span></div>
        <div class="svc-desc">${esc(s.description || "")}${s.last_error ? " — <span style='color:var(--crit)'>" + esc(s.last_error) + "</span>" : ""}</div>
      </div>
      <div style="display:flex;gap:6px">${svcButtons(s)}</div>
    </div>`).join("") || `<div class="empty">no services configured</div>`;
  wireSvcButtons(el);
}
async function svcAction(name, action) {
  try { await api(`/api/services/${name}/${action}`, { method: "POST" }); }
  catch (e) { alert(`Failed to ${action} ${name}: ${e.message}`); }
  refreshStatus();
}
function svcButtons(s) {
  return s.running
    ? `<button class="btn sm danger" data-svc="stop|${esc(s.name)}">Stop</button>
       <button class="btn sm" data-svc="restart|${esc(s.name)}">Restart</button>`
    : `<button class="btn sm primary" data-svc="start|${esc(s.name)}">Start</button>
       <button class="btn sm" data-svc="restart|${esc(s.name)}">Restart</button>`;
}
function wireSvcButtons(root) {
  $$("[data-svc]", root || document).forEach(b => {
    const [a, n] = b.dataset.svc.split("|"); b.onclick = () => svcAction(n, a);
  });
}
function svcInfo(s) {
  const i = s.info || {};
  if (i.radios) return i.radios.map(r =>
    `dev ${r.device ?? "auto"}: ${(r.frequencies || []).join("/")}${r.gain ? " @" + r.gain : ""}`).join(" · ");
  if (i.adapter) return `adapter ${esc(i.adapter)}${i.active_scan ? " · active" : ""}${i.throttle_s != null ? " · throttle " + i.throttle_s + "s" : ""}`;
  return "";
}

function renderCatBars() {
  const el = $("#catbars"); if (!el || !App.status) return;
  const by = App.status.signals_by_category || {};
  const total = Object.values(by).reduce((a, b) => a + b, 0) || 1;
  const order = ["tpms", "entertainment", "phone", "wearable", "unknown"];
  el.innerHTML = order.filter(k => by[k]).map(k => {
    const v = by[k], pct = (v / total * 100).toFixed(0);
    return `<div class="catbar"><span><span class="cat-dot c-${k}"></span> ${catLabel[k]}</span>
      <span class="track"><span class="bar c-${k}" style="width:${pct}%;background:var(--${k})"></span></span>
      <span style="text-align:right;color:var(--txt-dim)">${v}</span></div>`;
  }).join("") || `<div class="empty">no signals yet</div>`;
}

/* ============================ signals ============================ */
const sigFilter = { kind: "", category: "", q: "", baseline: "" };
async function viewSignals() {
  $("#main").innerHTML = `
    <div class="view-head"><h1>Signals</h1>
      <span class="sub">every unique RF identifier observed</span></div>
    <div class="toolbar">
      <input type="search" id="sq" placeholder="search identifier / label…" value="${esc(sigFilter.q)}"/>
      <select id="sk"><option value="">all kinds</option><option>tpms</option><option value="ble">ble</option><option value="bt_classic">bt_classic</option></select>
      <select id="sc"><option value="">all categories</option>${["tpms","entertainment","phone","wearable","unknown"].map(c=>`<option>${c}</option>`).join("")}</select>
      <select id="sb"><option value="">baseline: any</option><option value="false">non-baseline</option><option value="true">baseline</option></select>
    </div>
    <div class="card pad"><div style="overflow:auto"><table id="sigtable"></table></div></div>`;
  $("#sk").value = sigFilter.kind; $("#sc").value = sigFilter.category; $("#sb").value = sigFilter.baseline;
  $("#sq").oninput = debounce(e => { sigFilter.q = e.target.value; loadSignals(); }, 300);
  $("#sk").onchange = e => { sigFilter.kind = e.target.value; loadSignals(); };
  $("#sc").onchange = e => { sigFilter.category = e.target.value; loadSignals(); };
  $("#sb").onchange = e => { sigFilter.baseline = e.target.value; loadSignals(); };
  loadSignals();
}
async function loadSignals() {
  const p = new URLSearchParams();
  if (sigFilter.kind) p.set("kind", sigFilter.kind);
  if (sigFilter.category) p.set("category", sigFilter.category);
  if (sigFilter.q) p.set("q", sigFilter.q);
  if (sigFilter.baseline) p.set("baseline", sigFilter.baseline);
  const rows = await api("/api/signals?" + p);
  const t = $("#sigtable"); if (!t) return;
  t.innerHTML = `<thead><tr>
      <th></th><th>Identifier</th><th>Label</th><th>Kind</th><th>Seen</th>
      <th>RSSI</th><th>First</th><th>Last</th><th>Baseline</th></tr></thead>
    <tbody>${rows.map(s => `
      <tr data-sig="${s.id}">
        <td><span class="cat-dot c-${CAT[s.category]||"unknown"}"></span></td>
        <td class="mono">${esc(s.identifier)}</td>
        <td>${esc(s.label||"")}</td>
        <td><span class="kind-tag k-${s.kind}">${esc(s.kind)}</span></td>
        <td>${s.count}</td>
        <td>${s.rssi_last ?? "—"}</td>
        <td>${ago(s.first_seen)} ago</td>
        <td>${ago(s.last_seen)} ago</td>
        <td><span class="tag-base ${s.is_baseline?"on":""}">${s.is_baseline?"baseline":"—"}</span></td>
      </tr>`).join("")}</tbody>`;
  $$("#sigtable tr[data-sig]").forEach(tr =>
    tr.onclick = () => openSignal(+tr.dataset.sig));
  if (!rows.length) t.innerHTML += `<tbody><tr><td colspan="9"><div class="empty">no signals match</div></td></tr></tbody>`;
}

async function openSignal(id) {
  const [s, sightings, assoc] = await Promise.all([
    api(`/api/signals/${id}`), api(`/api/signals/${id}/sightings?limit=200`),
    api(`/api/signals/${id}/associations`),
  ]);
  const rssis = sightings.filter(x => x.rssi != null).map(x => x.rssi).reverse();
  drawer(`
    <span class="close-x" data-close>×</span>
    <h2>${esc(s.identifier)}</h2>
    <div class="sec-note">${esc(s.kind)} · ${catLabel[s.category]||s.category}</div>
    ${rssiChart(rssis)}
    <div class="kv">
      <span class="k">Label</span><span>${esc(s.label||"—")}</span>
      <span class="k">Sightings</span><span>${s.count}</span>
      <span class="k">RSSI last / best</span><span>${s.rssi_last??"—"} / ${s.rssi_best??"—"} dBm</span>
      <span class="k">First seen</span><span>${s.first_seen? new Date(s.first_seen).toLocaleString():"—"}</span>
      <span class="k">Last seen</span><span>${s.last_seen? new Date(s.last_seen).toLocaleString():"—"}</span>
      <span class="k">Vehicle</span><span>${s.vehicle_id?`<a data-veh="${s.vehicle_id}" style="color:var(--accent)">Vehicle #${s.vehicle_id}</a>`:"unlinked"}</span>
    </div>
    <div class="field"><label>Label</label><input id="d-label" value="${esc(s.label||"")}"/></div>
    <div class="field"><label>Notes</label><textarea id="d-notes" rows="2">${esc(s.notes||"")}</textarea></div>
    <label class="chk" style="display:flex;gap:8px;align-items:center;margin:8px 0">
      <input type="checkbox" id="d-base" ${s.is_baseline?"checked":""}/> Mark as baseline (my own device)</label>
    <button class="btn primary" id="d-save">Save</button>
    <div class="field"><label>Reassign to vehicle</label>
      <div style="display:flex;gap:8px;align-items:center">
        <input id="s-vehid" type="number" min="1" placeholder="vehicle id" style="width:120px"/>
        <button class="btn sm" id="s-move">Move</button>
        ${s.vehicle_id ? `<button class="btn sm danger" id="s-detach">Detach from #${s.vehicle_id}</button>` : `<span class="sec-note">currently unlinked</span>`}
      </div></div>
    <h3 style="margin-top:22px">Correlated over time <span class="sec-note" style="text-transform:none;letter-spacing:0"> — units seen together</span></h3>
    ${assoc.length ? `<div style="overflow:auto"><table>
      <thead><tr><th></th><th>Unit</th><th>Kind</th><th>Co-occur</th><th>RSSI corr</th></tr></thead>
      <tbody>${assoc.map(a=>`
      <tr><td><span class="cat-dot c-${CAT[a.other?.category]||'unknown'}"></span></td>
        <td class="mono">${esc(a.other?.identifier||'?')}</td>
        <td><span class="kind-tag k-${a.other?.kind}">${esc(a.other?.kind||'')}</span></td>
        <td>${a.co_count}× ${a.blocked?'<span class="sec-note">(blocked)</span>':''}</td>
        <td>${a.rssi_corr!=null?(a.rssi_corr>=0?'+':'')+a.rssi_corr.toFixed(2):`<span class="sec-note">${a.rssi_samples||0} smp</span>`}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="sec-note">no co-occurrence evidence yet</div>`}

    <h3 style="margin-top:22px">Recent sightings</h3>
    <div class="feed">${sightings.slice(0,40).map(x=>`
      <div class="feed-row"><span class="t">${clock(x.ts)}</span>
        <span class="kind-tag k-${s.kind}">${esc(s.kind)}</span>
        <span class="id mono">${esc(JSON.stringify(x.data).slice(0,60))}</span>
        <span class="rssi">${x.rssi??"—"} dBm</span></div>`).join("")}</div>
  `);
  $("#d-save").onclick = async () => {
    await api(`/api/signals/${id}`, { method: "PATCH", body: JSON.stringify({
      label: $("#d-label").value, notes: $("#d-notes").value, is_baseline: $("#d-base").checked }) });
    closeDrawer(); loadSignals();
  };
  const vl = $("[data-veh]"); if (vl) vl.onclick = () => { closeDrawer(); openVehicle(+vl.dataset.veh); };
  const reload = () => { if (App.view === "vehicles") loadVehicles(); if (App.view === "signals") loadSignals(); };
  $("#s-move").onclick = async () => {
    const vid = parseInt($("#s-vehid").value, 10);
    if (!vid) return alert("Enter a target vehicle id.");
    try { await api(`/api/signals/${id}/reassign`, { method: "POST", body: JSON.stringify({ vehicle_id: vid }) }); }
    catch (e) { return alert("Move failed: " + e.message); }
    closeDrawer(); reload();
  };
  const sd = $("#s-detach"); if (sd) sd.onclick = async () => {
    await api(`/api/signals/${id}/reassign`, { method: "POST", body: JSON.stringify({ vehicle_id: null }) });
    closeDrawer(); reload();
  };
}

function rssiChart(vals) {
  if (!vals.length) return `<div class="sec-note" style="margin:10px 0">no RSSI samples</div>`;
  const W = 500, H = 90, min = Math.min(...vals) - 3, max = Math.max(...vals) + 3;
  const rng = Math.max(1, max - min);
  const pts = vals.map((v, i) => [i * (W / Math.max(1, vals.length - 1)),
    H - ((v - min) / rng) * (H - 12) - 6]);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  return `<svg class="spark" style="height:90px" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <path class="fill" d="${line} L${W},${H} L0,${H} Z"/><path class="stroke" d="${line}"/></svg>
    <div class="sec-note">RSSI trend — closer approach = stronger signal</div>`;
}

/* ============================ vehicles ============================ */
async function viewVehicles() {
  $("#main").innerHTML = `
    <div class="view-head"><h1>Correlated Vehicles</h1>
      <span class="sub">clusters of co-occurring identifiers — an RF fingerprint per vehicle</span>
      <div class="spacer"></div>
      <select id="vorder"><option value="score">by exposure score</option><option value="last_seen">by last seen</option></select>
    </div>
    <div class="grid vgrid" id="vgrid"></div>`;
  $("#vorder").onchange = loadVehicles;
  loadVehicles();
}
async function loadVehicles() {
  const order = $("#vorder")?.value || "score";
  const vs = await api(`/api/vehicles?order=${order}`);
  const g = $("#vgrid"); if (!g) return;
  const maxScore = Math.max(50, ...vs.map(v => v.score));
  g.innerHTML = vs.map(v => `
    <div class="card vcard" data-veh="${v.id}" style="--accent:${v.color}">
      <div class="vh"><span class="vlabel">${esc(v.label)}</span><span class="score">${v.score|0}</span></div>
      <div class="meter"><span class="m" style="width:${(v.score/maxScore*100)|0}%"></span></div>
      <div class="cats">${Object.entries(v.categories||{}).map(([c,n])=>
        `<span class="mini-chip"><span class="cat-dot c-${c}"></span>${catLabel[c]||c} ×${n}</span>`).join("")}</div>
      <div class="cats">${v.has_plate?`<span class="plate">PLATE ✓</span>`:`<span class="sec-note">no plate bound</span>`}</div>
      <div class="vmeta"><span>${v.signal_count} identifiers · ${v.detection_count} shots</span>
        <span>seen ${ago(v.last_seen)} ago</span></div>
    </div>`).join("") || `<div class="empty">no confirmed vehicles yet — as units co-occur over time the engine raises proposals in <b>Review</b>; confirm one to form a vehicle.</div>`;
  $$("#vgrid .vcard").forEach(c => c.onclick = () => openVehicle(+c.dataset.veh));
}

async function openVehicle(id) {
  const v = await api(`/api/vehicles/${id}`);
  drawer(`
    <span class="close-x" data-close>×</span>
    <h2>${esc(v.label)}</h2>
    <div class="sec-note">exposure score ${v.score|0} · ${v.signal_count} identifiers · ${v.detection_count} detections</div>
    <div class="meter" style="margin-top:12px"><span class="m" style="width:${Math.min(100,v.score)}%"></span></div>
    ${v.detections?.some(d=>d.plate_text)?`<div style="margin:14px 0">${
      v.detections.filter(d=>d.plate_text).map(d=>`<span class="plate">${esc(d.plate_text)}</span>
        <span class="sec-note"> ${d.region||""} · ${(d.plate_confidence*100|0)}%</span>`).join("<br>")}</div>`:""}
    <div class="field"><label>Label</label><input id="v-label" value="${esc(v.label)}"/></div>
    <div class="field"><label>Notes</label><textarea id="v-notes" rows="2">${esc(v.notes||"")}</textarea></div>
    <button class="btn primary" id="v-save">Save</button>
    <button class="btn ghost" id="v-arch">Archive</button>

    <h3 style="margin-top:22px">Member identifiers
      <span class="sec-note" style="text-transform:none;letter-spacing:0"> — tick to split/detach</span></h3>
    <div style="overflow:auto"><table><tbody>${v.signals.map(s=>`
      <tr>
        <td><input type="checkbox" data-sel="${s.id}"></td>
        <td data-sig="${s.id}" style="cursor:pointer"><span class="cat-dot c-${CAT[s.category]||"unknown"}"></span></td>
        <td class="mono" data-sig="${s.id}" style="cursor:pointer">${esc(s.identifier)}</td>
        <td><span class="kind-tag k-${s.kind}">${esc(s.kind)}</span></td>
        <td>${s.count}×</td>
        <td><button class="btn sm ghost" data-detach1="${s.id}">detach</button></td>
      </tr>`).join("")}</tbody></table></div>
    <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">
      <button class="btn sm" id="v-split">Split selected → new vehicle</button>
      <button class="btn sm danger" id="v-detach">Detach selected</button>
    </div>

    ${v.detections?.length?`<h3 style="margin-top:22px">Detections</h3>
      <div class="grid gallery">${v.detections.map(d=>shotCard(d)).join("")}</div>`:""}

    <h3 style="margin-top:22px">Timeline</h3>
    <div class="feed">${(v.timeline||[]).slice(0,50).map(t=>`
      <div class="feed-row"><span class="t">${clock(t.ts)}</span>
        <span class="kind-tag k-event">sig ${t.signal_id}</span>
        <span class="id">${esc(t.source)}</span><span class="rssi">${t.rssi??"—"} dBm</span></div>`).join("")}</div>
  `);
  $("#v-save").onclick = async () => {
    await api(`/api/vehicles/${id}`, { method: "PATCH", body: JSON.stringify({
      label: $("#v-label").value, notes: $("#v-notes").value }) });
    closeDrawer(); loadVehicles();
  };
  $("#v-arch").onclick = async () => {
    await api(`/api/vehicles/${id}`, { method: "PATCH", body: JSON.stringify({ status: "archived" }) });
    closeDrawer(); loadVehicles();
  };
  $$("[data-sig]", $("#drawer")).forEach(tr => tr.onclick = () => { closeDrawer(); openSignal(+tr.dataset.sig); });

  const selected = () => $$("[data-sel]", $("#drawer")).filter(c => c.checked).map(c => +c.dataset.sel);
  $("#v-split").onclick = async () => {
    const ids = selected();
    if (!ids.length) return alert("Tick the members to split into a new vehicle.");
    if (ids.length === v.signals.length) return alert("Leave at least one member behind, or use Detach.");
    await api(`/api/vehicles/${id}/split`, { method: "POST", body: JSON.stringify({ signal_ids: ids }) });
    closeDrawer(); loadVehicles();
  };
  $("#v-detach").onclick = async () => {
    const ids = selected();
    if (!ids.length) return alert("Tick the members to detach.");
    await api(`/api/vehicles/${id}/detach`, { method: "POST", body: JSON.stringify({ signal_ids: ids }) });
    closeDrawer(); loadVehicles();
  };
  $$("[data-detach1]", $("#drawer")).forEach(b => b.onclick = async (e) => {
    e.stopPropagation();
    await api(`/api/vehicles/${id}/detach`, { method: "POST", body: JSON.stringify({ signal_ids: [+b.dataset.detach1] }) });
    closeDrawer(); loadVehicles();
  });
}

/* ============================ review (suggestions) ============================ */
const KIND_VERB = { form: "Form vehicle", attach: "Attach to vehicle", merge: "Merge vehicles" };
function rssiChip(corr) {
  if (corr == null) return `<span class="mini-chip" title="RSSI profile still gathering">📶 profile…</span>`;
  const c = corr >= 0.7 ? "var(--good)" : corr >= 0.5 ? "var(--warn)" : "var(--crit)";
  return `<span class="mini-chip" title="RSSI-profile agreement (signal strength moving together)">
    📶 <b style="color:${c}">${corr >= 0 ? "+" : ""}${corr.toFixed(2)}</b></span>`;
}
async function viewReview() {
  $("#main").innerHTML = `
    <div class="view-head"><h1>Correlation Review</h1>
      <span class="sub">the engine proposes; you confirm. Nothing merges without your approval.</span>
      <div class="spacer"></div>
      <select id="rstatus"><option value="pending">pending</option><option value="accepted">accepted</option><option value="rejected">rejected</option></select>
      <button class="btn sm danger" id="dismiss-all">Dismiss all pending</button>
    </div>
    <div class="grid" id="rlist" style="grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px"></div>`;
  $("#rstatus").onchange = loadSuggestions;
  $("#dismiss-all").onclick = async () => {
    if (!confirm("Reject ALL pending suggestions? (clears the backlog)")) return;
    const r = await api("/api/suggestions/dismiss-all", { method: "POST", body: "{}" });
    alert(`Dismissed ${r.dismissed} suggestion(s).`);
    loadSuggestions(); refreshStatus();
  };
  loadSuggestions();
}
async function loadSuggestions() {
  const status = $("#rstatus")?.value || "pending";
  const ss = await api(`/api/suggestions?status=${status}`);
  const g = $("#rlist"); if (!g) return;
  g.innerHTML = ss.map(s => {
    const b = s.b ? `<span class="mono">${esc(s.b.identifier)}</span>` : `<span class="sec-note">(from detection)</span>`;
    const conf = Math.round((s.confidence || 0) * 100);
    const plate = s.detection?.plate_text ? `<span class="plate">${esc(s.detection.plate_text)}</span>` : "";
    const actions = status === "pending" ? `
      <div style="display:flex;gap:8px;margin-top:12px">
        <button class="btn primary sm" data-accept="${s.id}">✓ Confirm</button>
        <button class="btn danger sm" data-reject="${s.id}">✕ Reject</button></div>` : "";
    return `<div class="card pad">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
        <span class="mini-chip">${KIND_VERB[s.kind] || s.kind}</span>
        ${rssiChip(s.rssi_corr)}
        <span class="conf" style="margin-left:auto">${s.encounters} enc · ${conf}%</span></div>
      <div class="meter"><span class="m" style="width:${conf}%"></span></div>
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:8px 0">
        <span class="cat-dot c-${CAT[s.a?.category]||'unknown'}"></span><span class="mono">${esc(s.a?.identifier||'?')}</span>
        <span style="color:var(--txt-faint)">↔</span>${b} ${plate}</div>
      <div class="sec-note">${esc(s.rationale||"")}</div>
      ${actions}</div>`;
  }).join("") || `<div class="empty">no ${status} suggestions. As units co-occur over repeated encounters, proposals appear here.</div>`;

  $$("[data-accept]").forEach(b => b.onclick = () => resolveSuggestion(b.dataset.accept, "accept"));
  $$("[data-reject]").forEach(b => b.onclick = () => resolveSuggestion(b.dataset.reject, "reject"));
}
async function resolveSuggestion(id, action) {
  try { await api(`/api/suggestions/${id}/${action}`, { method: "POST" }); }
  catch (e) { alert(`Failed: ${e.message}`); }
  loadSuggestions(); refreshStatus();
}

/* ============================ detections ============================ */
function shotCard(d) {
  return `<div class="card shot">
    <div class="img">${d.image_url ? `<img src="${d.image_url}" loading="lazy"/>` : "▧"}</div>
    <div class="meta">${d.plate_text?`<span class="plate">${esc(d.plate_text)}</span>`:`<span class="sec-note">no plate</span>`}
      <span class="conf">${d.plate_confidence!=null?(d.plate_confidence*100|0)+"%":""} ${clock(d.ts)}</span></div></div>`;
}
async function viewDetections() {
  $("#main").innerHTML = `
    <div class="view-head"><h1>Camera Detections</h1>
      <span class="sub">webcam captures &amp; recovered plates (ALPR)</span>
      <div class="spacer"></div>
      <button class="btn" id="snap">Trigger snapshot</button></div>
    <div class="grid gallery" id="gal"></div>`;
  $("#snap").onclick = async () => {
    try { await api("/api/services/camera/snapshot", { method: "POST" }); }
    catch (e) { alert("Camera not available (mock mode uses simulated captures)."); }
  };
  const ds = await api("/api/detections");
  $("#gal").innerHTML = ds.map(shotCard).join("") ||
    `<div class="empty">no detections yet — captures appear when the camera fires (or the simulator stages one).</div>`;
}

/* ============================ notifications ============================ */
async function viewNotifications() {
  $("#main").innerHTML = `
    <div class="view-head"><h1>Alerts</h1>
      <span class="sub">detections over baseline</span>
      <div class="spacer"></div>
      <button class="btn" id="ackall">Acknowledge all</button></div>
    <div class="notif-list" id="nlist"></div>`;
  $("#ackall").onclick = async () => { await api("/api/notifications/ack-all", { method: "POST" }); viewNotifications(); refreshStatus(); };
  const ns = await api("/api/notifications?limit=200");
  $("#nlist").innerHTML = ns.map(n => `
    <div class="notif ${n.level} ${n.acknowledged?"ack":""}">
      <span class="bar"></span>
      <div><div class="nt">${esc(n.title)}</div><div class="nb">${esc(n.body)}</div>
        <div class="nx">${new Date(n.ts).toLocaleString()} · ${esc(n.rule)}</div></div>
      ${n.acknowledged?"":`<button class="btn sm" data-ack="${n.id}">Ack</button>`}
    </div>`).join("") || `<div class="empty">no alerts</div>`;
  $$("[data-ack]").forEach(b => b.onclick = async () => {
    await api(`/api/notifications/${b.dataset.ack}/ack`, { method: "POST" });
    viewNotifications(); refreshStatus();
  });
}

/* ============================ settings ============================ */
async function viewSettings() {
  const st = App.status || await api("/api/system/status");
  $("#main").innerHTML = `
    <div class="view-head"><h1>Settings</h1><span class="sub">baseline &amp; capture configuration</span></div>
    <div class="grid" style="grid-template-columns:1fr 1fr;gap:16px;align-items:start">
      <div class="card pad" style="grid-column:1/-1">
        <h3>Capture services &amp; radios <span class="spacer"></span>
          <button class="btn sm" id="cam-adjust" title="Re-optimize exposure/white balance — use after re-aiming or shading the camera">📷 Auto-adjust camera</button>
          <button class="btn sm" id="svc-refresh">Refresh</button></h3>
        <div id="settings-services"></div>
      </div>
      <div class="card pad">
        <h3>Baseline</h3>
        <p class="sec-note">Tag your own devices/environment so they never raise "new over baseline" alerts. Run capture for a warm-up period, then learn.</p>
        <button class="btn primary" id="learn">Learn current as baseline</button>
        <button class="btn danger" id="reset">Reset baseline</button>
        <div class="kv" style="margin-top:14px"><span class="k">Baselined signals</span><span>${st.counts.baselined} / ${st.counts.signals}</span></div>
      </div>
      <div class="card pad">
        <h3>Runtime</h3>
        <div class="kv">
          <span class="k">Version</span><span>${esc(st.version)}</span>
          <span class="k">Mode</span><span>${st.mock_mode?'<span class="badge">MOCK</span> simulator':"live hardware"}</span>
          <span class="k">Services</span><span>${st.services.map(s=>esc(s.name)).join(", ")||"—"}</span>
          <span class="k">Sightings stored</span><span>${st.counts.sightings}</span>
        </div>
        <p class="sec-note">Edit <span class="mono">config.yaml</span> and restart to change radios, adapters, correlation window, and retention. Flip <span class="mono">mock_mode: false</span> once hardware is attached.</p>
      </div>
      <div class="card pad" style="grid-column:1/-1">
        <h3>About &amp; responsible use</h3>
        <p class="sec-note">Nightjar is a receive-only privacy-exposure demonstrator. It shows how vehicles broadcast persistent RF identifiers (TPMS, BLE) that de-anonymize them, and how those bind to a plate via ALPR. Operate only on RF broadcast in the clear, in an environment you are authorized to monitor. Nightjar never transmits.</p>
      </div>
    </div>`;
  $("#learn").onclick = async () => { await api("/api/baseline/learn", { method: "POST", body: "{}" }); refreshStatus(); viewSettings(); };
  $("#reset").onclick = async () => { if(confirm("Clear all baseline flags?")){ await api("/api/baseline/reset", { method: "POST" }); refreshStatus(); viewSettings(); } };
  renderSettingsServices();
  $("#svc-refresh").onclick = () => refreshStatus().then(renderSettingsServices);
  $("#cam-adjust").onclick = async () => {
    try {
      await api("/api/services/camera/auto-adjust", { method: "POST" });
      alert("Camera auto-adjust applied: auto-exposure + auto white balance, backlight off.");
    } catch (e) {
      alert("Auto-adjust failed (is the camera service running?): " + e.message);
    }
  };
}
function renderSettingsServices() {
  const el = $("#settings-services"); if (!el || !App.status) return;
  el.innerHTML = App.status.services.map(s => `
    <div class="svc-row">
      <span class="status-dot ${s.status}"></span>
      <div style="flex:1">
        <div class="svc-name">${esc(s.name)}
          <span class="sec-note">· ${esc(s.status)}${s.stats?.emitted ? " · " + s.stats.emitted + " emitted" : ""}</span></div>
        <div class="svc-desc">${esc(svcInfo(s)) || esc(s.description || "")}${s.last_error ? " — <span style='color:var(--crit)'>" + esc(s.last_error) + "</span>" : ""}</div>
      </div>
      <div style="display:flex;gap:6px">${svcButtons(s)}</div>
    </div>`).join("") || `<div class="empty">no services configured</div>`;
  wireSvcButtons(el);
}

/* ============================ drawer + toast ============================ */
function drawer(html) {
  $("#drawer-inner").innerHTML = html;
  $("#drawer").classList.remove("hidden");
  $$("[data-close]").forEach(x => x.onclick = closeDrawer);
}
function closeDrawer() { $("#drawer").classList.add("hidden"); }
$("#drawer").onclick = (e) => { if (e.target.id === "drawer") closeDrawer(); };

function toast(n) {
  const t = document.createElement("div");
  t.className = `toast ${n.level}`;
  t.innerHTML = `<div class="tt">${esc(n.title)}</div><div class="tb">${esc(n.body)}</div>`;
  $("#toasts").appendChild(t);
  t.onclick = () => t.remove();
  setTimeout(() => t.remove(), n.level === "critical" ? 12000 : 7000);
}

function debounce(fn, ms) { let h; return (...a) => { clearTimeout(h); h = setTimeout(() => fn(...a), ms); }; }

/* ============================ boot ============================ */
function boot() {
  $$(".navitem").forEach(n => n.onclick = () => go(n.dataset.view));
  const initial = NAV.includes(location.hash.slice(1)) ? location.hash.slice(1) : "dashboard";
  refreshStatus().then(() => go(initial));
  connectWS();
}

// legal gate
(function legal() {
  const gate = $("#legal-gate");
  if (localStorage.getItem("nightjar_ack") === "1") { gate.classList.add("hidden"); boot(); return; }
  gate.classList.remove("hidden");
  $("#legal-ok").onchange = e => $("#legal-continue").disabled = !e.target.checked;
  $("#legal-continue").onclick = () => {
    localStorage.setItem("nightjar_ack", "1"); gate.classList.add("hidden"); boot();
  };
})();
