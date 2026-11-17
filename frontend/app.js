document.documentElement.dataset.theme = localStorage.theme || "";
// SalesDesk CRM frontend. Vanilla JS (ES module). All authorisation is enforced by the backend.
const CFG = window.APP_CONFIG;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const S = { me: null, perms: [], stages: [], outcomes: [], sources: [], users: [], types: [], oppStages: [], campaigns: [], obt: {}, token: null, fb: null };
const can = p => S.perms.includes("*") || S.perms.includes(p);
const uname = id => (S.users.find(u => u.id === id) || {}).name || "Unassigned";
const fdate = s => s ? new Date(s).toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";
const fday = s => s ? new Date(s).toLocaleDateString([], { day: "2-digit", month: "short", year: "numeric" }) : "—";
const money = n => "$" + Number(n || 0).toLocaleString();
const isoLocal = d => new Date(d.getTime() - d.getTimezoneOffset() * 6e4).toISOString().slice(0, 16);
const toISO = v => v ? new Date(v).toISOString() : null;
const stageCls = s => "s" + Math.max(0, S.stages.indexOf(s)) % 10;
const pill = s => `<span class="pill ${stageCls(s)}">${esc(s)}</span>`;
const opts = (arr, sel, blank) => (blank != null ? `<option value="">${esc(blank)}</option>` : "") + arr.map(a => { const [v, l] = Array.isArray(a) ? a : [a, a]; return `<option value="${esc(v)}" ${v === sel ? "selected" : ""}>${esc(l)}</option>`; }).join("");
const userOpts = (sel, blank) => opts(S.users.map(u => [u.id, `${u.name} (${u.role})`]), sel, blank);

// ---------------------------------------------------------------- api
async function headers() {
  if (CFG.AUTH_ENABLED) { const t = S.fb ? await S.fb.getToken() : null; return t ? { Authorization: "Bearer " + t } : {}; }
  return { "X-Mock-User": localStorage.getItem("mockUser") || "u_admin" };
}
// ---- loading feedback: top bar + "what is happening" pill for anything slower than a blink
const LOAD = { n: 0, show: null, slow: null, label: "" };
const LABELS = [[/sync-campaigns/, "Syncing campaigns and leads from Apollo"], [/apollo\/search/, "Searching Apollo"], [/apollo\/import/, "Importing leads"], [/enrichment/, "Enriching leads"], [/imports\/upload/, "Uploading file"], [/imports\/.*\/(confirm|preview)/, "Processing import"], [/ai\/chat/, "Thinking"], [/today\/run/, "Running suggestions"]];
const describe = (method, path) => (LABELS.find(([r]) => r.test(path)) || [])[1] || (method === "GET" ? "Loading" : "Saving");
function loadStart(label, method) {
  LOAD.n++; LOAD.label = label; if (LOAD.n > 1) return;
  LOAD.show = setTimeout(() => {
    $("#topbar").classList.add("on"); $("#busyMsg").textContent = LOAD.label + "…"; $("#busy").classList.remove("hidden");
    LOAD.slow = setTimeout(() => { $("#busyMsg").textContent = LOAD.label + "… still working, this one is slow"; }, 6000);
  }, method === "GET" ? 1200 : 300);
}
function loadEnd() {
  if (--LOAD.n > 0) return; LOAD.n = 0; clearTimeout(LOAD.show); clearTimeout(LOAD.slow);
  $("#topbar").classList.remove("on"); $("#busy").classList.add("hidden");
}
async function signOut() {
  try { await S.fb.A.signOut(S.fb.auth); } catch { }
  try { localStorage.removeItem("crm_uid"); } catch { }
  history.replaceState(null, "", location.pathname); location.reload();       // next person always starts at the beginning
}
let expiring = false;
function sessionExpired() { if (expiring) return; expiring = true; toast("Your session expired. Please sign in again.", true); setTimeout(signOut, 1500); }
async function api(path, { method = "GET", body, form, raw } = {}) {
  loadStart(describe(method, path), method);
  try {
    const h = await headers(); let b;
    if (form) b = form; else if (body !== undefined) { h["Content-Type"] = "application/json"; b = JSON.stringify(body); }
    let r; try { r = await fetch(CFG.API_BASE + path, { method, headers: h, body: b }); }
    catch { const e = new Error("Can't reach the server. Check your connection and try again."); e.code = "network"; throw e; }
    if (raw && r.ok) return r;
    let j = null; try { j = await r.json(); } catch { }
    if (!r.ok) {
      const e = new Error(j?.error?.message || (r.status >= 500 ? "The server had a problem. Please try again." : "Request failed")); e.status = r.status; e.code = j?.error?.code;
      if (r.status === 401 && CFG.AUTH_ENABLED && S.me) sessionExpired();
      throw e;
    }
    return j;
  } finally { loadEnd(); }
}
async function download(path, name, method = "GET", body) {
  try { const r = await api(path, { raw: true, method, body }); const u = URL.createObjectURL(await r.blob()); const a = Object.assign(document.createElement("a"), { href: u, download: name }); a.click(); URL.revokeObjectURL(u); }
  catch (e) { toast(e.message, true); }
}
const qs = o => { const p = new URLSearchParams(); Object.entries(o).forEach(([k, v]) => { if (v !== "" && v != null) p.set(k, v); }); return p.toString(); };

// ---------------------------------------------------------------- ui helpers
function toast(m, bad) { const t = Object.assign(document.createElement("div"), { className: "toast" + (bad ? " err" : ""), textContent: m }); $("#toasts").append(t); setTimeout(() => t.remove(), 4200); }
function sk(n = 4) { return Array.from({ length: n }, () => '<div class="sk"></div>').join(""); }
function empty(t, s) { return `<div class="empty"><b>${esc(t)}</b>${esc(s || "")}</div>`; }
function modal(html, { wide } = {}) { const m = $("#modal"); m.innerHTML = `<div class="mbox ${wide ? "wide" : ""}">${html}</div>`; m.classList.remove("hidden"); m.onclick = e => { if (e.target === m) closeModal(); }; const f = $("input,select,textarea,button", m); f && f.focus(); return m; }
function closeModal() { $("#modal").classList.add("hidden"); $("#modal").innerHTML = ""; }
function confirmBox(title, msg, okLabel = "Confirm", danger) {
  return new Promise(res => { modal(`<h2>${esc(title)}</h2><p>${esc(msg)}</p><div class="acts"><button class="btn" id="cn">Cancel</button><button class="btn ${danger ? "danger" : "primary"}" id="cy">${esc(okLabel)}</button></div>`); $("#cn").onclick = () => { closeModal(); res(false); }; $("#cy").onclick = () => { closeModal(); res(true); }; });
}
function promptBox(title, label, okLabel = "Save", required = true) {
  return new Promise(res => { modal(`<h2>${esc(title)}</h2><label>${esc(label)}<textarea id="pv"></textarea></label><div class="acts"><button class="btn" id="cn">Cancel</button><button class="btn primary" id="cy">${esc(okLabel)}</button></div>`); $("#cn").onclick = () => { closeModal(); res(null); }; $("#cy").onclick = () => { const v = $("#pv").value.trim(); if (required && !v) { $("#pv").classList.add("inv"); return; } closeModal(); res(v); }; });
}
function tag(s) { return `<span class="tag">${esc(s)}</span>`; }

// ---------------------------------------------------------------- campaign / channel helpers + inline charts (no libraries)
const CH = { call: ["☎", "Calls"], linkedin: ["in", "LinkedIn"], email: ["✉", "Email"], mixed: ["◎", "Multi-channel"], ops: ["⚙", "Pipeline"] };
const chBadge = c => `<span class="chb ${c}">${CH[c]?.[0] || ""} ${CH[c]?.[1] || esc(c)}</span>`;
const campOf = id => S.campaigns.find(c => c.id === id) || { id: "none", name: "No campaign", channel: "mixed", color: "#9aa0b4" };
const campOpts = (sel, blank) => opts(S.campaigns.map(c => [c.id, c.name]), sel, blank);
const TYPE_OF = { call: "phone_call", linkedin: "linkedin", email: "email", mixed: "phone_call" };
const initials = id => (uname(id) || "?").split(" ").map(x => x[0]).join("").slice(0, 2);
const ownerCell = id => id ? `<span class="avs">${esc(initials(id))}</span>${esc(uname(id))}` : '<span class="muted">Unassigned</span>';
const STAGE_HEX = ["#8a90a6", "#2f9bf0", "#d98a0b", "#6a4fd1", "#1b78c2", "#a548c7", "#14877f", "#17a36b", "#e5484d", "#999999"];
const stageHex = s => STAGE_HEX[Math.max(0, S.stages.indexOf(s)) % 10];
const NEON = ["#22e6ff", "#8b5cff", "#ff3df2", "#9dff4a", "#ffb020", "#ff4d8d"];
const PAL = ["#5a5fe0", "#2f9bf0", "#17a36b", "#f0a020", "#e5484d", "#a548c7", "#14877f"];
const rid = () => Math.random().toString(36).slice(2, 7);

function spark(vals, color) {
  if (!vals.length || !Math.max(...vals)) return `<svg viewBox="0 0 100 34" preserveAspectRatio="none" aria-hidden="true"><line x1="0" y1="30" x2="100" y2="30" style="stroke:var(--border)" stroke-width="2"/></svg>`;
  const mx = Math.max(...vals), n = vals.length, pts = vals.map((v, i) => [i * 100 / Math.max(1, n - 1), 30 - (v / mx) * 26]); const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" "), g = "sp" + rid();
  return `<svg viewBox="0 0 100 34" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="${g}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:${color};stop-opacity:.3"/><stop offset="1" style="stop-color:${color};stop-opacity:0"/></linearGradient></defs><path d="${d} L100 34 L0 34Z" fill="url(#${g})"/><path d="${d}" fill="none" style="stroke:${color}" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>`;
}
function areaChart(labels, series, h = 230) {
  const W = 720, L = 34, R = 8, T = 10, B = 24, n = labels.length, top = Math.max(4, Math.ceil(Math.max(1, ...series.flatMap(s => s.vals)) / 4) * 4);
  const x = i => L + (n > 1 ? i * (W - L - R) / (n - 1) : 0), y = v => T + (h - T - B) * (1 - v / top);
  const grid = [0, 1, 2, 3, 4].map(k => { const v = top * k / 4; return `<line class="grid-l" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${Math.round(v)}</text>`; }).join("");
  const step = Math.max(1, Math.ceil(n / 8)), xl = labels.map((l, i) => i % step ? "" : `<text x="${x(i)}" y="${h - 6}" text-anchor="middle">${esc(l)}</text>`).join("");
  const paths = series.map(s => { const d = s.vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" "), g = "ar" + rid(); return `${s.area ? `<defs><linearGradient id="${g}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:${s.color};stop-opacity:.28"/><stop offset="1" style="stop-color:${s.color};stop-opacity:0"/></linearGradient></defs><path d="${d} L${x(n - 1)} ${y(0)} L${x(0)} ${y(0)}Z" fill="url(#${g})"/>` : ""}<path d="${d}" fill="none" style="stroke:${s.color}" stroke-width="${s.dashed ? 1.6 : 2.5}" ${s.dashed ? 'stroke-dasharray="5 4"' : ""} stroke-linejoin="round" stroke-linecap="round"/>`; }).join("");
  const bw = (W - L - R) / Math.max(1, n), hits = labels.map((l, i) => `<rect x="${x(i) - bw / 2}" y="${T}" width="${bw}" height="${h - T - B}" fill="transparent"><title>${esc(l)}: ${series.map(s => s.name + " " + s.vals[i]).join(" · ")}</title></rect>`).join("");
  return `<div class="chart"><svg viewBox="0 0 ${W} ${h}" role="img" aria-label="${esc(series.map(s => s.name).join(", "))} over time">${grid}${xl}${paths}${hits}</svg></div><div class="legend" style="margin-top:6px">${series.map(s => `<span><i style="background:${s.color}${s.dashed ? ";opacity:.5" : ""}"></i>${esc(s.name)}</span>`).join("")}</div>`;
}
function donut(items, center) {
  const tot = items.reduce((a, i) => a + i.value, 0); if (!tot) return empty("No data in this period");
  const R = 52, C = 2 * Math.PI * R; let off = 0;
  const segs = items.map(i => { const len = C * i.value / tot, s = `<circle r="${R}" cx="75" cy="75" fill="none" style="stroke:${i.color}" stroke-width="20" stroke-dasharray="${len} ${C - len}" stroke-dashoffset="${-off}" transform="rotate(-90 75 75)"><title>${esc(i.label)}: ${i.value}</title></circle>`; off += len; return s; }).join("");
  return `<div class="donut-wrap"><svg viewBox="0 0 150 150" role="img" aria-label="${esc(center)} breakdown">${segs}<text x="75" y="76" text-anchor="middle" style="font-size:24px;font-weight:700;fill:var(--text)">${tot}</text><text x="75" y="93" text-anchor="middle" style="font-size:11px;fill:var(--muted)">${esc(center)}</text></svg><div class="dl">${items.map(i => `<div><i style="background:${i.color}"></i>${esc(i.label)}<b>${i.value}</b></div>`).join("")}</div></div>`;
}
function ring(p, color, label, val) {
  const R = 36, C = 2 * Math.PI * R, f = Math.min(100, p) / 100 * C;
  return `<div class="ring" style="--rc:${color}"><svg viewBox="0 0 92 92" role="img" aria-label="${esc(label)} ${p}%"><circle cx="46" cy="46" r="${R}" fill="none" style="stroke:var(--surface-2)" stroke-width="9"/><circle cx="46" cy="46" r="${R}" fill="none" style="stroke:${color}" stroke-width="9" stroke-linecap="round" stroke-dasharray="${f} ${C}" transform="rotate(-90 46 46)"/><text x="46" y="51" text-anchor="middle" style="font-size:17px;font-weight:700;fill:var(--text)">${p}%</text></svg><b>${esc(label)}</b><span class="rv">${esc(val)}</span></div>`;
}
function funnelHtml(rows) {
  const mx = Math.max(1, ...rows.map(r => r.count));
  return rows.map(r => `<div class="fun"><span class="n">${esc(r.stage)}</span><span class="t"><i style="width:${Math.round(100 * r.count / mx)}%"></i></span><span class="c">${r.count} <span class="muted">(${r.conversion}%)</span></span></div>`).join("");
}
const insightHtml = i => `<div class="ins"><b>${esc(i.title)}</b>${esc(i.what)}<div>${esc(i.why)} <b style="display:inline">Do: ${esc(i.action)}</b></div><div class="meta">${esc(i.metric)} · ${esc(i.period)}</div></div>`;
const dchip = (pct, inv) => pct == null ? "" : `<span class="dchip ${pct === 0 ? "flat" : ((pct > 0) !== !!inv ? "up" : "down")}">${pct > 0 ? "▲" : pct < 0 ? "▼" : "•"} ${Math.abs(pct)}%</span>`;

// ---------------------------------------------------------------- navigation
const NAV = [
  ["today", "Today", "◎", "reports:read"], ["overview", "Overview", "▦", "reports:read"], ["leads", "Leads", "☰", "leads:read"], ["discovery", "Lead Discovery", "⌕", "import"],
  ["workspace", "Sales Activity", "✆", "activities:write"], ["followups", "Activities & Follow-ups", "✓", "tasks:read"], ["opportunities", "Opportunities", "◆", "opps:read"],
  ["reports", "Reports & Analytics", "▤", "reports:read"], ["assistant", "AI Assistant", "✨", "ai:use"], ["integrations", "Integrations", "⇄", "integrations:read"], ["settings", "Settings", "⚙", "settings:read"]];
const PAGES = {};
function renderNav() {
  const cur = location.hash.slice(2).split("/")[0] || "today";
  $("#nav").innerHTML = NAV.filter(n => can(n[3])).map(n => `<a class="nav-i ${n[0] === cur ? "on" : ""}" href="#/${n[0]}" data-p="${n[0]}"><span class="ic">${n[2]}</span>${esc(n[1])}</a>`).join("");
  $("#side").classList.remove("open");
}
let ROUTE_SEQ = 0;
async function route() {
  const my = ++ROUTE_SEQ; let p = location.hash.slice(2).split("/")[0] || "today"; let item = NAV.find(n => n[0] === p);
  if (!item || !can(item[3])) { p = NAV.find(n => can(n[3]))?.[0] || "overview"; item = NAV.find(n => n[0] === p); history.replaceState(null, "", "#/" + p); }   // keep the URL honest
  renderNav(); const view = $("#view"), v = document.createElement("div");      // fresh container: a slow old page can never paint over a newer one
  v.innerHTML = `<div class="pageload"><div class="spin"></div><span class="pl-msg">Loading ${esc(item?.[1] || "page")}…</span></div>${sk(5)}`; view.replaceChildren(v);
  const slow = setTimeout(() => { const m = v.querySelector(".pl-msg"); if (m) m.textContent = `Still loading ${item?.[1] || "page"}… the server may be waking up.`; }, 5000);
  try { await PAGES[p](v); }
  catch (e) { if (my !== ROUTE_SEQ) return; v.innerHTML = `<div class="card">${empty("Could not load this page", e.message)}<div style="text-align:center"><button class="btn primary" id="retry">Try again</button></div></div>`; v.querySelector("#retry").onclick = route; }
  finally { clearTimeout(slow); }
  if (my === ROUTE_SEQ) view.focus({ preventScroll: true });
}
const head = (t, sub, actions = "") => `<div class="page-h"><div><h1>${esc(t)}</h1>${sub ? `<p>${esc(sub)}</p>` : ""}</div><div class="bar" style="margin:0">${actions}</div></div>`;

// ---------------------------------------------------------------- TODAY (command center; nothing changes until a person approves)
const AGN = { triage: "triage", prep: "call prep", scout: "signal scout", cleaner: "data cleaner", watcher: "stale watcher", digest: "digest" };
const agTag = a => `<span class="ag ${esc(a)}">${esc(AGN[a] || a)}</span>`;
const scCls = n => n >= 60 ? "sc" : "sc mid";
const ago = s => { if (!s) return "never"; const m = Math.round((Date.now() - new Date(s)) / 6e4); return m < 1 ? "just now" : m < 60 ? m + " min ago" : m < 1440 ? Math.round(m / 60) + " h ago" : Math.round(m / 1440) + " d ago"; };
const TILES = [["call", "☎", "Calls to make", "var(--ok)", "var(--ok-soft)"], ["linkedin", "in", "LinkedIn touches", "var(--info)", "var(--info-soft)"], ["email", "✉", "Emails to send", "var(--warn)", "var(--warn-soft)"], ["ops", "⚙", "Pipeline & data", "var(--primary)", "var(--primary-soft)"]];
const T = { ch: "", owner: "", open: {}, more: {} };
PAGES.today = async v => {
  const d = await api("/today"); const st = d.status; let all = d.suggestions;
  if (T.owner) all = all.filter(x => (x.owner_id || "__team") === T.owner);
  const n = c => all.filter(x => x.channel === c).length; const shown = T.ch ? all.filter(x => x.channel === T.ch) : all;
  if (!shown.find(x => x.id === S.todaySel)) S.todaySel = shown[0]?.id;
  const hh = h => new Date(2000, 0, 1, h).toLocaleTimeString([], { hour: "numeric" });
  const owners = [...new Set(d.suggestions.map(x => x.owner_id || "__team"))];
  const groups = ["call", "linkedin", "email", "ops"].map(c => ({ c, items: shown.filter(x => x.channel === c) })).filter(g => g.items.length);
  const item = x => `<div class="sg ${x.id === S.todaySel ? "on" : ""}" data-id="${x.id}"><div class="r"><span class="t">${esc(x.title)}</span>${x.kind !== "digest" ? `<span class="${scCls(x.priority)}" title="Priority score">${x.priority}</span>` : ""}</div><div class="m">${agTag(x.agent)} ${esc(x.lead ? campOf(x.lead.campaign_id).name : "Team")}${x.owner_id ? " · " + esc(uname(x.owner_id)) : ""}</div></div>`;
  v.innerHTML = head("Today", "Suggestions prepared for each channel. Nothing changes until you approve it.", `<div class="runbar"><span>Last run ${ago(st.last_run)}${st.last_by ? " by " + esc(st.last_by) : ""} · next ${fdate(st.next_run)} (${st.hours.map(hh).join(", ")})</span><span class="pill ${st.provider.demo ? "s2" : "s7"}" title="${esc(st.provider.note || "")}">Signals: ${esc(st.provider.label)}</span>${can("agents:run") ? '<button class="btn primary sm" id="runNow">Run now</button>' : ""}</div>`) +
    `<div class="tiles">${TILES.map(([c, ic, l, col, bg]) => `<div class="tile ${T.ch === c ? "on" : ""}" data-ch="${c}"><span class="ic" style="background:${bg};color:${col}">${ic}</span><div><div class="v">${n(c)}</div><div class="l">${l}</div></div></div>`).join("")}</div>
    <div class="cc"><section class="card"><div class="ch"><h2>Waiting for approval <span class="muted">${shown.length}</span></h2>${S.me.role !== "caller" && owners.length > 1 ? `<select id="tow" aria-label="Filter by person" style="max-width:130px"><option value="">Everyone</option>${owners.map(o => `<option value="${o}" ${T.owner === o ? "selected" : ""}>${o === "__team" ? "Team items" : esc(uname(o))}</option>`).join("")}</select>` : ""}</div>
      <div class="scroll">${groups.length ? groups.map(g => { const open = T.open[g.c] !== false, lim = T.more[g.c] ? 99 : 5; return `<div class="sgg"><div class="sgg-h" data-g="${g.c}"><span>${open ? "▾" : "▸"} ${esc(CH[g.c][1])}</span><span>${g.items.length}</span></div>${open ? g.items.slice(0, lim).map(item).join("") + (g.items.length > lim ? `<button class="btn sm more" data-more="${g.c}">Show ${g.items.length - lim} more</button>` : "") : ""}</div>`; }).join("") : empty("Nothing waiting", can("agents:run") ? "Press Run now to look for suggestions." : "The next scheduled run will add suggestions.")}</div></section>
    <section id="ccm" class="scroll"></section>
    <div style="gap:16px;display:flex;flex-direction:column"><div class="card"><h2>This week</h2>${[["Leads added", d.week.leads_added], ["Call attempts", d.week.calls], ["Meetings booked", d.week.meetings], ["Follow-ups completed", d.week.tasks_done], ["Signals found", d.week.signals_found], ["Approved / dismissed", `${d.week.approved} / ${d.week.dismissed}`]].map(([l, x]) => `<div class="wk"><span>${l}</span><b>${x}</b></div>`).join("")}</div>
    <div class="card feed" style="flex:1;min-height:0;display:flex;flex-direction:column"><h2>Activity</h2><div class="scroll">${d.feed.length ? d.feed.map(e => `<div class="e">${agTag(e.agent)}<span>${esc(e.text)}</span><span class="muted">${ago(e.at)}</span></div>`).join("") : empty("No activity yet")}</div></div></div></div>
    <div class="card" style="margin-top:16px"><div class="ch"><h2>Pipeline</h2><span class="muted">Select a stage to open those leads</span></div><div class="lanes">${(() => { const mx = Math.max(1, ...d.lanes.map(x => x.count)); return d.lanes.map(x => `<div class="lane" data-s="${esc(x.stage)}"><span class="muted" style="font-size:12px">${esc(x.stage)}</span><b>${x.count}</b><i style="width:${Math.round(100 * x.count / mx)}%;background:${stageHex(x.stage)}"></i></div>`).join(""); })()}</div></div>`;
  $$(".tile", v).forEach(e => e.onclick = () => { T.ch = T.ch === e.dataset.ch ? "" : e.dataset.ch; PAGES.today(v); });
  $$(".sg", v).forEach(e => e.onclick = () => { S.todaySel = e.dataset.id; S.todayEdit = false; PAGES.today(v); });
  $$("[data-g]", v).forEach(e => e.onclick = () => { T.open[e.dataset.g] = T.open[e.dataset.g] === false; PAGES.today(v); });
  $$("[data-more]", v).forEach(e => e.onclick = () => { T.more[e.dataset.more] = true; PAGES.today(v); });
  $$(".lane", v).forEach(e => e.onclick = () => { LV.stage = e.dataset.s; LV.cursors = [""]; location.hash = "#/leads"; });
  if ($("#tow")) $("#tow").onchange = e => { T.owner = e.target.value; PAGES.today(v); };
  if ($("#runNow")) $("#runNow").onclick = async e => { e.target.disabled = true; e.target.textContent = "Running…"; try { const r = await api("/today/run", { method: "POST" }); toast(`Run finished: ${Object.values(r.created).reduce((a, b) => a + b, 0)} new suggestions`); } catch (er) { toast(er.message, true); } PAGES.today(v); };
  const x = shown.find(i => i.id === S.todaySel), m = $("#ccm"); if (!x) { m.innerHTML = `<div class="card">${empty("Pick a suggestion", "Details and the evidence behind it appear here.")}</div>`; return; }
  const l = x.lead, act = can("agents:act"), edit = !!S.todayEdit, isDraft = ["linkedin", "email"].includes(x.channel) && x.kind === "call_brief", cm = l ? campOf(l.campaign_id) : null;
  const brief = l ? `<div class="card"><div class="ch"><div><h2>${esc(l.full_name)}</h2><div class="muted">${esc(l.title)}${l.title && l.company ? " at " : ""}${esc(l.company)}</div></div><div>${pill(l.stage)}</div></div>
    <div class="bar" style="margin:8px 0">${chBadge(x.channel)} <span class="chb mixed" style="--x:0">${esc(cm.name)}</span></div>
    <dl class="kv">${dd("Phone", l.phone ? `<a href="tel:${esc(l.phone)}">${esc(l.phone)}</a>` : "")}${dd("Email", esc(l.email))}${dd("LinkedIn", l.linkedin_url ? `<a href="${esc(l.linkedin_url)}" target="_blank" rel="noopener noreferrer">Open profile</a>` : "")}${dd("Owner", esc(uname(l.owner_id)))}${dd("Touches", l.touches || 0)}${dd("Last touch", x.recent.length ? esc(x.recent[0].outcome) + " · " + fdate(x.recent[0].at) : "")}</dl>
    <h3 style="margin-top:12px">Why this is here</h3><ul style="margin:6px 0;padding-left:18px">${x.evidence.filter(e => !e.source).map(e => `<li>${esc(e.text)}</li>`).join("")}</ul>
    ${x.signals.length ? `<h3>Signals</h3>${x.signals.map(s => `<div class="sig"><b>${esc(s.summary)}</b><div class="m">${s.demo ? "Demo signal, not real" : `Unverified web result · ${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">source</a>` : ""}${s.date ? " · " + esc(s.date) : ""} · check before you quote it`}</div></div>`).join("")}` : ""}
    <div class="bar" style="margin:10px 0 0"><button class="btn sm" id="ol">Open lead</button></div></div>` : "";
  const lbl = isDraft ? `${CH[x.channel][1]} draft` : "Proposed";
  m.innerHTML = x.kind === "digest" ? `<div class="card"><div class="ch"><h2>${agTag(x.agent)} ${esc(x.title)}</h2></div><p style="white-space:pre-wrap">${esc(x.body)}</p><div style="text-align:right"><button class="btn" id="dm">Mark as read</button></div></div>` :
    `<div class="prop" style="margin-top:0"><div class="ch"><b>${agTag(x.agent)} ${lbl}: ${esc(x.action?.title || x.title)}</b><span class="wait">waiting for your ok</span></div>
    ${edit && x.action?.type === "create_task" ? `<div class="form2" style="margin:8px 0"><label>Task title<input id="et" value="${esc(x.action.title)}"></label><label>Due<input type="datetime-local" id="ed" value="${isoLocal(new Date(x.action.due_at))}"></label></div>` : ""}
    ${x.action?.type === "stage" ? `<p>Move to <b>${esc(x.action.stage)}</b>.</p>` : ""}${x.action?.type === "assign" ? `<p>New owner: <b>${esc(uname(x.action.owner_id))}</b></p>` : ""}${x.action?.type === "enrich" ? '<p class="warnc">Uses provider credits. You confirm before it runs.</p>' : ""}${x.action?.type === "archive" ? '<p class="warnc">The lead is archived, not deleted.</p>' : ""}${isDraft ? '<p class="muted" style="margin:6px 0">The CRM never sends messages. Review, copy, and send it yourself, then log the activity.</p>' : ""}
    <textarea id="eb" ${edit ? "" : "readonly"} aria-label="Suggestion text">${esc(x.body)}</textarea>
    ${act ? `<div class="bar" style="margin:10px 0 0"><button class="btn primary" id="ap">Approve${x.action?.type === "create_task" ? " & create task" : ""}</button>${x.action?.type === "create_task" ? `<button class="btn" id="ed2">${edit ? "Cancel edit" : "Edit"}</button>` : ""}${isDraft || x.channel === "call" ? '<button class="btn" id="cp">Copy text</button>' : ""}<button class="btn" id="dm">Dismiss</button></div>` : '<p class="muted">Your role can view suggestions but not act on them.</p>'}</div>${brief}`;
  const on = (i, f) => $(i, m) && ($(i, m).onclick = f);
  on("#ol", () => openLead(l.id)); on("#ed2", () => { S.todayEdit = !S.todayEdit; PAGES.today(v); });
  on("#cp", async () => { try { await navigator.clipboard.writeText($("#eb").value); toast("Copied"); } catch { toast("Copy failed. Select the text and copy it manually.", true); } });
  on("#dm", async () => { const r = x.kind === "digest" ? "" : await promptBox("Dismiss suggestion", "Reason (optional, helps tune future suggestions)", "Dismiss", false); if (r === null) return; try { await api(`/suggestions/${x.id}/dismiss`, { method: "POST", body: { reason: r } }); toast("Dismissed"); S.todayEdit = false; PAGES.today(v); } catch (e) { toast(e.message, true); } });
  on("#ap", async () => {
    const what = { create_task: "create this follow-up task", assign: `assign the lead to ${uname(x.action?.owner_id)}`, stage: `move the lead to ${x.action?.stage}`, archive: "archive this duplicate", enrich: `enrich ${x.action?.lead_ids?.length} leads (may use credits)` }[x.action?.type] || "apply this";
    if (!await confirmBox("Approve suggestion", `This will ${what}. It is recorded in the audit log.`, "Approve")) return;
    const body = edit ? { title: $("#et")?.value, body: $("#eb").value, due_at: toISO($("#ed")?.value) } : {};
    try { await api(`/suggestions/${x.id}/approve`, { method: "POST", body }); toast("Approved and applied"); S.todayEdit = false; PAGES.today(v); } catch (e) { toast(e.message, true); }
  });
};

// ---------------------------------------------------------------- OVERVIEW (executive dashboard)
const OV = { days: 30, campaign: "", owner: "" };
PAGES.overview = async v => {
  const d = await api("/analytics/overview?" + qs({ days: OV.days, campaign: OV.campaign, owner: OV.owner })); const k = d.kpis, s = d.series, eng = d.engagement;
  const card = (label, val, chip, sp, col, sub) => `<div class="card kp"><span class="l">${label}</span><span class="v">${val}</span><span>${chip} <span class="muted" style="font-size:11px">${sub || ""}</span></span>${sp ? spark(sp, col) : ""}</div>`;
  const mini = (l, x, bad) => `<div class="mini" title="${esc(k[x]?.definition || "")}"><div class="l">${l}</div><div class="v ${bad && k[x].value ? "bad" : ""}">${k[x].value}</div></div>`;
  const chRows = d.channels, chMax = Math.max(1, ...chRows.map(r => r.touches)), chCol = { phone_call: "var(--ok)", linkedin: "var(--info)", email: "var(--warn)" };
  v.innerHTML = head("Overview", `Executive view · last ${OV.days} days compared with the previous ${OV.days}`, `<div class="seg" role="group" aria-label="Period">${[7, 30, 90].map(x => `<button class="${OV.days === x ? "on" : ""}" data-d="${x}">${x}d</button>`).join("")}</div><select id="ovc" aria-label="Campaign">${campOpts(OV.campaign, "All campaigns")}</select>${S.me.role === "caller" ? "" : `<select id="ovo" aria-label="Owner">${opts([["unassigned", "Unassigned"], ...S.users.filter(u => ["caller", "outreach"].includes(u.role)).map(u => [u.id, u.name])], OV.owner, "All owners")}</select>`}`) +
    `<div class="kp-grid">${card("Total leads", k.total_leads.value, dchip(k.new_leads.pct), s.new_leads, "var(--primary)", `${k.new_leads.value} new`)}${card("Outreach touches", k.touches.value, dchip(k.touches.pct), s.touches, "var(--info)", "calls · LinkedIn · email")}
    ${card("Engagement rate", eng.value + "%", dchip(eng.previous ? Math.round(eng.value - eng.previous) : null), s.engaged, "var(--ok)", "points vs prev")}${card("Meetings booked", k.meetings.value, dchip(k.meetings.pct), s.meetings, "var(--warn)", "")}
    ${card("Open pipeline", money(d.money.open), `<span class="dchip flat">${k.opps.value} new opps</span>`, null, "", "")}${card("Deals won", k.won.value, dchip(k.won.pct), null, "", money(d.money.won) + " won value")}</div>
    <div class="mini-grid">${mini("Ready to contact", "ready")}${mini("Unique prospects contacted", "unique_contacted")}${mini("Qualified", "qualified")}${mini("Follow-ups due today", "due")}${mini("Overdue follow-ups", "overdue", 1)}${mini("Lost", "lost")}</div>
    <div class="grid g-main" style="margin-top:16px"><div class="card"><div class="ch"><h2>Outreach activity</h2><span class="muted">${d.period.bucket_days > 1 ? d.period.bucket_days + "-day buckets" : "per day"}</span></div>${areaChart(s.labels, [{ name: "Touches", vals: s.touches, color: "var(--primary)", area: 1 }, { name: "Engaged", vals: s.engaged, color: "var(--ok)", area: 1 }, { name: "Touches, previous period", vals: s.prev_touches, color: "var(--muted)", dashed: 1 }])}</div>
    <div class="card"><div class="ch"><h2>New leads by source</h2></div>${donut(d.sources.map((x, i) => ({ label: x.source, value: x.count, color: PAL[i % PAL.length] })), "new leads")}</div></div>
    <div class="grid g3" style="margin-top:16px"><div class="card"><h2>Conversion funnel</h2>${funnelHtml(d.funnel)}</div>
    <div class="card"><h2>Channel performance</h2>${chRows.map(r => `<div class="hb"><span class="n">${esc(r.label)}</span><span class="t"><i style="width:${Math.round(100 * r.touches / chMax)}%;background:${chCol[r.channel]}"></i></span><span class="c">${r.touches} · ${r.rate}% engaged</span></div>`).join("")}<p class="muted" style="font-size:12px;margin:10px 0 0">Bar = touches. Engaged = positive reply, accepted request or connected call.</p></div>
    <div class="card"><div class="ch"><h2>Needs attention</h2><a href="#/followups">Open</a></div>${[["Overdue follow-ups", d.attention.counts.overdue, "overdue"], ["Never contacted", d.attention.counts.untouched, "untouched"], ["Gone quiet", d.attention.counts.stale, "overdue"], ["No next action", d.attention.counts.no_next_action, "no_next_action"]].map(([l, n, st]) => `<div class="wk"><span>${l}</span><b class="${n ? "warnc" : ""}">${n}</b></div>`).join("")}${d.attention.overdue.length ? `<h3 style="margin-top:12px">Oldest overdue</h3>${d.attention.overdue.slice(0, 4).map(t => `<div class="wk" style="cursor:pointer" data-l="${t.lead_id}"><span>${esc(t.lead_name)}<div class="muted" style="font-size:11px">${esc(t.title)}</div></span><span class="bad" style="font-size:12px">${fdate(t.due_at)}</span></div>`).join("")}` : ""}</div></div>
    <div class="grid g-main" style="margin-top:16px"><div class="card"><h2>Campaign performance</h2>${d.campaigns.length ? `<div class="tbl-wrap" style="border:0"><table class="tbl"><thead><tr><th>Campaign</th><th>Leads</th><th>Contacted</th><th>Engaged</th><th>Qualified</th><th>Won</th></tr></thead><tbody>${d.campaigns.map(c => `<tr><td><span style="color:${c.color};font-weight:650">●</span> ${esc(c.name)} <span class="tag" title="${esc(CH[c.channel][1])}">${CH[c.channel][0]}</span></td><td>${c.leads}</td><td><span class="meter"><i style="width:${c.contacted_pct}%;background:var(--primary)"></i></span>${c.contacted_pct}%</td><td><span class="meter"><i style="width:${c.engaged_pct}%;background:var(--ok)"></i></span>${c.engaged_pct}%</td><td>${c.qualified}</td><td>${c.won}</td></tr>`).join("")}</tbody></table></div>` : empty("No campaigns yet")}</div>
    <div class="card"><h2>Insights</h2>${d.insights.length ? d.insights.map(insightHtml).join("") : empty("Nothing unusual", "No measurable changes to flag.")}</div></div>`;
  $$("[data-d]", v).forEach(b => b.onclick = () => { OV.days = +b.dataset.d; PAGES.overview(v); });
  $("#ovc").onchange = e => { OV.campaign = e.target.value; PAGES.overview(v); }; if ($("#ovo")) $("#ovo").onchange = e => { OV.owner = e.target.value; PAGES.overview(v); };
  $$("[data-l]", v).forEach(e => e.onclick = () => openLead(e.dataset.l));
};

// ---------------------------------------------------------------- LEADS (monday-style board by campaign, flat table, stage kanban)
const LV = { q: "", stage: "", source: "", owner: "", status: "", campaign: "", channel: "", sort: "created_at", desc: true, cursors: [""], mode: "board" };
const COLS = [["full_name", "Lead"], ["company", "Company"], ["campaign_id", "Campaign", "table"], ["stage", "Stage"], ["owner_id", "Owner"], ["last_activity_at", "Last activity"], ["next_action_due", "Next action"], ["source", "Source"], ["created_at", "Created"]];
const hiddenCols = () => JSON.parse(localStorage.hiddenCols || "[]");
const savedViews = () => JSON.parse(localStorage.savedViews || "[]");
const gcol = () => JSON.parse(localStorage.gcollapsed || "{}");
const GB = {}; let SEL = new Set();
const lvQuery = x => qs({ q: LV.q, stage: LV.stage, source: LV.source, owner: LV.owner, status: LV.status, sort: LV.sort, desc: LV.desc, ...x });
PAGES.leads = async v => {
  v.innerHTML = head("Leads", "Every lead lives in a campaign. Expand a campaign to work its leads.", `${can("assign") ? '<button class="btn" id="addC">+ Campaign</button><button class="btn" id="syncC" title="Import your active Apollo sequences as campaigns, with their contacts as leads">Sync from Apollo</button>' : ""}${can("leads:write") ? '<button class="btn primary" id="addL">+ Add lead</button>' : ""}${can("export") ? '<button class="btn" id="expL">Export CSV</button>' : ""}`) +
    `<div class="bar"><input id="lq" placeholder="Filter by name, company, email, phone" value="${esc(LV.q)}" style="min-width:230px" aria-label="Filter leads">
    <select id="lch" aria-label="Channel">${opts([["call", "Calls"], ["linkedin", "LinkedIn"], ["email", "Email"], ["mixed", "Multi-channel"]], LV.channel, "All channels")}</select><select id="lca" aria-label="Campaign">${campOpts(LV.campaign, "All campaigns")}</select>
    <select id="lst" aria-label="Stage">${opts(S.stages, LV.stage, "All stages")}</select><select id="lsr" aria-label="Source">${opts(S.sources, LV.source, "All sources")}</select>
    ${S.me.role === "caller" ? "" : `<select id="low" aria-label="Owner">${opts([["unassigned", "Unassigned"], ...S.users.map(u => [u.id, u.name])], LV.owner, "All owners")}</select>`}
    <select id="lsu" aria-label="Status">${opts([["overdue", "Overdue next action"], ["no_next_action", "No next action"], ["untouched", "Never contacted"], ["unassigned", "Unassigned"]], LV.status, "Any status")}</select>
    <span class="grow"></span><select id="lsv" aria-label="Saved views">${opts(savedViews().map((x, i) => [i, x.name]), "", "Saved views")}</select><button class="btn sm" id="svv">Save view</button>
    <details style="position:relative"><summary class="btn sm" style="list-style:none">Columns</summary><div class="card" style="position:absolute;right:0;z-index:5;width:190px;display:flex;flex-direction:column;gap:6px">${COLS.map(c => `<label style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" data-c="${c[0]}" ${hiddenCols().includes(c[0]) ? "" : "checked"}>${c[1]}</label>`).join("")}</div></details>
    <div class="seg" role="group" aria-label="View">${[["board", "Board"], ["table", "Table"], ["kanban", "Kanban"]].map(([m, l]) => `<button class="${LV.mode === m ? "on" : ""}" data-m="${m}">${l}</button>`).join("")}</div></div>
    <div id="bulk" class="bar hidden card" style="padding:8px 12px"></div><div id="ltab">${sk(8)}</div>`;
  const reload = () => { LV.cursors = [""]; loadLeads(); };
  const bind = (id, k) => $(id) && ($(id).onchange = e => { LV[k] = e.target.value; reload(); });
  bind("#lst", "stage"); bind("#lsr", "source"); bind("#lsu", "status"); bind("#low", "owner"); bind("#lch", "channel"); bind("#lca", "campaign");
  let t; $("#lq").oninput = e => { clearTimeout(t); t = setTimeout(() => { LV.q = e.target.value; reload(); }, 250); };
  $$("[data-m]", v).forEach(b => b.onclick = () => { LV.mode = b.dataset.m; PAGES.leads(v); });
  $$("[data-c]", v).forEach(c => c.onchange = () => { localStorage.hiddenCols = JSON.stringify($$("[data-c]").filter(x => !x.checked).map(x => x.dataset.c)); loadLeads(); });
  $("#svv").onclick = async () => { const n = await promptBox("Save view", "View name", "Save"); if (!n) return; const a = savedViews(); a.push({ name: n, f: { q: LV.q, stage: LV.stage, source: LV.source, owner: LV.owner, status: LV.status, campaign: LV.campaign, channel: LV.channel } }); localStorage.savedViews = JSON.stringify(a); PAGES.leads(v); toast("View saved"); };
  $("#lsv").onchange = e => { if (e.target.value === "") return; Object.assign(LV, savedViews()[+e.target.value].f); PAGES.leads(v); };
  if ($("#addL")) $("#addL").onclick = () => leadForm(null, LV.campaign || null); if ($("#addC")) $("#addC").onclick = () => campaignModal(() => PAGES.leads(v));
  if ($("#syncC")) $("#syncC").onclick = async e => {
    const b = e.target; b.disabled = true; b.textContent = "Syncing from Apollo… (can take a minute)";
    try {
      const r = await api("/integrations/apollo/sync-campaigns", { method: "POST" }); const m = await api("/meta"); S.campaigns = m.campaigns;
      toast(r.note || `Apollo: ${r.active} active of ${r.apollo_total} sequences (${r.created} new, ${r.updated} updated, ${r.archived} archived). Leads: ${r.leads_created} added, ${r.leads_duplicate} already in CRM${r.truncated ? ". Stopped at 10,000 contacts, run again" : ""}.`, !!r.note);
      PAGES.leads(v);
    } catch (er) { toast(er.message, true); b.disabled = false; b.textContent = "Sync from Apollo"; }
  };
  if ($("#expL")) $("#expL").onclick = () => download("/analytics/export/leads?" + qs({ stage: LV.stage, source: LV.source, owner: LV.owner, campaign: LV.campaign, channel: LV.channel }), "leads.csv");
  await loadLeads();
};
function campaignModal(done) {
  modal(`<h2>New campaign</h2><form id="cf" class="form2"><label class="full">Name*<input name="name" required placeholder="e.g. Healthcare CFOs · LinkedIn"></label><label class="full">Primary channel<select name="channel">${opts([["call", "Calls"], ["linkedin", "LinkedIn"], ["email", "Email"], ["mixed", "Multi-channel"]], "call")}</select></label><div class="acts full"><button type="button" class="btn" id="cn">Cancel</button><button class="btn primary">Create campaign</button></div></form>`);
  $("#cn").onclick = closeModal;
  $("#cf").onsubmit = async e => { e.preventDefault(); try { await api("/campaigns", { method: "POST", body: Object.fromEntries(new FormData(e.target)) }); const m = await api("/meta"); S.campaigns = m.campaigns; closeModal(); toast("Campaign created"); done && done(); } catch (er) { toast(er.message, true); } };
}
async function loadLeads() {
  if (!$("#ltab")) return; if (LV.mode === "board") return loadBoard();
  const box = $("#ltab"), cur = LV.cursors.at(-1);
  const r = await api("/leads?" + lvQuery({ campaign: LV.campaign, channel: LV.channel, cursor: cur, limit: LV.mode === "kanban" ? 100 : 25 }));
  SEL = new Set(); renderBulk();
  if (!r.items.length) { box.innerHTML = `<div class="card">${empty("No leads match", "Clear a filter, or add leads from Lead Discovery.")}</div>`; return; }
  if (LV.mode === "kanban") {
    box.innerHTML = `<div class="kanban">${S.stages.map(s => { const g = r.items.filter(l => l.stage === s); return `<div class="col" data-s="${esc(s)}"><h3>${esc(s)} <span class="muted">${g.length}</span></h3><div class="cards">${g.map(l => `<div class="kc" draggable="true" data-id="${l.id}"><b>${esc(l.full_name)}</b><span class="muted">${esc(l.company)}</span><div class="muted" style="border-left:3px solid ${campOf(l.campaign_id).color};padding-left:6px;margin-top:4px">${esc(campOf(l.campaign_id).name)}</div></div>`).join("")}</div></div>`; }).join("")}</div><p class="muted">Showing ${r.items.length} of ${r.total}. Drag a card to change its stage.</p>`;
    $$(".kc").forEach(c => { c.ondragstart = e => e.dataTransfer.setData("id", c.dataset.id); c.onclick = () => openLead(c.dataset.id); });
    $$(".col").forEach(c => { c.ondragover = e => { e.preventDefault(); c.classList.add("over"); }; c.ondragleave = () => c.classList.remove("over"); c.ondrop = async e => { e.preventDefault(); c.classList.remove("over"); const id = e.dataTransfer.getData("id"); if (!id || !can("leads:write")) return; await moveStage(id, c.dataset.s); loadLeads(); }; });
    return;
  }
  box.innerHTML = `<div class="tbl-wrap">${leadTable(r.items, true)}</div><div class="pager"><span>${r.total} leads</span><span><button class="btn sm" id="pp" ${LV.cursors.length < 2 ? "disabled" : ""}>Previous</button> <button class="btn sm" id="pn" ${r.next_cursor ? "" : "disabled"}>Next</button></span></div>`;
  bindTable(box, () => loadLeads()); $("#pn").onclick = () => { LV.cursors.push(r.next_cursor); loadLeads(); }; $("#pp").onclick = () => { LV.cursors.pop(); loadLeads(); };
}
function leadTable(items, withCamp) {
  const hc = hiddenCols(), cols = COLS.filter(c => !hc.includes(c[0]) && (withCamp || c[2] !== "table"));
  const cell = (l, k) => ({ full_name: `<b>${esc(l.full_name)}</b>${l.suppressed ? ' <span class="pill s8">DNC</span>' : ""}<div class="muted" style="font-size:11px">${esc(l.title)}</div>`, stage: `<span class="${stageCls(l.stage)}">${esc(l.stage)}</span>`, owner_id: ownerCell(l.owner_id), campaign_id: `<span style="color:${campOf(l.campaign_id).color}">●</span> ${esc(campOf(l.campaign_id).name)}`,
    last_activity_at: fdate(l.last_activity_at), created_at: fday(l.created_at), next_action_due: l.next_action ? `<span class="${l.next_action_due < new Date().toISOString() ? "bad" : ""}">${esc(l.next_action)} · ${fdate(l.next_action_due)}</span>` : '<span class="muted">None</span>' }[k] ?? esc(l[k]));
  return `<table class="tbl"><thead><tr><th style="width:34px"><input type="checkbox" class="all" aria-label="Select all"></th>${cols.map(c => `<th class="sortable" data-k="${c[0]}" aria-sort="${LV.sort === c[0] ? (LV.desc ? "descending" : "ascending") : "none"}">${c[1]}${LV.sort === c[0] ? (LV.desc ? " ▼" : " ▲") : ""}</th>`).join("")}</tr></thead><tbody>${items.map(l => `<tr class="click" data-id="${l.id}"><td><input type="checkbox" class="sel" data-id="${l.id}" ${SEL.has(l.id) ? "checked" : ""} aria-label="Select ${esc(l.full_name)}"></td>${cols.map(c => `<td class="${c[0] === "stage" ? "cell-stage" : ""}">${cell(l, c[0])}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}
function bindTable(root, redraw) {
  $$("th.sortable", root).forEach(h => h.onclick = () => { LV.desc = LV.sort === h.dataset.k ? !LV.desc : false; LV.sort = h.dataset.k; LV.cursors = [""]; loadLeads(); });
  $$("tbody tr", root).forEach(tr => tr.onclick = e => { if (e.target.closest("input")) return; openLead(tr.dataset.id); });
  $$(".sel", root).forEach(c => c.onchange = () => { c.checked ? SEL.add(c.dataset.id) : SEL.delete(c.dataset.id); renderBulk(); });
  $$(".all", root).forEach(a => a.onchange = e => $$(".sel", a.closest("table")).forEach(c => { c.checked = e.target.checked; e.target.checked ? SEL.add(c.dataset.id) : SEL.delete(c.dataset.id); renderBulk(); }));
}
async function loadBoard() {
  const box = $("#ltab"); let camps = await api("/campaigns");
  if (LV.campaign) camps = camps.filter(c => c.id === LV.campaign); else if (LV.channel) camps = camps.filter(c => c.channel === LV.channel);
  const res = await Promise.all(camps.map(c => api("/leads?" + lvQuery({ campaign: c.id, limit: 10 })))); camps.forEach((c, i) => GB[c.id] = res[i]);
  SEL = new Set(); renderBulk(); drawBoard(camps);
}
function drawBoard(camps) {
  const box = $("#ltab"), filtering = !!(LV.q || LV.stage || LV.source || LV.owner || LV.status), col = gcol();
  const vis = camps.filter(c => !filtering || GB[c.id].total > 0);
  if (!vis.length) { box.innerHTML = `<div class="card">${empty("No leads match", "Clear a filter, or add leads from Lead Discovery.")}</div>`; return; }
  box.innerHTML = vis.map(c => { const g = GB[c.id], tot = Object.values(c.stages).reduce((a, b) => a + b, 0) || 1;
    return `<div class="grp ${col[c.id] ? "collapsed" : ""}" data-c="${c.id}" style="--gc:${c.color}"><div class="grp-h" data-t="${c.id}"><span class="chev">▾</span><h3>${esc(c.name)}</h3>${chBadge(c.channel)}<span class="n">${g.total} lead${g.total === 1 ? "" : "s"}${filtering ? " match" : ""} · ${c.contacted} contacted</span>${filtering ? "" : `<div class="sdist" title="Stage mix">${S.stages.filter(s => c.stages[s]).map(s => `<i style="width:${100 * c.stages[s] / tot}%;background:${stageHex(s)}" title="${esc(s)}: ${c.stages[s]}"></i>`).join("")}</div>`}</div>
    <div class="grp-body">${g.items.length ? leadTable(g.items, false) : `<div class="empty">No leads in this campaign yet.</div>`}${g.next_cursor ? `<div class="grp-add" data-more="${c.id}">Load more (${g.total - g.items.length} remaining)</div>` : ""}${can("leads:write") && c.id !== "none" ? `<div class="grp-add" data-add="${c.id}">+ Add lead to this campaign</div>` : ""}</div></div>`; }).join("");
  $$("[data-t]", box).forEach(h => h.onclick = () => { const c = gcol(); c[h.dataset.t] = !c[h.dataset.t]; localStorage.gcollapsed = JSON.stringify(c); h.parentElement.classList.toggle("collapsed", c[h.dataset.t]); });
  $$("[data-add]", box).forEach(b => b.onclick = () => leadForm(null, b.dataset.add));
  $$("[data-more]", box).forEach(b => b.onclick = async () => { const id = b.dataset.more, g = GB[id]; const r = await api("/leads?" + lvQuery({ campaign: id, limit: 10, cursor: g.next_cursor })); GB[id] = { items: [...g.items, ...r.items], next_cursor: r.next_cursor, total: r.total }; drawBoard(camps); });
  $$(".grp-body", box).forEach(b => bindTable(b));
}
function renderBulk() {
  const b = $("#bulk"); if (!b) return; if (!SEL.size) { b.classList.add("hidden"); return; } b.classList.remove("hidden");
  b.innerHTML = `<b>${SEL.size} selected</b>${can("assign") ? `<select id="bo">${userOpts("", "Assign to…")}</select>` : ""}<select id="bc">${campOpts("", "Move to campaign…")}</select><select id="bs">${opts(S.stages, "", "Move to stage…")}</select><button class="btn sm danger" id="ba">Archive</button>${can("enrich") ? '<button class="btn sm" id="be">Enrich</button>' : ""}`;
  const run = async (action, value, reason) => { const r = await api("/leads/bulk", { method: "POST", body: { ids: [...SEL], action, value, reason } }); toast(`${r.updated} of ${r.requested} updated`); loadLeads(); };
  if ($("#bo")) $("#bo").onchange = e => e.target.value && run("assign", e.target.value);
  $("#bc").onchange = e => e.target.value && run("campaign", e.target.value);
  $("#bs").onchange = async e => { if (!e.target.value) return; let reason = ""; if (e.target.value === "Lost") { reason = await promptBox("Reason for Lost", "Why are these leads lost?"); if (!reason) return; } run("stage", e.target.value, reason); };
  $("#ba").onclick = async () => { if (await confirmBox("Archive leads", `Archive ${SEL.size} leads? They disappear from lists but history is kept.`, "Archive", true)) run("archive", ""); };
  if ($("#be")) $("#be").onclick = () => enrichConfirm([...SEL]);
}
async function moveStage(id, stage) {
  let reason = ""; if (stage === "Lost") { reason = await promptBox("Reason for Lost", "Why is this lead lost?"); if (!reason) return false; }
  try { await api(`/leads/${id}/stage`, { method: "POST", body: { stage, reason } }); toast("Stage updated"); return true; } catch (e) { toast(e.message, true); return false; }
}
async function enrichConfirm(ids) {
  if (!await confirmBox("Enrich with Apollo", `Enrich ${ids.length} lead(s)? This may consume provider credits. Existing and user-verified values are never overwritten.`, "Start enrichment")) return;
  try { const j = await api("/enrichment/jobs", { method: "POST", body: { lead_ids: ids, confirm: true } }); toast("Enrichment started"); pollJob(j.id); } catch (e) { toast(e.message, true); }
}
async function pollJob(id) {
  for (let i = 0; i < 30; i++) { await new Promise(r => setTimeout(r, 800)); const j = await api("/enrichment/jobs/" + id); if (!["queued", "running"].includes(j.status)) { const c = s => j.items.filter(x => x.status === s).length; toast(`Enrichment ${j.status}: ${c("enriched")} enriched, ${c("no_match")} no match, ${c("error")} errors`, j.status === "failed"); if (S.openLead && !$("#drawer").classList.contains("hidden")) openLead(S.openLead, true); return; } }
}

// ---------------------------------------------------------------- lead form + profile
function leadForm(l, defCamp) {
  const m = modal(`<h2>${l ? "Edit lead" : "Add lead"}</h2><form id="lf" class="form2">
    <label>First name*<input name="first_name" required value="${esc(l?.first_name)}"></label><label>Last name<input name="last_name" value="${esc(l?.last_name)}"></label>
    <label>Job title<input name="title" value="${esc(l?.title)}"></label><label>Company<input name="company" value="${esc(l?.company)}"></label>
    <label>Email<input name="email" type="email" value="${esc(l?.email)}"></label><label>Phone<input name="phone" value="${esc(l?.phone)}"></label>
    <label>LinkedIn URL<input name="linkedin_url" value="${esc(l?.linkedin_url)}"></label><label>Website<input name="website" value="${esc(l?.website)}"></label>
    <label>Location<input name="location" value="${esc(l?.location)}"></label><label>Country<input name="country" value="${esc(l?.country)}"></label><label>Campaign<select name="campaign_id">${campOpts(l ? l.campaign_id : defCamp, "No campaign")}</select></label>
    ${l ? "" : `<label>Source*<select name="source">${opts(S.sources, "Manual")}</select></label><label>Source details (URL, event, referrer)<input name="source_detail"></label>${can("assign") ? `<label class="full">Owner<select name="owner_id">${userOpts("", "Unassigned")}</select></label>` : ""}`}
    <p class="err full" id="fe"></p><div class="acts full"><button type="button" class="btn" id="cn">Cancel</button><button class="btn primary">${l ? "Save changes" : "Add lead"}</button></div></form>`);
  $("#cn").onclick = closeModal;
  $("#lf").onsubmit = async e => {
    e.preventDefault(); const d = Object.fromEntries(new FormData(e.target)); if (d.owner_id === "") d.owner_id = null;
    try { const r = l ? await api("/leads/" + l.id, { method: "PATCH", body: d }) : await api("/leads", { method: "POST", body: d }); closeModal(); toast(l ? "Lead saved" : "Lead added"); route(); if (!l) openLead(r.id); else openLead(l.id, true); }
    catch (er) { $("#fe").textContent = er.message; }
  };
}
const dd = (k, v) => `<dt>${esc(k)}</dt><dd>${v || '<span class="muted">—</span>'}</dd>`;
async function openLead(id, keep) {
  S.openLead = id; const d = $("#drawer"); d.classList.remove("hidden"); if (!keep) d.innerHTML = `<div class="db">${sk(8)}</div>`;
  let l, tl; try { [l, tl] = await Promise.all([api("/leads/" + id), api(`/leads/${id}/timeline`)]); } catch (e) { d.innerHTML = `<div class="db">${empty("Lead unavailable", e.message)}</div>`; return; }
  const tab = S.leadTab || "overview"; const w = can("leads:write");
  const tli = tl.map(x => x.kind === "activity" ? `<li><b>${esc(x.type.replace("_", " "))}: ${esc(x.outcome)}</b> <span class="muted">by ${esc(x.user)} · ${fdate(x.at)}</span>${x.notes ? `<div>${esc(x.notes)}</div>` : ""}</li>` : x.kind === "task" ? `<li class="t"><b>Task: ${esc(x.title)}</b> <span class="muted">${esc(x.status)} · due ${fdate(x.due_at)} · ${esc(uname(x.owner_id))}</span></li>` : `<li class="s"><b>Stage: ${esc(x.stage)}</b> <span class="muted">${fdate(x.at)} by ${esc(uname(x.by))}${x.reason ? " · " + esc(x.reason) : ""}</span></li>`).join("");
  const prov = Object.entries(l.provenance || {}).map(([f, p]) => `<tr><td>${esc(f)}</td><td>${esc(p.provider)}</td><td>${fdate(p.at)}</td><td>${esc(p.prev) || "—"}</td></tr>`).join("");
  const panes = {
    overview: `<dl class="kv">${dd("Phone", l.phone ? `<a href="tel:${esc(l.phone)}">${esc(l.phone)}</a>` : "")}${dd("Email", l.email ? `<a href="mailto:${esc(l.email)}">${esc(l.email)}</a>` : "")}${dd("LinkedIn", l.linkedin_url ? `<a href="${esc(l.linkedin_url)}" target="_blank" rel="noopener noreferrer">${esc(l.linkedin_url)}</a>` : "")}${dd("Website", esc(l.website))}${dd("Location", esc(l.location || l.country))}${dd("Source", esc(l.source) + (l.source_detail ? ` <span class="muted">(${esc(l.source_detail)})</span>` : ""))}${dd("Created", fday(l.created_at))}${dd("Import batch", esc(l.import_batch_id))}${dd("Owner", esc(uname(l.owner_id)))}${dd("Campaign", `<span class="chb ${campOf(l.campaign_id).channel}">${esc(campOf(l.campaign_id).name)}</span>`)}${dd("Touches", `${l.touches || 0} (${l.attempts || 0} calls)`)}${dd("Tags", (l.tags || []).map(esc).join(", "))}</dl>`,
    timeline: tl.length ? `<ul class="tl">${tli}</ul>` : empty("No history yet", "Log an activity to start the timeline."),
    enrichment: `<p>Status: <b>${esc(l.enrichment_state.replace(/_/g, " "))}</b> · Last enriched: ${fdate(l.last_enriched_at)}</p>${prov ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Field</th><th>Provider</th><th>When</th><th>Previous value</th></tr></thead><tbody>${prov}</tbody></table></div>` : empty("No enriched fields yet")}<p class="muted">Verified by a person: ${(l.verified_fields || []).map(esc).join(", ") || "none"}. These are never overwritten by enrichment.</p>${can("enrich") ? '<button class="btn" id="enr">Enrich this lead</button>' : ""}`,
    opportunity: `<div id="opp">${sk(2)}</div>`
  };
  d.innerHTML = `<div class="dh"><div><h2>${esc(l.full_name)} ${l.suppressed ? '<span class="pill s8">Do not contact</span>' : ""}</h2><div class="muted">${esc(l.title)}${l.title && l.company ? " at " : ""}${esc(l.company)}</div><div style="margin-top:6px">${pill(l.stage)} ${tag(l.source)}</div></div><button class="icon-btn" id="dx" aria-label="Close">✕</button></div>
  <div class="db"><div class="na"><div><b>Next action</b><div>${l.next_action ? `${esc(l.next_action)} · <span class="${l.next_action_due < new Date().toISOString() ? "bad" : ""}">${fdate(l.next_action_due)}</span>` : '<span class="warnc">None set. Add a follow-up.</span>'}</div></div>${can("tasks:write") ? '<button class="btn sm" id="addT">+ Task</button>' : ""}</div>
  <div class="bar">${can("activities:write") && !l.suppressed ? '<button class="btn primary sm" id="logA">Log activity</button>' : ""}${w ? `<select id="stg" aria-label="Change stage">${opts(S.stages, l.stage)}</select>` : ""}${can("assign") ? `<select id="own" aria-label="Owner">${userOpts(l.owner_id, "Unassigned")}</select>` : ""}${w ? '<button class="btn sm" id="edL">Edit</button>' : ""}${can("ai:use") ? '<button class="btn sm" id="aiL">✨ Ask AI</button>' : ""}${can("opps:write") ? '<button class="btn sm" id="mkO">+ Opportunity</button>' : ""}</div>
  <div class="tabs">${["overview", "timeline", "enrichment", "opportunity"].map(t => `<button class="tab ${t === tab ? "on" : ""}" data-t="${t}">${t[0].toUpperCase() + t.slice(1)}</button>`).join("")}</div>${panes[tab]}</div>`;
  $("#dx").onclick = () => { d.classList.add("hidden"); S.openLead = null; };
  $$(".tab[data-t]", d).forEach(b => b.onclick = () => { S.leadTab = b.dataset.t; openLead(id, true); });
  const on = (i, f) => $(i, d) && ($(i, d).onclick = f);
  on("#logA", () => logActivityModal(l, () => { openLead(id, true); if (location.hash.includes("leads")) loadLeads(); })); on("#edL", () => leadForm(l)); on("#enr", () => enrichConfirm([id]));
  on("#addT", () => taskModal(l)); on("#aiL", () => openAI(`Summarise this lead`, id)); on("#mkO", () => oppModal(l));
  if ($("#stg", d)) $("#stg", d).onchange = async e => { const ok = await moveStage(id, e.target.value); openLead(id, true); if (ok && $("#ltab")) loadLeads(); };
  if ($("#own", d)) $("#own", d).onchange = async e => { try { await api(`/leads/${id}/assign?` + qs({ owner_id: e.target.value }), { method: "POST" }); toast("Owner updated"); openLead(id, true); } catch (er) { toast(er.message, true); } };
  if (tab === "opportunity") { const o = (await api("/opportunities").catch(() => [])).filter(x => x.lead_id === id); $("#opp").innerHTML = o.length ? o.map(x => `<div class="card" style="margin-bottom:8px"><b>${esc(x.name)}</b> ${pill(x.stage)}<div>${money(x.value)}</div></div>`).join("") : empty("No opportunity yet", "Create one once the lead is qualified.", ""); }
}
$("#drawer")?.addEventListener("keydown", e => { if (e.key === "Escape") $("#dx")?.click(); });

// ---------------------------------------------------------------- log activity (outcome != stage); outcomes depend on the channel
function logActivityModal(l, done, defType) {
  const ch = campOf(l.campaign_id).channel, t0 = defType || TYPE_OF[ch] || "phone_call", now = new Date();
  modal(`<h2>Log activity · ${esc(l.full_name)}</h2><p class="muted" style="margin:0">Record what happened after you contacted the prospect outside the CRM. Outcomes never change the stage by themselves.</p>
  <form id="af" class="form2"><label>Channel<select name="type" id="at">${opts(S.types.map(t => [t, t.replace("_", " ")]), t0)}</select></label><label>Outcome*<select name="outcome" id="oc">${opts(S.obt[t0] || S.outcomes, "", "Choose outcome")}</select></label>
  <label>When<input type="datetime-local" name="occurred_at" value="${isoLocal(now)}"></label><label>Duration (min, optional)<input type="number" min="0" name="duration_min"></label>
  <label class="full">Notes<textarea name="notes" placeholder="What happened or was discussed?"></textarea></label>
  <label>Next action<input name="next_action" placeholder="e.g. Send pricing"></label><label>Follow-up due <span id="req"></span><input type="datetime-local" name="follow_up_due"></label>
  <p class="err full" id="fe"></p><div class="acts full"><button type="button" class="btn" id="cn">Cancel</button><button class="btn primary" id="sv">Save activity</button></div></form>`);
  $("#cn").onclick = closeModal;
  $("#at").onchange = e => { $("#oc").innerHTML = opts(S.obt[e.target.value] || S.outcomes, "", "Choose outcome"); };
  $("#oc").onchange = e => { const cb = e.target.value === "Call Back Requested"; $("#req").textContent = cb ? "(required)" : ""; if (cb) $("[name=follow_up_due]").value = isoLocal(new Date(Date.now() + 864e5)); };
  $("#af").onsubmit = async e => {
    e.preventDefault(); const d = Object.fromEntries(new FormData(e.target)); const btn = $("#sv"); btn.disabled = true;
    const body = { lead_id: l.id, type: d.type, outcome: d.outcome, notes: d.notes, next_action: d.next_action, occurred_at: toISO(d.occurred_at), follow_up_due: toISO(d.follow_up_due), duration_min: d.duration_min ? +d.duration_min : null };
    try {
      const r = await api("/activities", { method: "POST", body }); closeModal(); toast("Activity logged" + (r.task ? " · follow-up created" : ""));
      if (r.suggested_stage && can("leads:write") && await confirmBox("Move stage?", `Outcome "${d.outcome}" suggests moving ${l.full_name} from ${r.lead.stage} to ${r.suggested_stage}. Nothing changes unless you confirm.`, `Move to ${r.suggested_stage}`)) await moveStage(l.id, r.suggested_stage);
      done && done(r);
    } catch (er) { btn.disabled = false; $("#fe").textContent = er.message; }
  };
}

// ---------------------------------------------------------------- LEAD DISCOVERY (A: sources, B: upload wizard)
const FIELDS = [["", "Ignore column"], ["first_name", "First name"], ["last_name", "Last name"], ["email", "Email"], ["phone", "Phone"], ["company", "Company"], ["title", "Job title"], ["linkedin_url", "LinkedIn URL"], ["website", "Website"], ["location", "Location"], ["country", "Country"]];
PAGES.discovery = async v => {
  const hist = await api("/imports");
  v.innerHTML = head("Lead Discovery", "Add leads from connected sources or upload a list.") + `<div class="grid g2">
  <div class="card"><h2>A. Connected sources</h2><p class="muted">Search Apollo, add research leads, or bring in an official LinkedIn export.</p><div class="bar"><button class="btn primary" id="goA">Search Apollo</button><button class="btn" id="goM">Add research / manual lead</button></div>
  <p class="muted"><b>LinkedIn / Sales Navigator:</b> only official exports or approved CRM integrations. Upload the export in path B and choose source “LinkedIn”. No scraping.</p></div>
  <div class="card"><h2>B. Upload a lead list</h2><p class="muted">CSV or XLSX. Map columns, validate, resolve duplicates, then import.</p><button class="btn primary" id="goB">Upload file</button></div></div>
  <div id="work" style="margin-top:16px"></div>
  <div class="card" style="margin-top:16px"><h2>Import history</h2>${hist.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>When</th><th>Source / file</th><th>By</th><th>Rows</th><th>Created</th><th>Updated</th><th>Skipped</th><th>Invalid</th><th>Status</th><th></th></tr></thead><tbody>${hist.map(b => `<tr><td>${fdate(b.at)}</td><td>${esc(b.file)}</td><td>${esc(b.by_name)}</td><td>${b.row_count}</td><td>${b.created}</td><td>${b.updated}</td><td>${b.skipped}</td><td>${b.invalid}</td><td>${esc(b.status.replace("_", " "))}</td><td>${b.status === "done" && b.created ? `<button class="btn sm" data-rb="${b.id}">Roll back</button>` : ""}</td></tr>`).join("")}</tbody></table></div>` : empty("No imports yet", "Your first import will show up here with row counts and a rollback option.")}</div>`;
  $("#goM").onclick = () => leadForm(); $("#goA").onclick = apolloUI; $("#goB").onclick = wizard;
  $$("[data-rb]").forEach(b => b.onclick = async () => { if (!await confirmBox("Roll back import", "Remove every lead created by this import? Blocked if any of them already has activity.", "Roll back", true)) return; try { await api(`/imports/${b.dataset.rb}/rollback`, { method: "POST" }); toast("Import rolled back"); PAGES.discovery(v); } catch (e) { toast(e.message, true); } });
};
function apolloUI() {
  const w = $("#work"); let res = [];
  w.innerHTML = `<div class="card"><h2>Apollo search</h2><form id="as" class="bar"><select name="__c" id="apc" aria-label="Import into campaign">${campOpts("", "Import into: no campaign")}</select><input name="title" placeholder="Job title"><input name="company" placeholder="Company"><input name="location" placeholder="Location"><input name="keywords" placeholder="Keywords"><button class="btn primary">Search</button></form><div id="ar"></div></div>`;
  let page = 1;
  const run = async () => {
    const f = Object.fromEntries(new FormData($("#as"))); delete f.__c; $("#ar").innerHTML = sk(5);
    try {
      const r = await api("/integrations/apollo/search", { method: "POST", body: { ...f, page } }); res = r.results;
      $("#ar").innerHTML = (r.note ? `<p class="warnc">${esc(r.note)}</p>` : "") + `<div class="tbl-wrap"><table class="tbl"><thead><tr><th></th><th>Name</th><th>Title</th><th>Company</th><th>Location</th><th>Email</th><th>Duplicate?</th></tr></thead><tbody>${res.map((x, i) => `<tr><td><input type="checkbox" class="ap" data-i="${i}" ${x.duplicate_of ? "" : "checked"} aria-label="Select ${esc(x.first_name)}"></td><td>${esc(x.first_name)} ${esc(x.last_name)}</td><td>${esc(x.title)}</td><td>${esc(x.company)}</td><td>${esc(x.location)}</td><td>${esc(x.email) || `<span class="muted">${x.has_email ? "available, enrich to reveal" : "needs enrichment"}</span>`}</td><td>${x.duplicate_of ? `<span class="warnc">Matches ${esc(x.duplicate_of)}</span>` : "New"}</td></tr>`).join("")}</tbody></table></div>
      <div class="pager"><span><button class="btn sm" id="ap-p" ${page < 2 ? "disabled" : ""}>Previous page</button> <button class="btn sm" id="ap-n">Next page</button></span><button class="btn primary" id="ap-i">Import selected</button></div>`;
      $("#ap-p").onclick = () => { page--; run(); }; $("#ap-n").onclick = () => { page++; run(); };
      $("#ap-i").onclick = async () => { const sel = $$(".ap").filter(c => c.checked).map(c => res[+c.dataset.i]); if (!sel.length) return toast("Select at least one result", true); try { const b = await api("/integrations/apollo/import", { method: "POST", body: { records: sel, campaign_id: $("#apc").value || null } }); toast(`Imported ${b.created}, skipped ${b.skipped} duplicates`); route(); } catch (e) { toast(e.message, true); } };
    } catch (e) { $("#ar").innerHTML = empty("Search failed", e.message); }
  };
  $("#as").onsubmit = e => { e.preventDefault(); page = 1; run(); };
}
function wizard() {
  const w = $("#work"); const st = { b: null, map: {}, policy: "skip", source: "File Upload", pv: null }; const names = ["Upload", "Map fields", "Preview & validate", "Duplicates", "Confirm"];
  const steps = n => `<div class="steps">${names.map((x, i) => `<span class="step ${i === n ? "on" : i < n ? "done" : ""}">${i + 1}. ${x}</span>`).join("")}</div>`;
  const s0 = () => { w.innerHTML = `<div class="card"><h2>Upload a lead list</h2>${steps(0)}<label>CSV or XLSX file (max 5 MB, 5,000 rows)<input type="file" id="fi" accept=".csv,.xlsx"></label><div class="bar" style="margin-top:10px"><label style="min-width:220px">Source label<select id="fs">${opts(S.sources, "File Upload")}</select></label><label style="min-width:260px">Import into campaign<select id="fc">${campOpts("", "No campaign")}</select></label></div><p class="err" id="fe"></p></div>`; $("#fi").onchange = async e => { const f = e.target.files[0]; if (!f) return; if (!/\.(csv|xlsx)$/i.test(f.name)) { $("#fe").textContent = "Only .csv and .xlsx files are supported."; return; } st.source = $("#fs").value; st.campaign = $("#fc").value || null; const fd = new FormData(); fd.append("file", f); try { st.b = await api("/imports/upload", { method: "POST", form: fd }); st.map = { ...st.b.suggested_mapping }; s1(); } catch (er) { $("#fe").textContent = er.message; } }; };
  const s1 = () => { const b = st.b; w.innerHTML = `<div class="card"><h2>Map columns</h2>${steps(1)}<p class="muted">${b.row_count} rows found in ${esc(b.file)}. We guessed the mapping; check it.</p><div class="tbl-wrap"><table class="tbl"><thead><tr><th>Column in file</th><th>Sample</th><th>Maps to</th></tr></thead><tbody>${b.columns.map((c, i) => `<tr><td><b>${esc(c)}</b></td><td>${esc(b.sample[0]?.[c])}</td><td><select data-c="${i}">${opts(FIELDS, st.map[c] || "")}</select></td></tr>`).join("")}</tbody></table></div><div class="acts" style="margin-top:12px"><button class="btn" id="bk">Back</button><button class="btn primary" id="nx">Validate</button></div></div>`; $$("[data-c]").forEach(s => s.onchange = () => { st.map[b.columns[+s.dataset.c]] = s.value; }); $("#bk").onclick = s0; $("#nx").onclick = s2; };
  const body = () => ({ mapping: st.map, policy: st.policy, source: st.source, campaign_id: st.campaign || null, confirm_separate: !!st.cs });
  const s2 = async () => { w.innerHTML = `<div class="card">${steps(2)}${sk(5)}</div>`; try { st.pv = await api(`/imports/${st.b.id}/preview`, { method: "POST", body: body() }); } catch (e) { toast(e.message, true); return s1(); }
    const p = st.pv; w.innerHTML = `<div class="card"><h2>Preview & validation</h2>${steps(2)}<div class="grid g3"><div class="card kpi"><span class="l">Valid new leads</span><span class="v ok">${p.valid_new}</span></div><div class="card kpi"><span class="l">Possible duplicates</span><span class="v warnc">${p.duplicate_count}</span></div><div class="card kpi"><span class="l">Rows with errors</span><span class="v bad">${p.error_count}</span></div></div>
    ${p.error_count ? `<h3 style="margin-top:14px">Row errors</h3><div class="tbl-wrap" style="max-height:240px"><table class="tbl"><thead><tr><th>Row</th><th>Problem</th><th>Data</th></tr></thead><tbody>${p.errors.slice(0, 50).map(e => `<tr><td>${e.row}</td><td class="bad">${esc(e.errors.join("; "))}</td><td>${esc(Object.values(e.data).join(", "))}</td></tr>`).join("")}</tbody></table></div><button class="btn sm" id="er" style="margin-top:8px">Download error report</button>` : ""}
    <div class="acts" style="margin-top:12px"><button class="btn" id="bk">Back</button><button class="btn primary" id="nx">${p.duplicate_count ? "Resolve duplicates" : "Continue"}</button></div></div>`;
    $("#bk").onclick = s1; $("#nx").onclick = s3; if ($("#er")) $("#er").onclick = () => download(`/imports/${st.b.id}/errors.csv`, "import_errors.csv", "POST", body()); };
  const s3 = () => { const p = st.pv; w.innerHTML = `<div class="card"><h2>Duplicates</h2>${steps(3)}${p.duplicate_count ? `<p class="muted">Nothing is overwritten silently. Pick how to treat the ${p.duplicate_count} match(es) below.</p>
    <div class="bar">${[["skip", "Skip duplicates"], ["update", "Fill blank fields on existing leads"], ["separate", "Import as separate records"]].map(([v, l]) => `<label style="flex-direction:row;align-items:center;gap:6px;color:var(--text)"><input type="radio" name="pol" value="${v}" ${st.policy === v ? "checked" : ""}>${l}</label>`).join("")}</div>
    <div class="tbl-wrap" style="max-height:260px"><table class="tbl"><thead><tr><th>Row</th><th>Matches existing</th><th>Confidence</th><th>Would change (update policy)</th></tr></thead><tbody>${p.duplicates.slice(0, 60).map(d => `<tr><td>${d.row}</td><td>${esc(d.existing)}</td><td>${d.confidence === "high" ? "High (" + esc(d.signal) + ")" : '<span class="warnc">Low (' + esc(d.signal) + "), review</span>"}</td><td>${Object.entries(d.changes).map(([f, c]) => `${esc(f)}: ${esc(c.old) || "∅"} → ${esc(c.new)}`).join("; ") || "—"}</td></tr>`).join("")}</tbody></table></div>` : empty("No duplicates found", "Every valid row will be created as a new lead.")}
    <div class="acts" style="margin-top:12px"><button class="btn" id="bk">Back</button><button class="btn primary" id="nx">Review & confirm</button></div></div>`;
    $$("[name=pol]").forEach(r => r.onchange = () => { st.policy = r.value; }); $("#bk").onclick = s2; $("#nx").onclick = s4; };
  const s4 = async () => { const p = st.pv; if (st.policy === "separate" && p.duplicate_count && !await confirmBox("Create duplicate records?", `${p.duplicate_count} rows match existing leads. Importing them as separate records creates duplicates.`, "Yes, import separately", true)) return; st.cs = true;
    w.innerHTML = `<div class="card"><h2>Confirm import</h2>${steps(4)}<p>Source <b>${esc(st.source)}</b> · policy <b>${esc(st.policy)}</b>. New leads: <b>${p.valid_new}</b>${st.policy === "separate" ? " + " + p.duplicate_count + " separate" : ""}. Invalid rows (<b>${p.error_count}</b>) are not imported and stay in the error report.</p><div class="acts"><button class="btn" id="bk">Back</button><button class="btn primary" id="go">Import leads</button></div></div>`; $("#bk").onclick = s3;
    $("#go").onclick = async e => { e.target.disabled = true; try { const b = await api(`/imports/${st.b.id}/confirm`, { method: "POST", body: body() }); toast(`Imported ${b.created} new · ${b.updated} updated · ${b.skipped} skipped`); route(); } catch (er) { e.target.disabled = false; toast(er.message, true); } }; };
  s0();
}

// ---------------------------------------------------------------- SALES ACTIVITY (daily queue grouped by campaign, per channel)
const WS = { ch: "", q: "", open: {} };
PAGES.workspace = async v => {
  const all = await api("/workspace/queue"); const chOf = x => campOf(x.lead.campaign_id).channel;
  let q = all.filter(x => (!WS.ch || chOf(x) === WS.ch) && (!WS.q || (x.lead.full_name + x.lead.company).toLowerCase().includes(WS.q.toLowerCase())));
  if (!q.find(x => x.lead.id === S.wsLead)) S.wsLead = q[0]?.lead.id;
  const cnt = c => all.filter(x => !c || chOf(x) === c).length;
  const order = S.campaigns.map(c => c.id).concat("none"), groups = order.map(id => ({ c: campOf(id === "none" ? null : id), items: q.filter(x => (x.lead.campaign_id || "none") === id) })).filter(g => g.items.length);
  v.innerHTML = head("Sales Activity & Follow-up", "Work your queue channel by channel, then log what happened. Callbacks first, then overdue, then new leads.") +
    (!all.length ? `<div class="card">${empty("Queue is clear", "No callbacks, overdue tasks or untouched leads assigned to you.")}</div>` : `<div class="ws"><section class="card rail"><div class="seg" style="margin-bottom:10px;align-self:flex-start" role="group" aria-label="Channel">${[["", "All"], ["call", "Calls"], ["linkedin", "LinkedIn"], ["email", "Email"]].map(([c, l]) => `<button class="${WS.ch === c ? "on" : ""}" data-ch="${c}">${l} ${cnt(c)}</button>`).join("")}</div><input id="wq" placeholder="Search the queue" value="${esc(WS.q)}" style="margin-bottom:10px" aria-label="Search the queue">
    <div class="scroll">${groups.length ? groups.map(g => `<div class="qg ${WS.open[g.c.id] === false ? "collapsed" : ""}" style="--gc:${g.c.color}"><div class="qg-h" data-g="${g.c.id}"><span class="chev">▾</span>${esc(g.c.name)}<span class="n">${g.items.length}</span></div><div class="qg-b">${g.items.map(x => `<div class="qi ${x.lead.id === S.wsLead ? "on" : ""}" data-id="${x.lead.id}"><b>${esc(x.lead.full_name)}</b><div class="m">${esc(x.lead.company)}</div><div class="m ${x.rank < 2 ? "bad" : ""}">${esc(x.reason)}</div></div>`).join("")}</div></div>`).join("") : empty("Nothing in this channel", "Try another tab.")}</div></section><section class="card detail" id="qd">${sk(6)}</section></div>`);
  if (!all.length) return;
  $$("[data-ch]", v).forEach(b => b.onclick = () => { WS.ch = b.dataset.ch; PAGES.workspace(v); });
  let t; $("#wq").oninput = e => { clearTimeout(t); t = setTimeout(() => { WS.q = e.target.value; PAGES.workspace(v).then(() => { const i = $("#wq"); i.focus(); i.setSelectionRange(99, 99); }); }, 300); };
  $$("[data-g]", v).forEach(h => h.onclick = () => { WS.open[h.dataset.g] = WS.open[h.dataset.g] === false; h.parentElement.classList.toggle("collapsed", WS.open[h.dataset.g] === false); });
  $$(".qi", v).forEach(e => e.onclick = () => { S.wsLead = e.dataset.id; PAGES.workspace(v); });
  const cur = q.find(x => x.lead.id === S.wsLead); if (!cur) { $("#qd").innerHTML = empty("Pick a lead", "Details appear here."); return; }
  const l = cur.lead, cm = campOf(l.campaign_id), ch = cm.channel, idx = q.indexOf(cur), tl = await api(`/leads/${l.id}/timeline`);
  const tile = (k, label, body, on) => `<div class="card" style="${on ? "border-color:var(--primary);background:var(--primary-soft)" : ""}"><div class="muted">${label}</div><b style="font-size:${on ? 17 : 14}px;word-break:break-all">${body}</b></div>`;
  $("#qd").innerHTML = `<div class="ch"><div><h2 style="font-size:18px">${esc(l.full_name)}</h2><div class="muted">${esc(l.title)}${l.title && l.company ? " · " : ""}${esc(l.company)}</div><div class="bar" style="margin:8px 0 0">${chBadge(ch)} <span class="tag">${esc(cm.name)}</span></div></div>${pill(l.stage)}</div>
  <div class="grid g3" style="margin:14px 0">${tile("p", "Phone", l.phone ? `<a href="tel:${esc(l.phone)}">${esc(l.phone)}</a>` : "No phone, try enrichment", ch === "call" || ch === "mixed")}${tile("l", "LinkedIn", l.linkedin_url ? `<a href="${esc(l.linkedin_url)}" target="_blank" rel="noopener noreferrer">Open profile</a>` : "No profile URL", ch === "linkedin")}${tile("e", "Email", l.email ? `<a href="mailto:${esc(l.email)}">${esc(l.email)}</a>` : "No email", ch === "email")}</div>
  <div class="na"><div><b>Next action</b><div>${l.next_action ? esc(l.next_action) + " · " + fdate(l.next_action_due) : "None set"}</div></div><span class="muted">${l.touches || 0} touches · last ${fdate(l.last_activity_at)}</span></div>
  <div class="bar"><button class="btn primary" id="wl">Log ${ch === "linkedin" ? "LinkedIn touch" : ch === "email" ? "email" : "activity"}</button><button class="btn" id="wsk">Skip to next</button><button class="btn" id="wo">Open full profile</button></div>
  <h3>Recent history</h3>${tl.length ? `<ul class="tl">${tl.slice(0, 5).map(x => `<li><b>${esc(x.outcome || x.title || x.stage)}</b> <span class="muted">${esc((x.type || "").replace("_", " "))} ${fdate(x.at || x.created_at)}</span>${x.notes ? `<div>${esc(x.notes)}</div>` : ""}</li>`).join("")}</ul>` : empty("No history yet")}`;
  $("#wl").onclick = () => logActivityModal(l, () => { S.wsLead = q[idx + 1]?.lead.id || null; PAGES.workspace(v); }); $("#wsk").onclick = () => { S.wsLead = q[(idx + 1) % q.length].lead.id; PAGES.workspace(v); }; $("#wo").onclick = () => openLead(l.id);
};

// ---------------------------------------------------------------- ACTIVITIES & FOLLOW-UPS
let FT = "today";
PAGES.followups = async v => {
  const scope = can("assign") || S.me.role === "admin" ? (sessionStorage.fscope || (can("assign") ? "all" : "mine")) : "mine";
  const [open, done, acts] = await Promise.all([api(`/tasks?scope=${scope}&status=open`), api(`/tasks?scope=${scope}&status=done`), can("activities:read") ? api("/activities?limit=40") : []]);
  const n = new Date().toISOString(), td = n.slice(0, 10); const groups = { today: open.filter(t => t.due_at.slice(0, 10) === td), overdue: open.filter(t => t.due_at < n && t.due_at.slice(0, 10) !== td), upcoming: open.filter(t => t.due_at.slice(0, 10) > td), done: done.slice(-30).reverse(), history: [] };
  const row = t => `<tr><td><a href="#" data-l="${t.lead_id}"><b>${esc(t.lead_name)}</b></a><div class="muted">${esc(t.company)}</div></td><td>${esc(t.title)}</td><td class="${t.due_at < n && t.status === "open" ? "bad" : ""}">${fdate(t.due_at)}</td><td>${esc(uname(t.owner_id))}</td><td>${esc(t.priority)}</td><td>${t.status === "open" && can("tasks:write") ? `<button class="btn sm" data-a="complete" data-id="${t.id}">Done</button> <button class="btn sm" data-a="snooze" data-id="${t.id}">Snooze 1d</button> <button class="btn sm" data-a="reschedule" data-id="${t.id}">Reschedule</button> ${can("assign") ? `<button class="btn sm" data-a="reassign" data-id="${t.id}">Reassign</button>` : ""}` : ""}</td></tr>`;
  v.innerHTML = head("Activities & Follow-ups", "Tasks, callbacks, overdue work and your activity history.", can("assign") ? `<select id="fs" aria-label="Scope">${opts([["mine", "My tasks"], ["all", "Whole team"]], scope)}</select>` : "") +
    `<div class="tabs">${[["today", "Due today"], ["overdue", "Overdue"], ["upcoming", "Upcoming"], ["done", "Completed"], ["history", "Activity history"]].map(([k, l]) => `<button class="tab ${FT === k ? "on" : ""}" data-t="${k}">${l}${k !== "history" && k !== "done" ? ` (${groups[k].length})` : ""}</button>`).join("")}</div><div id="ft"></div>`;
  const show = () => { const b = $("#ft"); if (FT === "history") b.innerHTML = acts.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>When</th><th>Lead</th><th>Type</th><th>Outcome</th><th>By</th><th>Notes</th></tr></thead><tbody>${acts.map(a => `<tr><td>${fdate(a.at)}</td><td><a href="#" data-l="${a.lead_id}">${esc(a.lead_name)}</a></td><td>${esc(a.type.replace("_", " "))}</td><td>${esc(a.outcome)}</td><td>${esc(a.user)}</td><td>${esc(a.notes)}</td></tr>`).join("")}</tbody></table></div>` : `<div class="card">${empty("No activity yet", "Log your first activity from a lead.")}</div>`;
    else b.innerHTML = groups[FT].length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Lead</th><th>Task</th><th>Due</th><th>Owner</th><th>Priority</th><th></th></tr></thead><tbody>${groups[FT].map(row).join("")}</tbody></table></div>` : `<div class="card">${empty(FT === "overdue" ? "Nothing overdue" : "Nothing here", "You are on top of your follow-ups.")}</div>`;
    $$("[data-l]", b).forEach(a => a.onclick = e => { e.preventDefault(); openLead(a.dataset.l); });
    $$("[data-a]", b).forEach(btn => btn.onclick = async () => { const id = btn.dataset.id, a = btn.dataset.a; try {
      if (a === "reschedule") { modal(`<h2>Reschedule</h2><label>New date and time<input type="datetime-local" id="nd" value="${isoLocal(new Date(Date.now() + 864e5))}"></label><div class="acts"><button class="btn" id="cn">Cancel</button><button class="btn primary" id="cy">Reschedule</button></div>`); $("#cn").onclick = closeModal; $("#cy").onclick = async () => { await api(`/tasks/${id}/reschedule`, { method: "POST", body: { due_at: toISO($("#nd").value) } }); closeModal(); toast("Rescheduled"); PAGES.followups(v); }; return; }
      if (a === "reassign") { modal(`<h2>Reassign task</h2><label>New owner<select id="no">${userOpts("")}</select></label><div class="acts"><button class="btn" id="cn">Cancel</button><button class="btn primary" id="cy">Reassign</button></div>`); $("#cn").onclick = closeModal; $("#cy").onclick = async () => { await api(`/tasks/${id}/reassign`, { method: "POST", body: { owner_id: $("#no").value } }); closeModal(); toast("Reassigned"); PAGES.followups(v); }; return; }
      await api(`/tasks/${id}/${a}`, { method: "POST", body: {} }); toast(a === "complete" ? "Task completed" : "Task snoozed"); PAGES.followups(v);
    } catch (e) { toast(e.message, true); } });
  };
  $$(".tab[data-t]").forEach(t => t.onclick = () => { FT = t.dataset.t; $$(".tab[data-t]").forEach(x => x.classList.toggle("on", x === t)); show(); });
  if ($("#fs")) $("#fs").onchange = e => { sessionStorage.fscope = e.target.value; PAGES.followups(v); };
  show();
};

// ---------------------------------------------------------------- OPPORTUNITIES (kanban)
PAGES.opportunities = async v => {
  const ops = await api("/opportunities"); const total = ops.filter(o => !["Won", "Lost"].includes(o.stage)).reduce((a, o) => a + o.value, 0);
  v.innerHTML = head("Opportunities", `Open pipeline value ${money(total)}. Drag cards between stages.`) + (ops.length ? `<div class="kanban">${S.oppStages.map(s => { const g = ops.filter(o => o.stage === s); return `<div class="col" data-s="${esc(s)}"><h3>${esc(s)} <span class="muted">${g.length} · ${money(g.reduce((a, o) => a + o.value, 0))}</span></h3><div class="cards">${g.map(o => `<div class="kc" draggable="true" data-id="${o.id}"><b>${esc(o.name)}</b><span class="muted">${esc(o.lead_name)} · ${esc(o.company)}</span><div>${money(o.value)}</div></div>`).join("")}</div></div>`; }).join("")}</div>` : `<div class="card">${empty("No opportunities yet", "Open a qualified lead and choose + Opportunity.")}</div>`);
  $$(".kc").forEach(c => { c.ondragstart = e => e.dataTransfer.setData("id", c.dataset.id); c.onclick = () => openLead(ops.find(o => o.id === c.dataset.id).lead_id); });
  $$(".col").forEach(c => { c.ondragover = e => { e.preventDefault(); c.classList.add("over"); }; c.ondragleave = () => c.classList.remove("over"); c.ondrop = async e => { e.preventDefault(); c.classList.remove("over"); const id = e.dataTransfer.getData("id"); if (!id || !can("opps:write")) return; try { await api("/opportunities/" + id, { method: "PATCH", body: { stage: c.dataset.s } }); toast("Stage updated"); PAGES.opportunities(v); } catch (er) { toast(er.message, true); } }; });
};

// ---------------------------------------------------------------- REPORTS & ANALYTICS (neon story + detailed reports)
let RT = "story"; const RF = { days: 30, campaign: "", channel: "", owner: "", source: "", stage: "" };
const tbl = (cols, rows) => rows.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr>${cols.map(c => `<th>${esc(c[1])}</th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr>${cols.map(c => `<td>${c[2] ? c[2](r) : esc(r[c[0]])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : empty("No data for these filters");
const nbar = (label, val, max, text, col) => `<div class="nb"><span class="n">${esc(label)}</span><span class="t"><i style="width:${Math.max(2, Math.round(100 * val / Math.max(1, max)))}%;--c:${col}"></i></span><span class="c">${esc(text)}</span></div>`;
const RTABS = [["story", "The story"], ["campaigns", "Campaigns"], ["channels", "Channels"], ["funnel", "Funnel"], ["sources", "Sources"], ["calls", "Call outcomes"], ["team", "Team"], ["health", "Pipeline health"], ["enrichment", "Enrichment"], ["insights", "Insights"]];
PAGES.reports = async v => {
  v.innerHTML = `<div class="neon"><div class="page-h"><div><div class="eyebrow">Reports &amp; analytics</div><h1>${RT === "story" ? "How the pipeline performed" : RTABS.find(t => t[0] === RT)[1]}</h1></div><div class="bar" style="margin:0"><div class="seg" role="group" aria-label="Period">${[7, 30, 90].map(x => `<button class="${RF.days === x ? "on" : ""}" data-d="${x}">${x}d</button>`).join("")}</div>${can("export") && !["story", "health", "enrichment", "insights"].includes(RT) ? '<button class="btn" id="rx">Export CSV</button>' : ""}</div></div>
    <div class="bar"><select id="rc" aria-label="Campaign">${campOpts(RF.campaign, "All campaigns")}</select><select id="rh" aria-label="Channel">${opts([["call", "Calls"], ["linkedin", "LinkedIn"], ["email", "Email"], ["mixed", "Multi-channel"]], RF.channel, "All channels")}</select>${S.me.role === "caller" ? "" : `<select id="ro" aria-label="Salesperson">${opts([["unassigned", "Unassigned"], ...S.users.map(u => [u.id, u.name])], RF.owner, "All salespeople")}</select>`}<select id="rs" aria-label="Source">${opts(S.sources, RF.source, "All sources")}</select><select id="rt" aria-label="Stage">${opts(S.stages, RF.stage, "All stages")}</select></div>
    <div class="tabs">${RTABS.map(([k, l]) => `<button class="tab ${RT === k ? "on" : ""}" data-r="${k}">${l}</button>`).join("")}</div><div id="rb">${sk(6)}</div></div>`;
  const rel = () => PAGES.reports(v);
  $$("[data-d]", v).forEach(b => b.onclick = () => { RF.days = +b.dataset.d; rel(); });
  [["#rc", "campaign"], ["#rh", "channel"], ["#ro", "owner"], ["#rs", "source"], ["#rt", "stage"]].forEach(([i, k]) => $(i) && ($(i).onchange = e => { RF[k] = e.target.value; rel(); }));
  $$(".tab[data-r]", v).forEach(t => t.onclick = () => { RT = t.dataset.r; rel(); });
  if ($("#rx")) $("#rx").onclick = () => download(`/analytics/export/${RT === "calls" ? "calls" : RT}?` + qs(RF), RT + ".csv");
  const d = await api(`/analytics/${RT}?` + qs(RF)), b = $("#rb"), def = x => `<p class="muted" style="font-size:12px">Definition: ${esc(x)}</p>`;
  if (RT === "story") return story(b, d);
  if (RT === "campaigns") b.innerHTML = `<div class="card"><h2>Which campaigns turn leads into conversations?</h2>${tbl([["name", "Campaign", r => `<span style="color:${r.color}">●</span> ${esc(r.name)} ${chBadge(r.channel)}`], ["leads", "Leads"], ["contacted_pct", "Contacted", r => `<span class="meter"><i style="width:${r.contacted_pct}%;background:var(--primary)"></i></span>${r.contacted_pct}%`], ["engaged_pct", "Engaged", r => `<span class="meter"><i style="width:${r.engaged_pct}%;background:var(--ok)"></i></span>${r.engaged_pct}%`], ["qualified", "Qualified"], ["won", "Won"], ["touches", "Touches (period)"]], d.rows)}${def(d.definition)}</div>`;
  if (RT === "channels") b.innerHTML = `<div class="grid g3">${d.rows.map((r, i) => `<div class="card"><div class="eyebrow">${esc(r.label)}</div><div style="font-size:30px;font-weight:800">${r.touches}</div><div class="muted">touches · previous ${r.prev_touches}</div>${nbar("Engaged", r.engaged, Math.max(1, r.touches), `${r.engaged} · ${r.rate}%`, NEON[i])}<div class="muted">${r.unique} unique prospects</div></div>`).join("")}</div>${def(d.definition)}`;
  if (RT === "funnel") b.innerHTML = `<div class="card"><h2>Where leads progress or drop off</h2>${d.stages.map((r, i) => nbar(r.stage, r.count, d.stages[0].count, `${r.count} · ${r.conversion}%`, NEON[i % 6])).join("")}${d.biggest_drop ? `<p><b>Biggest drop:</b> ${d.biggest_drop.drop} leads did not reach ${esc(d.biggest_drop.stage)}.</p>` : ""}<p class="muted">Lost ${d.lost} · Nurture ${d.nurture}</p>${def(d.definition)}</div>`;
  if (RT === "sources") b.innerHTML = `<div class="card"><h2>Which sources add leads, and do they convert?</h2>${tbl([["source", "Source"], ["leads", "Leads"], ["contactable", "Contactable"], ["connected", "Engaged"], ["qualified", "Qualified"], ["connect_rate", "Engaged %"], ["qualify_rate", "Qualify %"]], d.rows)}${def(d.definition)}</div>`;
  if (RT === "calls") b.innerHTML = `<div class="grid g3">${[["Attempts", d.attempts], ["Unique prospects", d.unique], ["Connection rate", d.connect_rate + "%"]].map(([l, x]) => `<div class="card"><div class="eyebrow">${l}</div><div style="font-size:30px;font-weight:800">${x}</div></div>`).join("")}</div><div class="card" style="margin-top:16px"><h2>Call outcomes</h2>${d.outcomes.length ? d.outcomes.map((o, i) => nbar(o.outcome, o.pct, 100, `${o.count} · ${o.pct}%`, NEON[i % 6])).join("") : empty("No calls logged in this period")}${def(d.definition)}</div>`;
  if (RT === "team") b.innerHTML = `<div class="card"><h2>Activity and outcomes per salesperson</h2>${tbl([["user", "Name"], ["role", "Role"], ["owned_leads", "Leads owned"], ["touches", "Touches"], ["engaged", "Engaged"], ["attempts", "Calls"], ["unique", "Unique contacted"], ["open_tasks", "Open tasks"], ["overdue", "Overdue", r => `<span class="${r.overdue ? "bad" : ""}">${r.overdue}</span>`]], d.rows)}${def(d.definition)}</div>`;
  if (RT === "health") { const c = d.counts; const sec = (t, k) => `<div class="card"><h2>${t} (${c[k]})</h2>${tbl([["name", "Lead", r => `<a href="#" data-l="${r.id}">${esc(r.name)}</a>`], ["company", "Company"], ["stage", "Stage"], ["owner_id", "Owner", r => esc(uname(r.owner_id))], ["last_activity_at", "Last activity", r => fdate(r.last_activity_at)]], d[k].slice(0, 10))}</div>`; b.innerHTML = `<div class="grid g2">${sec("Stale", "stale")}${sec("Never contacted", "untouched")}${sec("Overdue next action", "overdue")}${sec("No next action", "no_next_action")}</div>${def(d.definition)}`; $$("[data-l]", b).forEach(a => a.onclick = e => { e.preventDefault(); openLead(a.dataset.l); }); }
  if (RT === "enrichment") b.innerHTML = `<div class="grid g4">${[["Jobs", d.jobs], ["Leads processed", d.items], ["Enriched", d.succeeded], ["No match", d.no_match], ["Errors", d.errors], ["Phones found", d.found_phone], ["Emails found", d.found_email], ["Credit usage", d.credit_usage ?? "n/a"]].map(([l, x]) => `<div class="card"><div class="eyebrow">${l}</div><div style="font-size:26px;font-weight:800">${x}</div></div>`).join("")}</div>${def(d.definition)}`;
  if (RT === "insights") b.innerHTML = `<div class="card"><h2>Insights</h2>${d.insights.length ? d.insights.map(insightHtml).join("") : empty("Nothing unusual", "No measurable changes to flag for these filters.")}<p class="muted">Insights come from fixed rules over real data. They show association, not proven cause.</p></div>`;
};
function story(b, d) {
  const C = Object.fromEntries(d.chapters.map(c => [c.id, c])), lb = d.leaderboard, me = lb.rows.find(r => r.id === S.me.id), top = lb.rows[0], gc = ["var(--primary)", "#8b5cff", "#ff3df2", "#9dff4a"];
  const medal = r => r === 1 ? "🥇" : r === 2 ? "🥈" : r === 3 ? "🥉" : r;
  const bd = id => lb.badges.find(x => x.id === id);
  const ch = (c, visual) => `<section class="card n-ch"><div><div class="num">${c.n}</div><h3>${esc(c.title)}</h3><div class="hl">${esc(c.headline)}</div><div class="sub">${esc(c.sub)}</div><div class="def">${esc(c.definition)}</div></div><div>${visual}</div></section>`;
  const chCol = { phone_call: "#9dff4a", linkedin: "#22e6ff", email: "#ffb020" };
  b.innerHTML = `<div class="n-hero"><section class="card"><div class="eyebrow">Mission control · period goals</div><div class="rings" style="margin-top:10px">${d.goals.map((g, i) => ring(g.pct, gc[i], g.label, `${g.actual} / ${g.target}`)).join("")}</div></section>
    <section class="card"><div class="eyebrow">${me ? "Your level" : "Squad leader"}</div>${(me || top) ? (() => { const r = me || top; return `<div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-top:6px"><div style="font-size:46px;font-weight:800;line-height:1;color:#fff;text-shadow:0 0 18px rgba(139,92,255,.8)">Lv ${r.level}</div><div style="flex:1;min-width:180px"><b>${esc(r.name)}</b> <span class="muted">${esc(r.role)}</span><div class="xp" aria-label="Level progress"><i style="width:${r.level_pct}%"></i></div><div class="muted" style="font-size:12px">${r.xp} XP this period · ${r.level_pct}% to next level</div></div><div style="text-align:center"><div style="font-size:28px">🔥 ${r.streak}</div><div class="muted" style="font-size:12px">day streak</div></div></div><div class="bar" style="margin:10px 0 0">${r.badges.length ? r.badges.map(x => `<span class="bdg" title="${esc(bd(x).rule)}">${bd(x).icon} ${esc(bd(x).name)}</span>`).join("") : '<span class="muted">No badges yet. Hover a badge below to see its rule.</span>'}</div>`; })() : empty("No salespeople yet")}</section></div>
    ${ch(C.acquire, C.acquire.sources.length ? C.acquire.sources.map((s, i) => nbar(s.source, s.count, C.acquire.sources[0].count, `${s.count} leads`, NEON[i % 6])).join("") : empty("No new leads in this period"))}
    ${ch(C.reach, `<div style="display:flex;gap:18px;flex-wrap:wrap;align-items:center"><div style="flex:1;min-width:220px">${C.reach.channels.map(r => nbar(r.label, r.touches, Math.max(1, ...C.reach.channels.map(x => x.touches)), `${r.touches} touches`, chCol[r.channel])).join("")}</div>${ring(Math.round(C.reach.coverage), "#22e6ff", "Coverage", "active leads touched")}</div>`)}
    ${ch(C.engage, `${C.engage.channels.map(r => nbar(r.label, r.rate, 100, `${r.engaged}/${r.touches} · ${r.rate}%`, chCol[r.channel])).join("")}<h3 class="eyebrow" style="margin:14px 0 4px">Campaigns by engaged leads</h3>${C.engage.campaigns.slice(0, 5).map((c, i) => nbar(c.name, c.engaged_pct, 100, `${c.engaged_pct}% of ${c.leads}`, NEON[i % 6])).join("")}`)}
    ${ch(C.convert, C.convert.funnel.map((r, i) => nbar(r.stage, r.count, C.convert.funnel[0].count, `${r.count} · ${r.conversion}%`, NEON[i % 6])).join(""))}
    ${ch(C.pace, `<div class="pace">${[["Overdue follow-ups", "overdue", "overdue"], ["Never contacted", "untouched", "untouched"], ["Gone quiet", "stale", "overdue"], ["No next action", "no_next_action", "no_next_action"]].map(([l, k, st]) => `<div class="pc ${C.pace.counts[k] ? "bad" : ""}"><b>${C.pace.counts[k]}</b><span class="muted">${l}</span></div>`).join("")}</div><div class="bar" style="margin-top:12px"><a class="btn sm" href="#/leads" id="gl">Open leads that need work</a></div>`)}
    <section class="card" style="margin-bottom:16px"><div class="eyebrow">The squad</div><h2 style="margin-bottom:12px">Leaderboard</h2>${lb.rows.length ? lb.rows.map(r => `<div class="lb ${r.rank === 1 ? "first" : ""}"><div class="rk">${medal(r.rank)}</div><div><b>${esc(r.name)}</b> <span class="muted">${esc(r.role)} · Lv ${r.level}</span><div class="xp"><i style="width:${r.level_pct}%"></i></div></div><div><b>${r.xp}</b> <span class="muted">XP</span></div><div>🔥 ${r.streak}</div><div class="bd">${r.badges.map(x => `<span class="bdg" title="${esc(bd(x).rule)}">${bd(x).icon} ${esc(bd(x).name)}</span>`).join("") || '<span class="muted">—</span>'}</div></div>`).join("") : empty("No salespeople")}<p class="muted" style="font-size:12px">${esc(lb.formula)} Badges: ${lb.badges.map(x => `${x.icon} ${x.name} = ${x.rule}`).join(" ")}</p></section>`;
  $("#gl", b) && ($("#gl", b).onclick = () => { LV.mode = "board"; });
}

// ---------------------------------------------------------------- AI ASSISTANT
const SUGG = ["How many leads were added this week and from which sources?", "How many prospects were called, connected with and qualified?", "Which leads are due for a callback today?", "Which salesperson has the largest overdue queue?", "Which source has the strongest connection rate?", "Where is the biggest pipeline drop-off?", "What changed compared with the previous period?", "How many enrichment requests succeeded or failed?"];
const CHAT = [];
function mountChat(el, ctxLead) {
  const draw = () => { $(".msgs", el).innerHTML = CHAT.length ? CHAT.map(m => m.u ? `<div class="msg u">${esc(m.u)}</div>` : `<div class="msg a">${esc(m.a.answer)}${m.a.records?.length ? `<ul>${m.a.records.map(r => `<li><a href="#" data-l="${esc(r.id)}">${esc(r.name)}</a>${r.detail ? " · " + esc(r.detail) : ""}</li>`).join("")}</ul>` : ""}${m.a.proposed_action ? `<div class="na"><span>${esc(m.a.proposed_action.label)}</span><button class="btn sm primary" data-act="${m.a.proposed_action.id}">Confirm</button></div>` : ""}${m.a.period || m.a.definition ? `<div class="meta"><b>${m.a.kind === "fact" ? "Fact from CRM data" : ""}</b> ${m.a.period ? "· Period: " + esc(m.a.period) : ""}${m.a.definition ? "<br>Definition: " + esc(m.a.definition) : ""}</div>` : ""}</div>`).join("") : `<div class="muted">Ask about your CRM data. I only read what your role can see, and I never change records without your confirmation.</div><div class="chips">${SUGG.map(s => `<span class="chip">${esc(s)}</span>`).join("")}</div>`;
    const m = $(".msgs", el); m.scrollTop = m.scrollHeight;
    $$(".chip", el).forEach(c => c.onclick = () => send(c.textContent)); $$("[data-l]", el).forEach(a => a.onclick = e => { e.preventDefault(); openLead(a.dataset.l); });
    $$("[data-act]", el).forEach(b => b.onclick = async () => { if (!await confirmBox("Confirm AI-suggested action", b.previousElementSibling.textContent + ". This will be recorded in the audit log.", "Confirm")) return; try { const r = await api(`/ai/actions/${b.dataset.act}/confirm`, { method: "POST" }); toast(`${r.created} tasks created`); b.disabled = true; } catch (e) { toast(e.message, true); } });
  };
  const send = async t => { t = (t || "").trim(); if (!t) return; CHAT.push({ u: t }); draw(); $("input", el).value = ""; try { CHAT.push({ a: await api("/ai/chat", { method: "POST", body: { message: t, lead_id: ctxLead || null } }) }); } catch (e) { CHAT.push({ a: { answer: e.message, kind: "error" } }); } draw(); };
  el.innerHTML = `<div class="chat"><div class="msgs" aria-live="polite"></div><form class="bar" style="margin:0"><input class="grow" placeholder="Ask a question about your CRM" aria-label="Question"><button class="btn primary">Ask</button></form></div>`;
  $("form", el).onsubmit = e => { e.preventDefault(); send($("input", el).value); }; draw(); return send;
}
PAGES.assistant = async v => { v.innerHTML = head("AI Assistant", "Answers come from deterministic CRM calculations, scoped to what you are allowed to see.") + `<div class="card" style="height:68vh"><div id="ch" style="height:100%"></div></div>`; mountChat($("#ch")); };
function openAI(msg, leadId) { const p = $("#aiPanel"); p.classList.remove("hidden"); p.innerHTML = `<div class="dh"><h2>AI assistant</h2><button class="icon-btn" id="ax" aria-label="Close">✕</button></div><div class="db" id="ap" style="display:flex;flex-direction:column"></div>`; $("#ax").onclick = () => p.classList.add("hidden"); const send = mountChat($("#ap"), leadId); if (msg) send(msg); }

// ---------------------------------------------------------------- INTEGRATIONS
PAGES.integrations = async v => {
  const list = await api("/integrations");
  v.innerHTML = head("Integrations", "Secrets live in the server .env only. Nothing here is ever sent to the browser.") + `<div class="grid g2">${list.map(i => `<div class="card int"><div><h2><span class="dot ${i.status === "connected" ? "on" : ""}"></span>${esc(i.name)}</h2><p class="muted">${esc(i.note)}</p><p class="tag">Env: ${esc(i.env)}</p></div><div style="text-align:right"><div class="pill ${i.status === "connected" ? "s7" : "s2"}">${i.status === "connected" ? "Connected" : "Pending configuration"}</div><div style="margin-top:8px"><button class="btn sm" data-t="${i.id}">Test connection</button></div></div></div>`).join("")}</div>`;
  $$("[data-t]").forEach(b => b.onclick = async () => { try { const r = await api(`/integrations/${b.dataset.t}/test`, { method: "POST" }); toast(r.message, !r.ok); } catch (e) { toast(e.message, true); } });
};

// ---------------------------------------------------------------- SETTINGS
let ST = "users";
PAGES.settings = async v => {
  v.innerHTML = head("Settings", "Users, campaigns, pipeline rules and audit history.") + `<div class="tabs">${[["users", "Users & roles"], ["campaigns", "Campaigns"], ["pipeline", "Pipeline & goals"], ["audit", "Audit log"], ["roles", "Role matrix"]].map(([k, l]) => `<button class="tab ${ST === k ? "on" : ""}" data-s="${k}">${l}</button>`).join("")}</div><div id="sb">${sk(5)}</div>`;
  $$(".tab[data-s]").forEach(t => t.onclick = () => { ST = t.dataset.s; PAGES.settings(v); }); const b = $("#sb");
  if (ST === "users") { const us = await api("/users"); const roles = ["admin", "caller", "outreach", "da", "developer"]; b.innerHTML = `<div class="card"><p class="muted">admin = manager/CEO · caller · outreach team · da = data analyst · developer. In Firebase mode the user document id must equal the Firebase UID.</p>${tbl([["name", "Name"], ["email", "Email"], ["role", "Role", r => `<select data-u="${r.id}" data-k="role">${opts(roles, r.role)}</select>`], ["active", "Active", r => `<input type="checkbox" data-u="${r.id}" data-k="active" ${r.active ? "checked" : ""} aria-label="Active">`]], us)}</div>`; $$("[data-u]", b).forEach(e => e.onchange = async () => { try { await api("/users/" + e.dataset.u, { method: "PATCH", body: { [e.dataset.k]: e.dataset.k === "active" ? e.checked : e.value } }); toast("User updated"); } catch (er) { toast(er.message, true); } }); }
  if (ST === "campaigns") { const cs = await api("/campaigns"); b.innerHTML = `<div class="card"><div class="ch"><h2>Campaigns</h2>${can("assign") ? '<button class="btn primary sm" id="nc">+ New campaign</button>' : ""}</div>${tbl([["name", "Name", r => `<span style="color:${r.color}">●</span> ${esc(r.name)}`], ["channel", "Channel", r => r.id === "none" ? "—" : `<select data-cid="${r.id}">${opts([["call", "Calls"], ["linkedin", "LinkedIn"], ["email", "Email"], ["mixed", "Multi-channel"]], r.channel)}</select>`], ["count", "Leads"], ["id", "", r => r.id === "none" ? "" : `<button class="btn sm danger" data-arch="${r.id}">Archive</button>`]], cs)}</div>`;
    if ($("#nc")) $("#nc").onclick = () => campaignModal(() => PAGES.settings(v));
    $$("[data-cid]", b).forEach(s => s.onchange = async () => { try { await api("/campaigns/" + s.dataset.cid, { method: "PATCH", body: { channel: s.value } }); const m = await api("/meta"); S.campaigns = m.campaigns; toast("Channel updated"); } catch (e) { toast(e.message, true); } });
    $$("[data-arch]", b).forEach(x => x.onclick = async () => { if (!await confirmBox("Archive campaign", "Leads stay in the CRM but the campaign group is hidden.", "Archive", true)) return; try { await api("/campaigns/" + x.dataset.arch, { method: "PATCH", body: { archived: true } }); const m = await api("/meta"); S.campaigns = m.campaigns; PAGES.settings(v); } catch (e) { toast(e.message, true); } }); }
  if (ST === "pipeline") { const s = await api("/settings"), g = s.goals; b.innerHTML = `<div class="card"><form id="sf" class="form2"><label class="full">Lead stages (one per line, first must be New)<textarea id="s1" rows="9">${esc(s.stages.join("\n"))}</textarea></label><label class="full">Call outcomes (one per line)<textarea id="s2" rows="9">${esc(s.outcomes.join("\n"))}</textarea></label><label>Stale after (days)<input type="number" id="s3" min="1" value="${s.stale_days}"></label><label style="flex-direction:row;align-items:center;gap:8px;color:var(--text)"><input type="checkbox" id="s4" ${s.require_lost_reason ? "checked" : ""}>Require a reason for Lost</label>
    <label class="full">What we offer, in one line (used in LinkedIn and email drafts)<input id="s5" value="${esc(s.pitch)}"></label><h3 class="full">Goals per 30 days (scaled to the selected report period)</h3>${[["touches", "Outreach touches"], ["engaged", "Engaged responses"], ["meetings", "Meetings booked"], ["won", "Deals won"]].map(([k, l]) => `<label>${l}<input type="number" min="1" data-g="${k}" value="${g[k]}"></label>`).join("")}<div class="acts full"><button class="btn primary">Save</button></div></form></div>`;
    $("#sf").onsubmit = async e => { e.preventDefault(); const L = id => $(id).value.split("\n").map(x => x.trim()).filter(Boolean); const goals = {}; $$("[data-g]").forEach(i => goals[i.dataset.g] = +i.value); try { await api("/settings", { method: "PUT", body: { stages: L("#s1"), outcomes: L("#s2"), stale_days: +$("#s3").value, require_lost_reason: $("#s4").checked, pitch: $("#s5").value, goals } }); toast("Saved. Reloading…"); setTimeout(() => location.reload(), 600); } catch (er) { toast(er.message, true); } }; }
  if (ST === "audit") { const a = await api("/audit"); b.innerHTML = `<div class="card">${tbl([["at", "When", r => fdate(r.at)], ["user", "User"], ["action", "Action"], ["entity", "Entity"], ["detail", "Detail"]], a)}</div>`; }
  if (ST === "roles") { const rows = [["Today, Overview, Reports", "all roles"], ["Leads: view", "admin, developer, da, outreach (all) · caller (own only)"], ["Leads: create/edit", "admin, developer, outreach, caller (own)"], ["Campaigns, assign / reassign", "admin, developer, outreach"], ["Lead Discovery, import, enrichment", "admin, developer, outreach"], ["Log activity, tasks, approve suggestions", "admin, developer, caller, outreach"], ["Run suggestions now", "admin, developer, outreach"], ["Integrations (view)", "admin, developer, outreach"], ["Settings, users, audit", "admin, developer"], ["Data analyst (da)", "read-only reports + CSV export"]]; b.innerHTML = `<div class="card"><p class="muted">Enforced server-side in services.py (PERMS). Edit it there to change what a role can do.</p>${tbl([[0, "Capability", r => esc(r[0])], [1, "Roles", r => esc(r[1])]], rows)}</div>`; }
};

// ---------------------------------------------------------------- BOOT + AUTH
async function initFirebase() {
  const { initializeApp } = await import("https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js");
  const A = await import("https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js");
  const auth = A.getAuth(initializeApp(CFG.FIREBASE));
  S.fb = { auth, A, getToken: async () => auth.currentUser ? auth.currentUser.getIdToken() : null };
  return new Promise(res => A.onAuthStateChanged(auth, u => res(u)));
}
const LOGIN_ERRORS = { "auth/invalid-credential": "Wrong email or password, or no account exists for that email.", "auth/user-not-found": "No Firebase account for that email.", "auth/wrong-password": "Wrong password.", "auth/operation-not-allowed": "Email/Password sign-in is not enabled in Firebase Authentication.", "auth/invalid-api-key": "The Firebase apiKey in config.js is invalid.", "auth/network-request-failed": "Network error reaching Firebase.", "auth/too-many-requests": "Too many attempts. Wait a few minutes.", "auth/user-disabled": "This Firebase account is disabled." };
let bootSlow;
function bootStatus(msg) {
  $("#bootMsg").textContent = msg; $("#boot").classList.remove("hidden"); clearTimeout(bootSlow);
  bootSlow = setTimeout(() => { $("#bootMsg").textContent = msg + " Still working… if the server was idle, the first load can take about 10 seconds."; }, 5000);
}
function bootDone() { clearTimeout(bootSlow); $("#boot").classList.add("hidden"); }
function cannotStart(message) {
  bootDone(); document.body.innerHTML = `<div class="login"><div class="card login-card"><h1>Cannot start</h1><p>${esc(message)}</p><button class="btn" onclick="location.reload()">Retry</button> ${CFG.AUTH_ENABLED ? '<button class="btn" id="soErr">Sign out</button>' : ""}</div></div>`;
  if ($("#soErr")) $("#soErr").onclick = signOut;
}
function showLogin() {
  $("#login").classList.remove("hidden"); $("#lEmail").focus();
  $("#loginForm").onsubmit = async e => {
    e.preventDefault(); const btn = $("#lBtn"); btn.disabled = true; btn.textContent = "Signing in…"; $("#lErr").textContent = "";
    try { await S.fb.A.signInWithEmailAndPassword(S.fb.auth, $("#lEmail").value, $("#lPass").value); }
    catch (er) { $("#lErr").textContent = `Sign-in failed: ${LOGIN_ERRORS[er.code] || er.message || "unknown error"} (${er.code || "no code"})`; btn.disabled = false; btn.textContent = "Sign in"; console.error(er); return; }
    $("#login").classList.add("hidden"); $("#loginForm").reset(); btn.disabled = false; btn.textContent = "Sign in";
    history.replaceState(null, "", "#/today"); await start();                      // no page reload, and always begins at the start page
  };
}
async function boot() {
  bootStatus("Loading…");
  if (CFG.AUTH_ENABLED) {
    let u; try { u = await initFirebase(); } catch { return cannotStart("Could not load the sign-in service. Check your internet connection and retry."); }
    if (!u) { bootDone(); showLogin(); return; }
  }
  await start();
}
async function start() {
  bootStatus("Loading your workspace…");
  let m; try { m = await api("/meta"); } catch (e) { return cannotStart(e.message); }
  let prev = null; try { prev = localStorage.getItem("crm_uid"); localStorage.setItem("crm_uid", m.me.id); } catch { }
  if (prev !== m.me.id) history.replaceState(null, "", "#/today");                // a different person (or a first visit) always starts at the beginning
  Object.assign(S, { me: m.me, perms: m.perms, stages: m.stages, outcomes: m.outcomes, sources: m.sources, users: m.users, types: m.activity_types, oppStages: m.opp_stages, campaigns: m.campaigns, obt: m.outcomes_by_type });
  $("#app").classList.remove("hidden");
  $("#me").innerHTML = `<span>${esc(m.me.name)} · ${esc(m.me.role)}</span><span class="av">${esc(m.me.name[0])}</span>${CFG.AUTH_ENABLED ? '<button class="btn sm" id="so">Sign out</button>' : ""}`;
  if (m.demo) $("#demoBadge").classList.remove("hidden");
  if (m.auth_disabled && !CFG.AUTH_ENABLED) { $("#mockWrap").classList.remove("hidden"); $("#mockSel").innerHTML = opts(m.users.map(u => [u.id, `${u.name} · ${u.role}`]), m.me.id); $("#mockSel").onchange = e => { localStorage.setItem("mockUser", e.target.value); location.hash = "#/today"; location.reload(); }; }
  if ($("#so")) $("#so").onclick = signOut;
  $("#sideFoot").textContent = CFG.AUTH_ENABLED ? "Signed in with Firebase" : "Test mode: auth switch is OFF";
  const dark = () => (document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark"; const paint = () => $("#themeBtn").textContent = dark() ? "☀" : "☾";
  $("#themeBtn").onclick = () => { const t = dark() ? "light" : "dark"; document.documentElement.dataset.theme = t; localStorage.theme = t; paint(); }; paint();
  $("#menuBtn").onclick = () => $("#side").classList.toggle("open");
  $("#quickAdd").style.display = can("leads:write") ? "" : "none"; $("#quickAdd").onclick = () => leadForm();
  $("#aiBtn").style.display = can("ai:use") ? "" : "none"; $("#aiBtn").onclick = () => openAI();
  let t; $("#gsearch").onkeydown = e => { if (e.key === "Enter") { LV.q = e.target.value; LV.cursors = [""]; e.target.value = ""; location.hash = "#/leads"; route(); } };
  document.addEventListener("keydown", e => { if (e.key === "Escape") { closeModal(); $("#aiPanel").classList.add("hidden"); } });
  window.addEventListener("hashchange", route); bootDone(); route();
}
boot();
