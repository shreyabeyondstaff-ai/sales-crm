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
async function signOut() {
  try { await S.fb.A.signOut(S.fb.auth); } catch { }
  try { localStorage.removeItem("crm_uid"); } catch { }
  history.replaceState(null, "", location.pathname); location.reload();       // next person always starts at the beginning
}
let expiring = false;
function sessionExpired() { if (expiring) return; expiring = true; toast("Your session expired. Please sign in again.", true); setTimeout(signOut, 1500); }
async function api(path, { method = "GET", body, form, raw } = {}) {
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
  } finally { }
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
const CH_OPTS = [["call", "Calls"], ["linkedin", "LinkedIn"], ["email", "Email"]];
const leadChannels = l => (l.channels && l.channels.length) ? l.channels : (c => ["call", "linkedin", "email"].includes(c) ? [c] : [])(campOf(l.campaign_id).channel);
const targetHtml = id => `<div class="card target" id="${id}"><b>Where should these leads go?</b>
  <div class="bar" style="margin:8px 0"><span class="muted">Channels (tick every one you will use):</span>${CH_OPTS.map(([v, lb]) => `<label class="chk"><input type="checkbox" class="it-ch" value="${v}"> ${lb}</label>`).join("")}</div>
  <div class="bar"><label style="min-width:240px">Campaign<select class="it-camp"></select></label><label class="it-newl hidden" style="min-width:240px">New campaign name<input class="it-new" maxlength="80" placeholder="e.g. Fintech VPs Q1"></label>${can("assign") ? `<label style="min-width:220px">Assign to<select class="it-own">${userOpts("", "Unassigned")}</select></label>` : ""}</div>
  <p class="muted" style="margin:6px 0 0">Each lead appears in its owner's My Day for every channel you tick. Callers only see leads assigned to them.</p></div>`;
function bindTarget(root) {
  const camp = $(".it-camp", root), nl = $(".it-newl", root);
  camp.innerHTML = `<option value="">No campaign</option>${can("assign") ? '<option value="__new">New campaign…</option>' : ""}${S.campaigns.map(c => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("")}`;
  const sync = () => nl.classList.toggle("hidden", camp.value !== "__new"); sync();
  camp.onchange = () => { sync(); const c = S.campaigns.find(x => x.id === camp.value); if (c && ["call", "linkedin", "email"].includes(c.channel) && !$$(".it-ch", root).some(i => i.checked)) $$(".it-ch", root).forEach(i => { i.checked = i.value === c.channel; }); };
}
function readTarget(root) {
  const channels = $$(".it-ch", root).filter(i => i.checked).map(i => i.value), v = $(".it-camp", root).value, name = ($(".it-new", root).value || "").trim();
  if (!channels.length) throw new Error("Choose at least one channel (Calls, LinkedIn or Email)");
  if (v === "__new" && !name) throw new Error("Type a name for the new campaign");
  return { channels, campaign_id: v && v !== "__new" ? v : null, new_campaign_name: v === "__new" ? name : null, owner_id: $(".it-own", root)?.value || null };
}
const refreshCampaigns = async () => { try { S.campaigns = (await api("/meta")).campaigns; } catch { } };
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
  ["myday", "My Day", "◎", "activities:write", ["admin", "developer"]], ["dashboard", "Dashboard", "▦", "reports:read"], ["leads", "Leads", "☰", "leads:read"], ["companies", "Companies", "▣", "companies:read"],
  ["intake", "Lead intake", "⇉", "assign"], ["followups", "Follow-ups", "✓", "tasks:read"], ["opportunities", "Opportunities", "◆", "opps:read"], ["settings", "Settings", "⚙", "integrations:read"]];
const homePage = () => can("assign") ? "dashboard" : can("activities:write") ? "myday" : "dashboard";
const ALIAS = { today: ["home"], overview: ["dashboard"], reports: ["dashboard"], assistant: ["dashboard"], assign: ["intake", () => { INT.tab = "assign"; }], discovery: ["intake", () => { INT.tab = "import"; }], workspace: ["myday"], integrations: ["settings", () => { ST = "integrations"; }] };
const PAGES = {};
function renderNav() {
  const cur = location.hash.slice(2).split("/")[0] || homePage();
  $("#nav").innerHTML = NAV.filter(n => can(n[3]) && !(n[4] || []).includes(S.me.role)).map(n => `<a class="nav-i ${n[0] === cur ? "on" : ""}" href="#/${n[0]}" data-p="${n[0]}"><span class="ic">${n[2]}</span>${esc(n[1])}</a>`).join("");
  $("#side").classList.remove("open");
}
let ROUTE_SEQ = 0;
async function route() {
  const my = ++ROUTE_SEQ; let p = location.hash.slice(2).split("/")[0] || homePage();
  if (ALIAS[p]) { const [to, fx] = ALIAS[p]; fx && fx(); p = to === "home" ? homePage() : to; history.replaceState(null, "", "#/" + p); }      // old bookmarks keep working
  let item = NAV.find(n => n[0] === p);
  if (!item || !can(item[3]) || (item[4] || []).includes(S.me.role)) { p = homePage(); item = NAV.find(n => n[0] === p); history.replaceState(null, "", "#/" + p); }   // keep the URL honest
  renderNav(); const view = $("#view"), v = document.createElement("div");      // fresh container: a slow old page can never paint over a newer one
  v.innerHTML = `<div class="pageload"><div class="spin"></div><span class="pl-msg">Loading ${esc(item?.[1] || "page")}…</span></div>`; view.replaceChildren(v);
  const slow = setTimeout(() => { const m = v.querySelector(".pl-msg"); if (m) m.textContent = `Still loading ${item?.[1] || "page"}… the server may be waking up.`; }, 5000);
  try { await PAGES[p](v); }
  catch (e) { if (my !== ROUTE_SEQ) return; v.innerHTML = `<div class="card">${empty("Could not load this page", e.message)}<div style="text-align:center"><button class="btn primary" id="retry">Try again</button></div></div>`; v.querySelector("#retry").onclick = route; }
  finally { clearTimeout(slow); }
  if (my === ROUTE_SEQ) view.focus({ preventScroll: true });
}
const tbar = (sub, actions = "") => (sub || actions) ? `<div class="page-h tb"><p>${esc(sub)}</p><div class="bar" style="margin:0">${actions}</div></div>` : "";
// Company cues, shown to a caller when a lead is open. hero = why call now, script = what to say (copyable), chip = short facts, detail = long research (collapsed).
function coBrief(c, o = {}) {
  if (!c) return "";
  const by = k => (c.cues || []).filter(x => x.kind === k), i = c.info || {}, det = by("detail");
  const meta = [i.industry, i.employees ? Number(i.employees).toLocaleString() + " employees" : "", i.location].filter(Boolean).join(" · ");
  return `<section class="co">${o.bare ? "" : `<div class="co-h"><div><b>${esc(c.name)}</b><div class="muted">${esc(meta) || "Company brief"}</div></div>${i.website ? `<a href="${esc(/^https?:/.test(i.website) ? i.website : "http://" + i.website)}" target="_blank" rel="noopener noreferrer">Website</a>` : ""}</div>`}
    ${by("hero").map(x => `<div class="cx-sig"><span>${esc(x.label)}</span><b>${esc(x.value)}</b></div>`).join("")}
    ${by("script").map(x => `<div class="co-script"><div class="co-sh"><span>${esc(x.label)}</span><button class="btn sm" data-copy="${esc(x.value)}">Copy</button></div><p>${esc(x.value)}</p></div>`).join("")}
    ${by("chip").length ? `<div class="co-chips">${by("chip").map(x => `<span class="co-chip"><i>${esc(x.label)}</i>${esc(x.value)}</span>`).join("")}</div>` : ""}
    ${det.length ? `<details class="co-d"><summary>Research notes (${det.length})</summary>${det.map(x => `<div class="co-dv"><b>${esc(x.label)}</b>${esc(x.value)}</div>`).join("")}</details>` : ""}</section>`;
}
const bindCopy = root => $$("[data-copy]", root).forEach(b => b.onclick = () => { try { navigator.clipboard.writeText(b.dataset.copy); toast("Copied"); } catch { toast("Copy failed. Select the text and copy it manually.", true); } });
// Multi-select dropdown (campaigns, company lists). Tick and untick freely; the change applies when the panel closes, so there is one reload, not one per click.
const msHtml = (id, noun, options, sel) => {
  const on = new Set((sel || "").split(",").filter(Boolean)), lab = !on.size ? `All ${noun}` : on.size === 1 ? esc(options.find(o => o[0] === [...on][0])?.[1] || "1 selected") : `${on.size} ${noun}`;
  return `<details class="menu-d ms" id="${id}"><summary class="btn"><span class="ms-lab">${lab}</span> ▾</summary><div class="menu-p ms-p">${options.length > 8 ? `<input class="ms-q" placeholder="Search ${noun}" aria-label="Search ${noun}">` : ""}<div class="ms-a"><button type="button" data-all>Select all</button><button type="button" data-none>Clear</button></div><div class="ms-l">${options.map(([v, l, n]) => `<label class="ms-o"><input type="checkbox" value="${esc(v)}" ${on.has(v) ? "checked" : ""}><span>${esc(l)}</span>${n != null ? `<i>${n}</i>` : ""}</label>`).join("") || '<div class="muted" style="padding:8px">Nothing to pick yet</div>'}</div></div></details>`;
};
function msBind(id, noun, onChange) {
  const d = $("#" + id); if (!d) return; const was = () => [...$$("input[type=checkbox]:checked", d)].map(c => c.value), start = was().join(",");
  const lab = () => { const v = was(), all = [...$$("input[type=checkbox]", d)]; d.querySelector(".ms-lab").textContent = !v.length ? `All ${noun}` : v.length === 1 ? all.find(c => c.checked).nextElementSibling.textContent : `${v.length} ${noun}`; };
  $$("input[type=checkbox]", d).forEach(c => c.onchange = lab);
  d.querySelector("[data-all]").onclick = () => { $$("input[type=checkbox]", d).forEach(c => { if (!c.closest("label").hidden) c.checked = true; }); lab(); };
  d.querySelector("[data-none]").onclick = () => { $$("input[type=checkbox]", d).forEach(c => c.checked = false); lab(); };
  const q = d.querySelector(".ms-q"); if (q) q.oninput = () => $$(".ms-o", d).forEach(o => o.hidden = !o.textContent.toLowerCase().includes(q.value.toLowerCase()));
  d.addEventListener("toggle", () => { if (!d.open && was().join(",") !== start) onChange(was().join(",")); });
}
document.addEventListener("click", e => $$("details.ms[open]").forEach(d => { if (!d.contains(e.target)) d.open = false; }));
const go = p => { if (location.hash === "#/" + p) route(); else location.hash = "#/" + p; };
const head = (t, sub, actions = "") => `<div class="page-h"><div><h1>${esc(t)}</h1>${sub ? `<p>${esc(sub)}</p>` : ""}</div><div class="bar" style="margin:0">${actions}</div></div>`;

// ---------------------------------------------------------------- TODAY (command center; nothing changes until a person approves)
const AGN = { triage: "triage", prep: "call prep", cleaner: "data cleaner", watcher: "stale watcher", digest: "digest" };
const agTag = a => `<span class="ag ${esc(a)}">${esc(AGN[a] || a)}</span>`;
const scCls = n => n >= 60 ? "sc" : "sc mid";
const ago = s => { if (!s) return "never"; const m = Math.round((Date.now() - new Date(s)) / 6e4); return m < 1 ? "just now" : m < 60 ? m + " min ago" : m < 1440 ? Math.round(m / 60) + " h ago" : Math.round(m / 1440) + " d ago"; };
const TILES = [["call", "☎", "Calls to make", "var(--ok)", "var(--ok-soft)"], ["linkedin", "in", "LinkedIn touches", "var(--info)", "var(--info-soft)"], ["email", "✉", "Emails to send", "var(--warn)", "var(--warn-soft)"], ["ops", "⚙", "Pipeline & data", "var(--primary)", "var(--primary-soft)"]];
// ---------------------------------------------------------------- MY DAY (the caller's one work screen: pick a lead, read the cue, call or message, log in one click, move on)
const MD = { sel: null, camp: "", ch: "" };
const greet = () => { const h = new Date().getHours(); return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"; };
const QUICK = [["No Answer", "No answer", "no"], ["Voicemail", "Voicemail", "no"], ["Busy", "Busy", "no"], ["Connected", "Connected", "ok"], ["Interested", "Interested", "ok"], ["Not Interested", "Not interested", "no"], ["Wrong Number", "Wrong number", "no"]];
const QUICK_LI = [["Connection Sent", "Request sent", "ok"], ["Connection Accepted", "Accepted", "ok"], ["Replied", "Replied", "ok"], ["No Response", "No response", "no"], ["Not Interested", "Not interested", "no"]];
const MD_GROUPS = [["callbacks", "Callbacks due"], ["followups", "Follow-ups due"], ["new", "New leads"], ["other", "Other assigned leads"]];
const mdPoints = b => (b || "").split("\n").filter(Boolean).slice(1).map(ln => { const i = ln.indexOf(":"); return i > 0 && i < 24 ? `<li><b>${esc(ln.slice(0, i + 1))}</b>${esc(ln.slice(i + 1))}</li>` : `<li>${esc(ln)}</li>`; }).join("");
const chipsOf = l => (l.channels && l.channels.length) ? l.channels : ["call"];
const MD_MODE = { call: "phone_call", linkedin: "linkedin", email: "email" };
// My day fills the screen: both panels end where the sidebar ends and scroll inside themselves (no page scrollbar). Stacked on narrow screens.
// "Open in Apollo": opens the lead's page in Apollo in a new tab. It never dials. Uses the Apollo contact id, else the person id.
const apolloUrl = l => { const L = S.apolloLinks || {}, cid = l.apollo_contact_id, pid = l.apollo_id && !String(l.apollo_id).startsWith("demo_") ? l.apollo_id : ""; return cid && L.contact ? L.contact.replace("{id}", encodeURIComponent(cid)) : pid && L.person ? L.person.replace("{id}", encodeURIComponent(pid)) : ""; };
const phoneKind = t => ({ work_hq: "company switchboard", mobile: "mobile", work_direct: "direct", direct: "direct", personal: "personal", home: "home" }[t] || (t ? String(t).replace(/_/g, " ") : ""));
function fitCx() {
  const el = $(".cx"); if (!el) return;
  if (innerWidth <= 980) { el.style.height = ""; return; }
  el.style.height = Math.max(420, innerHeight - (el.getBoundingClientRect().top + scrollY) - 24) + "px";
}
addEventListener("resize", fitCx);
document.addEventListener("visibilitychange", () => { if (!document.hidden && $(".cx, .cx-done") && Date.now() - (MD.at || 0) > 30000) route(); });      // coming back to the tab: show what changed meanwhile (new assignments, rolled-back lists)
PAGES.myday = async v => {
  const d = await api("/today/mine"), t = d.tiles; MD.at = Date.now();
  const have = new Set(d.items.map(x => x.lead.campaign_id || "none")); MD.camp = MD.camp.split(",").filter(i => have.has(i)).join(","); const cset = new Set(MD.camp.split(",").filter(Boolean));
  const cnt = c => d.items.filter(x => chipsOf(x.lead).includes(c)).length, multi = cnt("linkedin") + cnt("email") > 0; if (MD.ch && !cnt(MD.ch)) MD.ch = "";
  const items = d.items.filter(x => (!cset.size || cset.has(x.lead.campaign_id || "none")) && (!MD.ch || chipsOf(x.lead).includes(MD.ch))), cids = [...new Set(d.items.map(x => x.lead.campaign_id || "none"))];
  if (!items.find(x => x.lead.id === MD.sel)) MD.sel = items[0]?.lead.id || null;
  const total = t.done_today + t.to_call, pct = total ? Math.round(100 * t.done_today / total) : 0, first = esc((S.me.name || "").split(" ")[0]);
  const qi = x => { const l = x.lead; return `<div class="cx-i ${l.id === MD.sel ? "on" : ""}" data-id="${esc(l.id)}" tabindex="0" role="button"><div style="min-width:0"><div class="n">${esc(l.full_name)}${x.has_company ? ' <span class="cdot" title="Company brief available">●</span>' : ""}</div><div class="s">${esc(l.title)}${l.title && l.company ? " · " : ""}${esc(l.company)}</div><div class="r ${x.overdue ? "bad" : ""}">${esc(x.reason)}${x.due_at ? " · " + fdate(x.due_at) : ""}</div><div class="cx-cp">${esc(campOf(l.campaign_id).name)}</div></div><div class="cx-rf" title="Fit score">${x.fit}<span>fit</span></div></div>`; };
  const list = MD_GROUPS.map(([g, title]) => { const xs = items.filter(x => x.group === g); return xs.length ? `<div class="cx-qh"><span>${title}</span><span>${xs.length}</span></div>${xs.map(qi).join("")}` : ""; }).join("") + (d.total > d.items.length ? `<div class="cx-qh"><span>Showing the first ${d.items.length} of ${d.total}</span></div>` : "");
  const qhead = (multi ? `<div class="cx-qh"><div class="seg" role="group" aria-label="Channel">${[["", "All"], ["call", "Calls"], ["linkedin", "LinkedIn"], ["email", "Email"]].map(([c, l]) => `<button data-mch="${c}" class="${MD.ch === c ? "on" : ""}">${l}</button>`).join("")}</div></div>` : "") + (cids.length > 1 ? `<div class="cx-qh">${msHtml("cxCamp", "campaigns", cids.map(id => [id, campOf(id === "none" ? null : id).name, d.items.filter(x => (x.lead.campaign_id || "none") === id).length]), MD.camp)}</div>` : "");
  v.innerHTML = `<div class="cx-head"><div><h1>${greet()}, ${first}</h1><p>${t.to_call ? `${t.to_call} to work today, best ones first.` : "Nothing due right now."} ${t.assigned} ${t.assigned === 1 ? "lead is" : "leads are"} assigned to you in total.</p></div>
      <div class="cx-prog"><div class="t"><span>Today's progress</span><span><b>${t.done_today}</b> logged · ${t.to_call} to go</span></div><div class="cx-bar"><i style="width:${pct}%"></i></div></div></div>
    <div class="cx-chips"><span class="cx-chip"><b>${t.callbacks}</b> callbacks due</span><span class="cx-chip"><b>${t.followups}</b> follow-ups</span><span class="cx-chip"><b>${t.new}</b> new leads</span><span class="cx-chip"><b>${t.connected_today}</b> connected today</span></div>` +
    (items.length ? `<div class="cx"><section class="card cx-q" aria-label="Your list">${qhead}${list}</section><section class="card cx-c" id="cxr" aria-live="polite"></section></div>` : `<div class="card cx-done"><div class="big">📭</div><h2>Nothing assigned to you yet</h2><p class="muted">Leads appear here the moment your manager assigns them to you.</p></div>`);
  fitCx(); requestAnimationFrame(fitCx);
  if (!items.length) return;
  const cur = () => items.find(x => x.lead.id === MD.sel), idx = () => items.findIndex(x => x.lead.id === MD.sel), next = () => (items[idx() + 1] || items[idx() - 1])?.lead.id || null;
  const contact = (l, mode) => {
    const li = l.linkedin_url ? `<a class="btn ${mode === "linkedin" ? "cx-main" : ""}" href="${esc(l.linkedin_url)}" target="_blank" rel="noopener noreferrer">LinkedIn</a>` : "", em = l.email ? `<a class="btn ${mode === "email" ? "cx-main" : ""}" href="mailto:${esc(l.email)}">${mode === "email" ? "✉ " + esc(l.email) : "Email"}</a>` : "", ph = l.phone ? `<span class="cx-phone" title="Phone number (nothing is dialled from here)">☎ ${esc(l.phone)}${l.phone_type ? ` <span class="cx-pk ${l.phone_type === "work_hq" ? "hq" : ""}">${esc(phoneKind(l.phone_type))}</span>` : ""}</span>` : "";
    const apb = apolloUrl(l) ? `<a class="cx-apollo" href="${esc(apolloUrl(l))}" target="_blank" rel="noopener noreferrer" title="Opens this lead in Apollo in a new tab. Nothing is dialled.">Open in Apollo <span aria-hidden="true">↗</span></a>` : "";
    if (mode === "linkedin") return `<div class="cx-callrow">${li || '<span class="muted">No LinkedIn profile for this lead.</span>'}${ph}${em}</div>`;
    if (mode === "email") return `<div class="cx-callrow">${em || '<span class="muted">No email for this lead.</span>'}${li}${ph}</div>`;
    return l.phone ? `<div class="cx-callrow">${apb}${ph}<button class="btn" id="cxCopy">Copy number</button>${li}${em}</div>${apb ? '<div class="cx-hint">To call, press the ☎ icon next to the name in Apollo.</div>' : ""}` : `<div class="cx-nophone">${apb}<b>No phone number for this lead.</b> ${can("enrich") ? '<button class="btn sm" id="cxEnr">Find it with Apollo</button>' : ""}${li}${em}</div>`;
  };
  const draw = () => {
    const x = cur(), l = x.lead, box = $("#cxr"), chl = chipsOf(l), mode = MD.ch || (chl.includes("call") ? "call" : chl[0]), quick = mode === "call" ? QUICK : mode === "linkedin" ? QUICK_LI : [];
    const info = [["Location", l.location], ["Source", l.source], ["Campaign", campOf(l.campaign_id).name], ["Touches", `${x.touches ?? l.attempts ?? 0}`], ["Email", l.email]].filter(r => r[1]);
    box.innerHTML = `<div class="cx-top"><div style="min-width:0"><h2>${esc(l.full_name)}</h2><div class="muted">${esc(l.title)}${l.title && l.company ? " at " : ""}${esc(l.company)}</div><div class="bar" style="margin:8px 0 0">${pill(l.stage)} ${chl.map(chBadge).join(" ")}</div></div>
        <div class="cx-score" title="${esc(x.fit_why.map(f => `+${f.points} ${f.label}`).join("\n"))}"><b>${x.fit}</b><span>fit score</span></div></div>
      ${contact(l, mode)}
      <div class="cx-body"><div class="cx-main">
        <div class="cx-sec"><h4>Why call now</h4><div class="cx-why ${x.overdue ? "bad" : ""}">${esc(x.reason)}${x.due_at ? " · " + fdate(x.due_at) : ""}</div>
          ${l.signal ? `<div class="cx-sig"><span>Signal</span><b>${esc(l.signal)}</b>${l.signal_url && /^https?:/.test(l.signal_url) ? ` <a href="${esc(l.signal_url)}" target="_blank" rel="noopener noreferrer">source</a>` : ""}</div>` : ""}</div>
        <div id="cxco"></div>
        ${x.brief ? `<details class="co-d" ${x.has_company || l.signal ? "" : "open"}><summary>Talking points</summary><ul class="cx-pts">${mdPoints(x.brief)}</ul></details>` : ""}
      </div><aside class="cx-side">
        <div class="cx-log"><h4>${mode === "call" ? "Log this call" : "Log this touch"}</h4><input id="cxn" class="cx-note" placeholder="Note (optional)" aria-label="Note">
          <div class="cx-oc">${quick.map(([o, lb, tone]) => `<button class="btn ${tone}" data-oc="${esc(o)}">${lb}</button>`).join("")}<button class="btn" id="cxMore">${quick.length ? "More options…" : "Log activity"}</button></div>
          ${mode === "email" ? `<p class="muted" style="margin:8px 0 0">Emails go out from your Apollo sequences. Sends, replies and bounces appear after a sync.${can("assign") ? ' <button class="btn sm" id="cxSync">Sync emails from Apollo</button>' : ""}</p>` : ""}</div>
        <div class="cx-sec"><h4>Lead</h4><dl class="kv">${info.map(([k, v]) => dd(k, esc(v))).join("")}</dl></div>
        ${x.recent && x.recent.length ? `<details class="co-d"><summary>Recent history (${x.recent.length})</summary><ul class="cx-pts">${x.recent.map(a => `<li><b>${esc(a.type.replace("_", " "))}: ${esc(a.outcome)}</b> <span class="muted">${fdate(a.at)}</span>${a.notes ? `<div class="muted" style="white-space:pre-wrap">${esc(a.notes)}</div>` : ""}</li>`).join("")}</ul></details>` : ""}
      </aside></div>
      <div class="cx-foot"><button class="btn sm" id="cxSkip">Skip to next</button><button class="btn sm" id="cxOpen">Open full profile</button></div>`;
    const fillCo = () => {                                      // company cues: inline for the first rows, fetched on demand for the rest
      const el = $("#cxco"); if (!el) return;
      if (x.company) { el.innerHTML = coBrief(x.company); bindCopy(el); }
      else if (!x.has_company && !l.signal) el.innerHTML = `<div class="cx-empty"><b>No signal or company brief yet.</b><span>${can("companies:write") ? 'Import this company under <a href="#/companies">Companies</a> and its signal and pitch appear here.' : "Ask your manager to add this company's signal."}</span></div>`;
      else if (x.company === undefined && x.has_company) {
        el.innerHTML = '<div class="co-load">Loading company brief…</div>'; const id = l.id;
        api(`/leads/${id}/company`).then(c => { x.company = c; if (MD.sel === id) fillCo(); }).catch(() => { x.company = null; if (MD.sel === id) fillCo(); });
      }
    }; fillCo();
    if ($("#cxCopy")) $("#cxCopy").onclick = () => { navigator.clipboard?.writeText(l.phone); toast("Number copied"); };
    if ($("#cxEnr")) $("#cxEnr").onclick = () => enrichConfirm([l.id]);
    if ($("#cxSync")) $("#cxSync").onclick = async e => { const b = e.target; b.disabled = true; b.textContent = "Syncing…"; try { const r = await api("/integrations/apollo/sync-emails", { method: "POST" }); toast(`Apollo emails: ${r.sent} new sent, ${r.replies} replies, ${r.bounces} bounces.`); PAGES.myday(v); } catch (er) { toast(er.message, true); b.disabled = false; b.textContent = "Sync emails from Apollo"; } };
    $("#cxOpen").onclick = () => openLead(l.id);
    $("#cxSkip").onclick = () => { MD.sel = items[(idx() + 1) % items.length].lead.id; $$(".cx-i", v).forEach(r => r.classList.toggle("on", r.dataset.id === MD.sel)); draw(); };
    $("#cxMore").onclick = () => logActivityModal(l, () => { MD.sel = next(); PAGES.myday(v); }, MD_MODE[mode]);
    $$("[data-oc]", box).forEach(b => b.onclick = async () => {
      const o = b.dataset.oc, note = $("#cxn").value.trim(); $$(".cx-oc .btn", box).forEach(z => z.disabled = true);
      try {
        const r = await api("/activities", { method: "POST", body: { lead_id: l.id, type: MD_MODE[mode], outcome: o, notes: note } }); toast(`${o} logged for ${l.full_name}`);
        if (r.suggested_stage && can("leads:write") && await confirmBox("Move stage?", `Outcome "${o}" suggests moving ${l.full_name} from ${r.lead.stage} to ${r.suggested_stage}. Nothing changes unless you confirm.`, `Move to ${r.suggested_stage}`)) await moveStage(l.id, r.suggested_stage);
        MD.sel = next(); PAGES.myday(v);
      } catch (er) { toast(er.message, true); $$(".cx-oc .btn", box).forEach(z => z.disabled = false); }
    });
  };
  msBind("cxCamp", "campaigns", val => { MD.camp = val; PAGES.myday(v); });
  $$("[data-mch]", v).forEach(b => b.onclick = () => { MD.ch = b.dataset.mch; PAGES.myday(v); });
  $$(".cx-i", v).forEach(r => { const pick = () => { MD.sel = r.dataset.id; $$(".cx-i", v).forEach(z => z.classList.toggle("on", z === r)); draw(); }; r.onclick = pick; r.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } }; });
  draw();
};

const T = { ch: "", owner: "", open: {}, more: {} };
const renderSuggestions = async v => {
  const d = await api("/today"); const st = d.status; let all = d.suggestions;
  if (T.owner) all = all.filter(x => (x.owner_id || "__team") === T.owner);
  const n = c => all.filter(x => x.channel === c).length; const shown = T.ch ? all.filter(x => x.channel === T.ch) : all;
  if (!shown.find(x => x.id === S.todaySel)) S.todaySel = shown[0]?.id;
  const hh = h => new Date(2000, 0, 1, h).toLocaleTimeString([], { hour: "numeric" });
  const owners = [...new Set(d.suggestions.map(x => x.owner_id || "__team"))];
  const groups = ["call", "linkedin", "email", "ops"].map(c => ({ c, items: shown.filter(x => x.channel === c) })).filter(g => g.items.length);
  const item = x => `<div class="sg ${x.id === S.todaySel ? "on" : ""}" data-id="${x.id}"><div class="r"><span class="t">${esc(x.title)}</span>${x.kind !== "digest" ? `<span class="${scCls(x.priority)}" title="Priority score">${x.priority}</span>` : ""}</div><div class="m">${agTag(x.agent)} ${esc(x.lead ? campOf(x.lead.campaign_id).name : "Team")}${x.owner_id ? " · " + esc(uname(x.owner_id)) : ""}</div></div>`;
  v.innerHTML = tbar("Suggestions prepared for each channel. Nothing changes until you approve it.", `<div class="runbar"><span>Last run ${ago(st.last_run)}${st.last_by ? " by " + esc(st.last_by) : ""} · next ${fdate(st.next_run)} (${st.hours.map(hh).join(", ")})</span>${can("agents:run") ? '<button class="btn primary sm" id="runNow">Run now</button>' : ""}</div>`) +
    `<div class="tiles">${TILES.map(([c, ic, l, col, bg]) => `<div class="tile ${T.ch === c ? "on" : ""}" data-ch="${c}"><span class="ic" style="background:${bg};color:${col}">${ic}</span><div><div class="v">${n(c)}</div><div class="l">${l}</div></div></div>`).join("")}</div>
    <div class="cc"><section class="card"><div class="ch"><h2>Waiting for approval <span class="muted">${shown.length}</span></h2>${S.me.role !== "caller" && owners.length > 1 ? `<select id="tow" aria-label="Filter by person" style="max-width:130px"><option value="">Everyone</option>${owners.map(o => `<option value="${o}" ${T.owner === o ? "selected" : ""}>${o === "__team" ? "Team items" : esc(uname(o))}</option>`).join("")}</select>` : ""}</div>
      <div class="scroll">${groups.length ? groups.map(g => { const open = T.open[g.c] !== false, lim = T.more[g.c] ? 99 : 5; return `<div class="sgg"><div class="sgg-h" data-g="${g.c}"><span>${open ? "▾" : "▸"} ${esc(CH[g.c][1])}</span><span>${g.items.length}</span></div>${open ? g.items.slice(0, lim).map(item).join("") + (g.items.length > lim ? `<button class="btn sm more" data-more="${g.c}">Show ${g.items.length - lim} more</button>` : "") : ""}</div>`; }).join("") : empty("Nothing waiting", can("agents:run") ? "Press Run now to look for suggestions." : "The next scheduled run will add suggestions.")}</div></section>
    <section id="ccm" class="scroll"></section>
    <div style="gap:16px;display:flex;flex-direction:column"><div class="card feed" style="flex:1;min-height:0;display:flex;flex-direction:column"><h2>Activity</h2><div class="scroll">${d.feed.length ? d.feed.map(e => `<div class="e">${agTag(e.agent)}<span>${esc(e.text)}</span><span class="muted">${ago(e.at)}</span></div>`).join("") : empty("No activity yet")}</div></div></div></div>`;
  $$(".tile", v).forEach(e => e.onclick = () => { T.ch = T.ch === e.dataset.ch ? "" : e.dataset.ch; renderSuggestions(v); });
  $$(".sg", v).forEach(e => e.onclick = () => { S.todaySel = e.dataset.id; S.todayEdit = false; renderSuggestions(v); });
  $$("[data-g]", v).forEach(e => e.onclick = () => { T.open[e.dataset.g] = T.open[e.dataset.g] === false; renderSuggestions(v); });
  $$("[data-more]", v).forEach(e => e.onclick = () => { T.more[e.dataset.more] = true; renderSuggestions(v); });
  if ($("#tow")) $("#tow").onchange = e => { T.owner = e.target.value; renderSuggestions(v); };
  if ($("#runNow")) $("#runNow").onclick = async e => { e.target.disabled = true; e.target.textContent = "Running…"; try { const r = await api("/today/run", { method: "POST" }); toast(`Run finished: ${Object.values(r.created).reduce((a, b) => a + b, 0)} new suggestions`); } catch (er) { toast(er.message, true); } renderSuggestions(v); };
  const x = shown.find(i => i.id === S.todaySel), m = $("#ccm"); if (!x) { m.innerHTML = `<div class="card">${empty("Pick a suggestion", "Details and the evidence behind it appear here.")}</div>`; return; }
  const l = x.lead, act = can("agents:act"), edit = !!S.todayEdit, isDraft = ["linkedin", "email"].includes(x.channel) && x.kind === "call_brief", cm = l ? campOf(l.campaign_id) : null;
  const brief = l ? `<div class="card"><div class="ch"><div><h2>${esc(l.full_name)}</h2><div class="muted">${esc(l.title)}${l.title && l.company ? " at " : ""}${esc(l.company)}</div></div><div>${pill(l.stage)}</div></div>
    <div class="bar" style="margin:8px 0">${chBadge(x.channel)} <span class="chb mixed" style="--x:0">${esc(cm.name)}</span></div>
    <dl class="kv">${dd("Phone", l.phone ? `<a href="tel:${esc(l.phone)}">${esc(l.phone)}</a>` : "")}${dd("Email", esc(l.email))}${dd("LinkedIn", l.linkedin_url ? `<a href="${esc(l.linkedin_url)}" target="_blank" rel="noopener noreferrer">Open profile</a>` : "")}${dd("Owner", esc(uname(l.owner_id)))}${dd("Touches", l.touches || 0)}${dd("Last touch", x.recent.length ? esc(x.recent[0].outcome) + " · " + fdate(x.recent[0].at) : "")}</dl>
    <h3 style="margin-top:12px">Why this is here</h3><ul style="margin:6px 0;padding-left:18px">${x.evidence.filter(e => !e.source).map(e => `<li>${esc(e.text)}</li>`).join("")}</ul>
    ${l.signal ? `<h3>Signal</h3><div class="sig"><b>${esc(l.signal)}</b>${l.signal_url && /^https?:/.test(l.signal_url) ? `<div class="m"><a href="${esc(l.signal_url)}" target="_blank" rel="noopener noreferrer">source</a></div>` : ""}</div>` : ""}
    <div class="bar" style="margin:10px 0 0"><button class="btn sm" id="ol">Open lead</button></div></div>` : "";
  const lbl = isDraft ? `${CH[x.channel][1]} draft` : "Proposed";
  m.innerHTML = x.kind === "digest" ? `<div class="card"><div class="ch"><h2>${agTag(x.agent)} ${esc(x.title)}</h2></div><p style="white-space:pre-wrap">${esc(x.body)}</p><div style="text-align:right"><button class="btn" id="dm">Mark as read</button></div></div>` :
    `<div class="prop" style="margin-top:0"><div class="ch"><b>${agTag(x.agent)} ${lbl}: ${esc(x.action?.title || x.title)}</b><span class="wait">waiting for your ok</span></div>
    ${edit && x.action?.type === "create_task" ? `<div class="form2" style="margin:8px 0"><label>Task title<input id="et" value="${esc(x.action.title)}"></label><label>Due<input type="datetime-local" id="ed" value="${isoLocal(new Date(x.action.due_at))}"></label></div>` : ""}
    ${x.action?.type === "stage" ? `<p>Move to <b>${esc(x.action.stage)}</b>.</p>` : ""}${x.action?.type === "assign" ? `<p>New owner: <b>${esc(uname(x.action.owner_id))}</b></p>` : ""}${x.action?.type === "enrich" ? '<p class="warnc">Uses provider credits. You confirm before it runs.</p>' : ""}${x.action?.type === "archive" ? '<p class="warnc">The lead is archived, not deleted.</p>' : ""}${isDraft ? '<p class="muted" style="margin:6px 0">The CRM never sends messages. Review, copy, and send it yourself, then log the activity.</p>' : ""}
    <textarea id="eb" ${edit ? "" : "readonly"} aria-label="Suggestion text">${esc(x.body)}</textarea>
    ${act ? `<div class="bar" style="margin:10px 0 0"><button class="btn primary" id="ap">Approve${x.action?.type === "create_task" ? " & create task" : ""}</button>${x.action?.type === "create_task" ? `<button class="btn" id="ed2">${edit ? "Cancel edit" : "Edit"}</button>` : ""}${isDraft || x.channel === "call" ? '<button class="btn" id="cp">Copy text</button>' : ""}<button class="btn" id="dm">Dismiss</button></div>` : '<p class="muted">Your role can view suggestions but not act on them.</p>'}</div>${brief}`;
  const on = (i, f) => $(i, m) && ($(i, m).onclick = f);
  on("#ol", () => openLead(l.id)); on("#ed2", () => { S.todayEdit = !S.todayEdit; renderSuggestions(v); });
  on("#cp", async () => { try { await navigator.clipboard.writeText($("#eb").value); toast("Copied"); } catch { toast("Copy failed. Select the text and copy it manually.", true); } });
  on("#dm", async () => { const r = x.kind === "digest" ? "" : await promptBox("Dismiss suggestion", "Reason (optional, helps tune future suggestions)", "Dismiss", false); if (r === null) return; try { await api(`/suggestions/${x.id}/dismiss`, { method: "POST", body: { reason: r } }); toast("Dismissed"); S.todayEdit = false; renderSuggestions(v); } catch (e) { toast(e.message, true); } });
  on("#ap", async () => {
    const what = { create_task: "create this follow-up task", assign: `assign the lead to ${uname(x.action?.owner_id)}`, stage: `move the lead to ${x.action?.stage}`, archive: "archive this duplicate", enrich: `enrich ${x.action?.lead_ids?.length} leads (may use credits)` }[x.action?.type] || "apply this";
    if (!await confirmBox("Approve suggestion", `This will ${what}. It is recorded in the audit log.`, "Approve")) return;
    const body = edit ? { title: $("#et")?.value, body: $("#eb").value, due_at: toISO($("#ed")?.value) } : {};
    try { await api(`/suggestions/${x.id}/approve`, { method: "POST", body }); toast("Approved and applied"); S.todayEdit = false; renderSuggestions(v); } catch (e) { toast(e.message, true); }
  });
};

// ---------------------------------------------------------------- OVERVIEW (executive dashboard)
const renderOverview = async v => {
  const d = await api("/analytics/overview?" + qs(RF)); const k = d.kpis, s = d.series, eng = d.engagement;
  const card = (label, val, chip, sp, col, sub) => `<div class="card kp"><span class="l">${label}</span><span class="v">${val}</span><span>${chip} <span class="muted" style="font-size:11px">${sub || ""}</span></span>${sp ? spark(sp, col) : ""}</div>`;
  const mini = (l, x, bad) => `<div class="mini" title="${esc(k[x]?.definition || "")}"><div class="l">${l}</div><div class="v ${bad && k[x].value ? "bad" : ""}">${k[x].value}</div></div>`;
  const chRows = d.channels, chMax = Math.max(1, ...chRows.map(r => r.touches)), chCol = { phone_call: "var(--ok)", linkedin: "var(--info)", email: "var(--warn)" };
  v.innerHTML =
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
  $$("[data-l]", v).forEach(e => e.onclick = () => openLead(e.dataset.l));
};

// ---------------------------------------------------------------- LEADS (monday-style board by campaign, flat table, stage kanban)
const LV = { q: "", stage: "", source: "", owner: "", status: "", campaign: "", channel: "", sort: "created_at", desc: true, cursors: [""], mode: "board" };
const COLS = [["full_name", "Lead"], ["company", "Company"], ["campaign_id", "Campaign", "table"], ["stage", "Stage"], ["owner_id", "Owner"], ["last_activity_at", "Last activity"], ["next_action_due", "Next action"]];
const hiddenCols = () => JSON.parse(localStorage.hiddenCols || "[]");
const savedViews = () => JSON.parse(localStorage.savedViews || "[]");
const gcol = () => JSON.parse(localStorage.gcollapsed || "{}");
const GB = {}; let SEL = new Set();
const lvQuery = x => qs({ q: LV.q, stage: LV.stage, source: LV.source, owner: LV.owner, status: LV.status, sort: LV.sort, desc: LV.desc, ...x });
PAGES.leads = async v => {
  const menu = [can("assign") ? '<button class="mi" id="addC">New campaign</button><button class="mi" id="syncC" title="Import your active Apollo sequences as campaigns, with their contacts as leads">Sync campaigns from Apollo</button>' : "", can("enrich") ? '<button class="mi" id="enrAll" title="Enrich every lead that still has missing data, using Apollo">Enrich missing data</button>' : "", can("export") ? '<button class="mi" id="expL">Export CSV</button>' : ""].join(""), nx = [LV.channel, LV.source, LV.status].filter(Boolean).length;
  v.innerHTML = head("Leads", "Every lead lives in a campaign. Expand a campaign to work its leads.", `${can("leads:write") ? '<button class="btn primary" id="addL">+ Add lead</button>' : ""}${menu ? `<details class="menu-d"><summary class="btn" aria-label="More actions">⋯</summary><div class="menu-p">${menu}</div></details>` : ""}`) +
    `<div class="bar"><input id="lq" placeholder="Search name, company, email, phone" value="${esc(LV.q)}" style="min-width:250px" aria-label="Search leads">${msHtml("lca", "campaigns", [...S.campaigns.map(c => [c.id, c.name]), ["none", "No campaign"]], LV.campaign)}<select id="lst" aria-label="Stage">${opts(S.stages, LV.stage, "All stages")}</select>
    ${S.me.role === "caller" ? "" : `<select id="low" aria-label="Owner">${opts([["unassigned", "Unassigned"], ...S.users.map(u => [u.id, u.name])], LV.owner, "All owners")}</select>`}
    <details class="menu-d"><summary class="btn">More filters${nx ? " · " + nx : ""}</summary><div class="menu-p form-p"><label>Channel<select id="lch">${opts([["call", "Calls"], ["linkedin", "LinkedIn"], ["email", "Email"], ["mixed", "Multi-channel"]], LV.channel, "All channels")}</select></label><label>Source<select id="lsr">${opts(S.sources, LV.source, "All sources")}</select></label><label>Status<select id="lsu">${opts([["overdue", "Overdue next action"], ["no_next_action", "No next action"], ["untouched", "Never contacted"], ["unassigned", "Unassigned"]], LV.status, "Any status")}</select></label></div></details>
    <span class="grow"></span><div class="seg" role="group" aria-label="View">${[["board", "Board"], ["table", "Table"], ["kanban", "Kanban"]].map(([m, l]) => `<button class="${LV.mode === m ? "on" : ""}" data-m="${m}">${l}</button>`).join("")}</div></div>
    <div id="bulk" class="bar hidden card" style="padding:8px 12px"></div><div id="ltab">${sk(8)}</div>`;
  const reload = () => { LV.cursors = [""]; loadLeads(); };
  const bind = (id, k) => $(id) && ($(id).onchange = e => { LV[k] = e.target.value; if (["#lch", "#lsr", "#lsu"].includes(id)) return PAGES.leads(v); reload(); });
  bind("#lst", "stage"); bind("#lsr", "source"); bind("#lsu", "status"); bind("#low", "owner"); bind("#lch", "channel"); msBind("lca", "campaigns", val => { LV.campaign = val; reload(); });
  let t; $("#lq").oninput = e => { clearTimeout(t); t = setTimeout(() => { LV.q = e.target.value; reload(); }, 250); };
  $$("[data-m]", v).forEach(b => b.onclick = () => { LV.mode = b.dataset.m; PAGES.leads(v); });
  const mi = (id, f) => $(id) && ($(id).onclick = e => { e.target.closest("details")?.removeAttribute("open"); f(e); });
  mi("#addL", () => leadForm(null, LV.campaign && !LV.campaign.includes(",") && LV.campaign !== "none" ? LV.campaign : null)); mi("#addC", () => campaignModal(() => PAGES.leads(v))); mi("#enrAll", enrichAll);
  mi("#syncC", async e => {
    const b = e.target; b.disabled = true; b.textContent = "Syncing from Apollo… (can take a minute)";
    try {
      const r = await api("/integrations/apollo/sync-campaigns", { method: "POST" }); const m = await api("/meta"); S.campaigns = m.campaigns;
      toast(r.note || `Apollo: ${r.active} active of ${r.apollo_total} sequences (${r.created} new, ${r.updated} updated, ${r.archived} archived). Leads: ${r.leads_created} added, ${r.leads_duplicate} already in CRM${r.truncated ? ". Stopped at 10,000 contacts, run again" : ""}.${r.emails ? (r.emails.error ? ` Emails not synced: ${r.emails.error}` : ` Emails: ${r.emails.sent} sent, ${r.emails.replies} replies, ${r.emails.bounces} bounces.`) : ""}`, !!r.note);
      PAGES.leads(v);
    } catch (er) { toast(er.message, true); b.disabled = false; b.textContent = "Sync campaigns from Apollo"; }
  });
  mi("#expL", () => download("/analytics/export/leads?" + qs({ stage: LV.stage, source: LV.source, owner: LV.owner, campaign: LV.campaign, channel: LV.channel }), "leads.csv"));
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
  const cell = (l, k) => ({ company: esc(l.company) + (l.has_company ? ' <span class="cdot" title="Company brief available">●</span>' : ""), full_name: `<b>${esc(l.full_name)}</b>${l.suppressed ? ' <span class="pill s8">DNC</span>' : ""}<div class="muted" style="font-size:11px">${esc(l.title)}</div>`, stage: `<span class="${stageCls(l.stage)}">${esc(l.stage)}</span>`, owner_id: ownerCell(l.owner_id), campaign_id: `<span style="color:${campOf(l.campaign_id).color}">●</span> ${esc(campOf(l.campaign_id).name)}`,
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
  const r = await api("/leads/board?" + lvQuery({ campaign: LV.campaign, channel: LV.channel, limit: 10 }));       // one request for every campaign group
  if (!$("#ltab")) return; Object.keys(GB).forEach(k => delete GB[k]); Object.assign(GB, r.groups);
  SEL = new Set(); renderBulk(); drawBoard(r.campaigns);
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
  b.innerHTML = `<b>${SEL.size} selected</b>${can("assign") ? `<select id="bo">${userOpts("", "Assign to…")}</select>` : ""}<select id="bc">${campOpts("", "Move to campaign…")}</select><select id="bs">${opts(S.stages, "", "Move to stage…")}</select><button class="btn sm danger" id="ba">Archive</button>${can("enrich") ? '<button class="btn sm" id="be">Enrich</button><button class="btn sm" id="bsc" title="Look for call recordings and transcripts in Apollo">Sync calls</button>' : ""}`;
  const run = async (action, value, reason) => { const r = await api("/leads/bulk", { method: "POST", body: { ids: [...SEL], action, value, reason } }); toast(`${r.updated} of ${r.requested} updated`); loadLeads(); };
  if ($("#bo")) $("#bo").onchange = e => e.target.value && run("assign", e.target.value);
  $("#bc").onchange = e => e.target.value && run("campaign", e.target.value);
  $("#bs").onchange = async e => { if (!e.target.value) return; let reason = ""; if (e.target.value === "Lost") { reason = await promptBox("Reason for Lost", "Why are these leads lost?"); if (!reason) return; } run("stage", e.target.value, reason); };
  $("#ba").onclick = async () => { if (await confirmBox("Archive leads", `Archive ${SEL.size} leads? They disappear from lists but history is kept.`, "Archive", true)) run("archive", ""); };
  if ($("#be")) $("#be").onclick = () => enrichConfirm([...SEL]);
  if ($("#bsc")) $("#bsc").onclick = () => callsSync([...SEL]);
}
async function moveStage(id, stage) {
  let reason = ""; if (stage === "Lost") { reason = await promptBox("Reason for Lost", "Why is this lead lost?"); if (!reason) return false; }
  try { await api(`/leads/${id}/stage`, { method: "POST", body: { stage, reason } }); toast("Stage updated"); return true; } catch (e) { toast(e.message, true); return false; }
}
async function enrichConfirm(ids, refresh) {
  if (!await confirmBox(refresh ? "Refresh from Apollo" : "Enrich with Apollo", refresh ? `Re-check ${ids.length} lead(s) with Apollo? Values Apollo now reports differently are updated (the old value is kept in the change history). Verified values are never changed. Apollo charges credits only for people it matches.` : `Enrich ${ids.length} lead(s)? Apollo only fills in what is missing. Existing and verified values are never overwritten, and credits are charged only for people it matches.`, refresh ? "Refresh" : "Start enrichment")) return;
  try { const j = await api("/enrichment/jobs", { method: "POST", body: { lead_ids: ids, confirm: true, refresh: !!refresh } }); jobProgress(j.id); } catch (e) { toast(e.message, true); }
}
async function enrichAll() {
  let pv; try { pv = await api("/enrichment/preview"); } catch (e) { return toast(e.message, true); }
  if (!pv.configured) { modal(`<h2>Apollo is pending configuration</h2><p>${esc(pv.pending_note)}</p><div class="acts"><button class="btn primary" id="cn">OK</button></div>`); $("#cn").onclick = closeModal; return; }
  if (pv.busy) return toast("An enrichment job is already running. Wait for it to finish.", true);
  if (!pv.qualifying) return toast("No leads qualify: they are already enriched, have all their data, or have nothing Apollo could search on.");
  const more = pv.remaining_after ? ` ${pv.remaining_after} more qualify and will need another run.` : "";
  if (!await confirmBox("Enrich all contacts", `${pv.qualifying} lead(s) qualify. This run processes ${pv.will_run} of them in ${pv.api_calls} Apollo call(s) of up to ${pv.batch_size}.${more} ${pv.credit_note}`, `Enrich ${pv.will_run} leads`)) return;
  try { const j = await api("/enrichment/jobs", { method: "POST", body: { all: true, confirm: true } }); jobProgress(j.id); } catch (e) { toast(e.message, true); }
}
const JSTAT = { pending: "Waiting", enriched: "Enriched", no_new_data: "Matched, nothing new", no_match: "No match", error: "Failed" };
async function jobProgress(id) {
  modal(`<h2>Enrichment</h2><div id="jp">${sk(3)}</div>`, { wide: true });
  const draw = j => {
    const c = s => j.items.filter(x => x.status === s).length, run = ["queued", "running"].includes(j.status), done = j.items.length - c("pending"), pct = Math.round(100 * done / Math.max(1, j.items.length));
    const order = { error: 0, no_match: 1, enriched: 2, no_new_data: 3 }, rows = j.items.filter(x => x.status !== "pending").sort((x, y) => order[x.status] - order[y.status]).slice(0, 300);
    $("#jp").innerHTML = `<p>${run ? (j.status === "queued" ? "Starting…" : "Working. You can close this window; the job keeps running.") : `Finished: ${esc(j.status)}.`}${j.error ? ` <span class="bad">${esc(j.error)}</span>` : ""}</p>
      <div class="jp-bar" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><i style="width:${pct}%"></i></div>
      <p class="muted">${done} of ${j.items.length} processed · ${c("enriched")} enriched · ${c("no_new_data")} nothing new · ${c("no_match")} no match · ${c("error")} failed · Apollo credits used: ${j.credits_used ?? 0}</p>
      ${rows.length ? `<div class="tbl-wrap" style="max-height:260px"><table class="tbl"><thead><tr><th>Lead</th><th>Result</th><th>Detail</th></tr></thead><tbody>${rows.map(x => `<tr><td>${esc(x.name)}</td><td>${esc(JSTAT[x.status] || x.status)}</td><td>${esc(x.error || x.note || (x.updated || []).join(", ") || "")}</td></tr>`).join("")}</tbody></table></div>` : ""}
      <div class="acts">${!run && (c("error") + c("no_match")) && can("enrich") ? '<button class="btn" id="jr">Retry failed / no match</button>' : ""}<button class="btn primary" id="jc">Close</button></div>`;
    $("#jc").onclick = closeModal;
    if ($("#jr")) $("#jr").onclick = async e => { e.target.disabled = true; try { const r = await api(`/enrichment/jobs/${id}/retry`, { method: "POST" }); jobProgress(r.id); } catch (er) { e.target.disabled = false; toast(er.message, true); } };
    return run;
  };
  let fails = 0;
  for (let i = 0; i < 1800; i++) {
    let j; try { j = await api("/enrichment/jobs/" + id); fails = 0; } catch (e) { if (!$("#jp")) return; if (++fails >= 5) { $("#jp").innerHTML = `${empty("Lost contact with the server", e.message)}<div class="acts"><button class="btn primary" id="jc">Close</button></div>`; $("#jc").onclick = closeModal; return; } await new Promise(r => setTimeout(r, 2000)); continue; }
    if (!$("#jp")) return;                                  // dialog closed: stop polling (the job itself keeps running on the server)
    if (!draw(j)) { if (S.openLead && !$("#drawer").classList.contains("hidden")) openLead(S.openLead, true); if (location.hash.startsWith("#/leads")) { try { loadLeads(); } catch { } } return; }
    await new Promise(r => setTimeout(r, 2000));
  }
}

// ---------------------------------------------------------------- lead form + profile
function leadForm(l, defCamp) {
  const m = modal(`<h2>${l ? "Edit lead" : "Add lead"}</h2><form id="lf" class="form2">
    <label>First name*<input name="first_name" required value="${esc(l?.first_name)}"></label><label>Last name<input name="last_name" value="${esc(l?.last_name)}"></label>
    <label>Job title<input name="title" value="${esc(l?.title)}"></label><label>Company<input name="company" value="${esc(l?.company)}"></label>
    <label>Email<input name="email" type="email" value="${esc(l?.email)}"></label><label>Phone<input name="phone" value="${esc(l?.phone)}"></label>
    <label>LinkedIn URL<input name="linkedin_url" value="${esc(l?.linkedin_url)}"></label><label>Website<input name="website" value="${esc(l?.website)}"></label>
    <label class="full">Signal (why now: hiring, funding, news, a referral...)<input name="signal" maxlength="240" value="${esc(l?.signal)}"></label><label class="full">Signal source URL (optional)<input name="signal_url" value="${esc(l?.signal_url)}"></label>
    <label>Location<input name="location" value="${esc(l?.location)}"></label><label>Country<input name="country" value="${esc(l?.country)}"></label><label>Campaign<select name="campaign_id">${campOpts(l ? l.campaign_id : defCamp, "No campaign")}</select></label>
    ${l ? "" : `<label>Source*<select name="source">${opts(S.sources, "Manual")}</select></label><label>Source details (URL, event, referrer)<input name="source_detail"></label>${can("assign") ? `<label class="full">Owner<select name="owner_id">${userOpts("", "Unassigned")}</select></label>` : ""}`}
    <div class="full"><span class="muted">Channels</span><div class="bar" style="margin:4px 0 0">${CH_OPTS.map(([v, lb]) => `<label class="chk"><input type="checkbox" name="channels" value="${v}" ${(l?.channels || []).includes(v) ? "checked" : ""}> ${lb}</label>`).join("")}<span class="muted">none ticked = follow the campaign</span></div></div>
    <p class="err full" id="fe"></p><div class="acts full"><button type="button" class="btn" id="cn">Cancel</button><button class="btn primary">${l ? "Save changes" : "Add lead"}</button></div></form>`);
  $("#cn").onclick = closeModal;
  $("#lf").onsubmit = async e => {
    e.preventDefault(); const d = Object.fromEntries(new FormData(e.target)); if (d.owner_id === "") d.owner_id = null; d.channels = new FormData(e.target).getAll("channels");
    try { const r = l ? await api("/leads/" + l.id, { method: "PATCH", body: d }) : await api("/leads", { method: "POST", body: d }); closeModal(); toast(l ? "Lead saved" : "Lead added"); route(); if (!l) openLead(r.id); else openLead(l.id, true); }
    catch (er) { $("#fe").textContent = er.message; }
  };
}
// Contact data: every field, where it came from, and what is missing. Enrichment is offered only for gaps, plus an explicit refresh.
function dataPane(l) {
  const F = [["email", "Email"], ["phone", "Phone"], ["linkedin_url", "LinkedIn"], ["title", "Job title"], ["company", "Company"], ["website", "Website"], ["location", "Location"]], prov = l.provenance || {}, ver = l.verified_fields || [];
  const origin = f => prov[f] ? `Enriched by ${esc(prov[f].provider)} · ${fdate(prov[f].at)}` : ver.includes(f) ? "Verified by a person" : l.source ? `From ${esc(l.source)} import` : "Added manually";
  const show = (f, v) => f === "email" ? `<a href="mailto:${esc(v)}">${esc(v)}</a>` : f === "phone" ? `<a href="tel:${esc(v)}">${esc(v)}</a>` : f === "linkedin_url" || f === "website" ? `<a href="${esc(/^https?:/.test(v) ? v : "http://" + v)}" target="_blank" rel="noopener noreferrer">${esc(v)}</a>` : esc(v);
  const gaps = [["email", "Email"], ["linkedin_url", "LinkedIn"], ["title", "Job title"]].filter(([f]) => !l[f]), hist = Object.entries(prov).filter(([, p]) => p.prev);
  const rows = F.map(([f, n]) => `<tr><td>${n}</td><td>${l[f] ? `<span class="ok">✓</span> ${show(f, l[f])}` : f === "phone" ? '<span class="muted">Missing. Enrichment does not fetch phone numbers (extra Apollo credits).</span>' : '<span class="warnc">Missing</span>'}</td><td class="muted">${l[f] ? origin(f) : ""}</td></tr>`).join("");
  return `<div class="ds ${gaps.length ? "gap" : "ok"}">${gaps.length ? `<b>Missing: ${gaps.map(g => g[1]).join(", ")}</b><span>Apollo may be able to fill these in.</span>` : "<b>✓ Contact data is complete</b><span>Email, LinkedIn and job title are all present. No enrichment needed.</span>"}</div>
    <div class="tbl-wrap"><table class="tbl dt"><thead><tr><th>Field</th><th>Value</th><th>Source</th></tr></thead><tbody>${rows}</tbody></table></div>
    ${can("enrich") && !l.suppressed ? `<div class="bar" style="margin-top:12px">${gaps.length ? '<button class="btn primary" id="enr">Fill missing data</button>' : ""}<button class="btn" id="enrR" title="Re-check Apollo and update values that changed. Verified values are kept. Apollo charges credits only for matched people.">Refresh from Apollo</button></div>` : ""}
    <p class="muted" style="font-size:12px">Last enriched: ${fdate(l.last_enriched_at) || "never"}${ver.length ? ` · Verified by a person (never overwritten): ${ver.map(esc).join(", ")}` : ""}</p>
    ${hist.length ? `<details class="co-d"><summary>Change history (${hist.length})</summary><div class="tbl-wrap"><table class="tbl"><thead><tr><th>Field</th><th>Previous value</th><th>Changed</th></tr></thead><tbody>${hist.map(([f, p]) => `<tr><td>${esc(f)}</td><td>${esc(p.prev)}</td><td>${fdate(p.at)}</td></tr>`).join("")}</tbody></table></div></details>` : ""}`;
}
const emailTags = x => x.source === "apollo" ? ` ${tag("Apollo")}${x.clicked ? tag("clicked") : x.opened ? tag("opened") : ""}` : "";
const dd = (k, v) => `<dt>${esc(k)}</dt><dd>${v || '<span class="muted">—</span>'}</dd>`;
// Calls tab: recordings (links into Apollo) and transcripts that Apollo returned for this lead. Nothing is invented: only what Apollo has.
function callsPane(l, trs) {
  const ap = apolloUrl(l), url = u => u && /^https?:/.test(u), dur = s => s || s === 0 ? `${Math.max(1, Math.round(s / 60))} min` : "";
  const head = `<div class="bar" style="margin:0 0 12px">${can("enrich") && l.apollo_contact_id ? '<button class="btn primary sm" id="syncCalls">Sync calls from Apollo</button>' : ""}${ap ? `<a class="btn sm" href="${esc(ap)}" target="_blank" rel="noopener noreferrer">Open in Apollo ↗</a>` : ""}</div>`;
  const note = l.apollo_contact_id ? "" : '<p class="muted">This lead has no Apollo contact ID (it did not come from a synced Apollo list or sequence), so its calls cannot be looked up.</p>';
  const sum = s => s && typeof s === "object" ? Object.entries(s).filter(([, v]) => v && (!Array.isArray(v) || v.length)).map(([k, v]) => `<div><b>${esc(k.replace(/_/g, " "))}:</b> ${esc(Array.isArray(v) ? v.join("; ") : typeof v === "object" ? JSON.stringify(v) : v)}</div>`).join("") : "";
  const items = trs.map(t => `<div class="card tr"><div class="ch"><div><b>${esc(t.topic || "Call")}</b><div class="muted">${fdate(t.started_at)}${t.duration_s != null ? " · " + dur(t.duration_s) : ""}${t.conversation_type ? " · " + esc(t.conversation_type.replace("_", " ")) : ""}${t.host ? " · host " + esc(t.host) : ""}</div>${(t.participants || []).length ? `<div class="muted">With ${t.participants.map(esc).join(", ")}</div>` : ""}</div>
      <div class="bar" style="margin:0">${url(t.audio_url) ? `<a class="btn sm" href="${esc(t.audio_url)}" target="_blank" rel="noopener noreferrer" title="Opens the recording stored in Apollo">Recording ↗</a>` : ""}${url(t.video_url) ? `<a class="btn sm" href="${esc(t.video_url)}" target="_blank" rel="noopener noreferrer">Video ↗</a>` : ""}</div></div>
    ${sum(t.summary) ? `<div class="trs">${sum(t.summary)}</div>` : ""}${t.text ? `<details class="co-d"><summary>Transcript</summary><pre class="trt">${esc(t.text)}</pre></details>` : '<p class="muted">Apollo has not produced a transcript for this call yet. Sync again later.</p>'}</div>`).join("");
  return head + note + (items || empty("No call recordings or transcripts attached yet", l.apollo_contact_id ? (can("enrich") ? "Press “Sync calls from Apollo” to look for this lead's calls." : "A manager can sync this lead's calls from Apollo.") : ""));
}
// Looking calls up is free; fetching a transcript can cost 1 Apollo credit when the call has AI insights, so the person confirms with real numbers first.
async function callsSync(ids, done) {
  let s; try { s = await api("/apollo/calls/scan", { method: "POST", body: { lead_ids: ids } }); } catch (e) { return toast(e.message, true); }
  const skipNote = s.no_contact_id ? ` ${s.no_contact_id} lead${s.no_contact_id > 1 ? "s have" : " has"} no Apollo contact ID and ${s.no_contact_id > 1 ? "were" : "was"} skipped.` : "";
  if (!s.new) return toast(s.conversations ? `Nothing new: ${s.already} call${s.already === 1 ? " is" : "s are"} already attached.${skipNote}` : `No calls found in Apollo for ${s.leads - s.no_contact_id} lead${s.leads - s.no_contact_id === 1 ? "" : "s"}.${skipNote}`);
  const go = Math.min(s.new, s.fetch_cap);
  if (!await confirmBox("Fetch call transcripts", `${s.new} new call${s.new === 1 ? "" : "s"} found in Apollo. Looking them up was free, but fetching each transcript can cost 1 Apollo credit if the call has AI insights (up to ${go} credit${go === 1 ? "" : "s"} now). ${s.new > s.fetch_cap ? `This run fetches the first ${s.fetch_cap}; run it again for the rest. ` : ""}Nothing is dialled and nothing changes in Apollo.${skipNote}`, `Fetch ${go}`)) return;
  try { const r = await api("/apollo/calls/sync", { method: "POST", body: { lead_ids: ids, confirm: true } }); toast(`${r.stored} transcript${r.stored === 1 ? "" : "s"} attached${r.pending ? `, ${r.pending} not ready in Apollo yet` : ""}${r.remaining ? `, ${r.remaining} left (run again)` : ""}${r.stopped ? `. Stopped: ${r.stopped}` : ""}.`, !!r.stopped); done && done(); }
  catch (e) { toast(e.message, true); }
}
async function openLead(id, keep) {
  S.openLead = id; const d = $("#drawer"); d.classList.remove("hidden"); if (!keep) d.innerHTML = `<div class="db">${sk(8)}</div>`;
  let l, tl; try { [l, tl] = await Promise.all([api("/leads/" + id), api(`/leads/${id}/timeline`)]); } catch (e) { d.innerHTML = `<div class="db">${empty("Lead unavailable", e.message)}</div>`; return; }
  const tab = ["overview", "timeline", "data", "calls"].includes(S.leadTab) ? S.leadTab : "overview"; const w = can("leads:write");
  const tli = tl.map(x => x.kind === "activity" ? `<li><b>${esc(x.type.replace("_", " "))}: ${esc(x.outcome)}</b> <span class="muted">by ${esc(x.user)} · ${fdate(x.at)}</span>${emailTags(x)}${x.notes ? `<div style="white-space:pre-wrap">${esc(x.notes)}</div>` : ""}</li>` : x.kind === "task" ? `<li class="t"><b>Task: ${esc(x.title)}</b> <span class="muted">${esc(x.status)} · due ${fdate(x.due_at)} · ${esc(uname(x.owner_id))}</span></li>` : `<li class="s"><b>Stage: ${esc(x.stage)}</b> <span class="muted">${fdate(x.at)} by ${esc(uname(x.by))}${x.reason ? " · " + esc(x.reason) : ""}</span></li>`).join("");
  const trs = tab === "calls" ? await api(`/leads/${id}/transcripts`).catch(() => []) : [];
  const prov = Object.entries(l.provenance || {}).map(([f, p]) => `<tr><td>${esc(f)}</td><td>${esc(p.provider)}</td><td>${fdate(p.at)}</td><td>${esc(p.prev) || "—"}</td></tr>`).join("");
  const panes = {
    overview: (l.company_brief ? coBrief(l.company_brief) : (l.company && can("companies:write") ? `<p class="muted">No company data for “${esc(l.company)}” yet. <a href="#/companies">Add it in Companies</a>.</p>` : "")) + `<dl class="kv">${dd("Signal", l.signal ? esc(l.signal) + (l.signal_url && /^https?:/.test(l.signal_url) ? ` <a href="${esc(l.signal_url)}" target="_blank" rel="noopener noreferrer">source</a>` : "") : "")}${dd("Phone", l.phone ? `<a href="tel:${esc(l.phone)}">${esc(l.phone)}</a>` : "")}${dd("Email", l.email ? `<a href="mailto:${esc(l.email)}">${esc(l.email)}</a>` : "")}${dd("LinkedIn", l.linkedin_url ? `<a href="${esc(l.linkedin_url)}" target="_blank" rel="noopener noreferrer">${esc(l.linkedin_url)}</a>` : "")}${dd("Website", esc(l.website))}${dd("Location", esc(l.location || l.country))}${dd("Source", esc(l.source) + (l.source_detail ? ` <span class="muted">(${esc(l.source_detail)})</span>` : ""))}${dd("Created", fday(l.created_at))}${dd("Owner", esc(uname(l.owner_id)))}${dd("Campaign", `<span class="chb ${campOf(l.campaign_id).channel}">${esc(campOf(l.campaign_id).name)}</span>`)}${dd("Channels", leadChannels(l).map(c => chBadge(c)).join(" ") || '<span class="muted">All / unspecified</span>')}${dd("Touches", `${l.touches || 0} (${l.attempts || 0} calls)`)}${dd("Tags", (l.tags || []).map(esc).join(", "))}</dl>${can("opps:read") ? '<h3 style="margin-top:14px">Opportunities</h3><div id="opp"></div>' : ""}`,
    timeline: tl.length ? `<ul class="tl">${tli}</ul>` : empty("No history yet", "Log an activity to start the timeline."),
    data: dataPane(l),
    calls: callsPane(l, trs)
  };
  d.innerHTML = `<div class="dh"><div><h2>${esc(l.full_name)} ${l.suppressed ? '<span class="pill s8">Do not contact</span>' : ""}</h2><div class="muted">${esc(l.title)}${l.title && l.company ? " at " : ""}${esc(l.company)}</div><div style="margin-top:6px">${pill(l.stage)} ${tag(l.source)}</div></div><button class="icon-btn" id="dx" aria-label="Close">✕</button></div>
  <div class="db"><div class="na"><div><b>Next action</b><div>${l.next_action ? `${esc(l.next_action)} · <span class="${l.next_action_due < new Date().toISOString() ? "bad" : ""}">${fdate(l.next_action_due)}</span>` : '<span class="warnc">None set. Add a follow-up.</span>'}</div></div>${can("tasks:write") ? '<button class="btn sm" id="addT">+ Task</button>' : ""}</div>
  <div class="bar">${can("activities:write") && !l.suppressed ? '<button class="btn primary sm" id="logA">Log activity</button>' : ""}${w ? `<select id="stg" aria-label="Change stage">${opts(S.stages, l.stage)}</select>` : ""}${can("assign") ? `<select id="own" aria-label="Owner">${userOpts(l.owner_id, "Unassigned")}</select>` : ""}${w ? '<button class="btn sm" id="edL">Edit</button>' : ""}${can("ai:use") ? '<button class="btn sm" id="aiL">✨ Ask AI</button>' : ""}${can("opps:write") ? '<button class="btn sm" id="mkO">+ Opportunity</button>' : ""}</div>
  <div class="tabs">${["overview", "timeline", "data", "calls"].map(t => `<button class="tab ${t === tab ? "on" : ""}" data-t="${t}">${t[0].toUpperCase() + t.slice(1)}</button>`).join("")}</div>${panes[tab]}</div>`;
  $("#dx").onclick = () => { d.classList.add("hidden"); S.openLead = null; }; bindCopy(d);
  $$(".tab[data-t]", d).forEach(b => b.onclick = () => { S.leadTab = b.dataset.t; openLead(id, true); });
  const on = (i, f) => $(i, d) && ($(i, d).onclick = f);
  on("#logA", () => logActivityModal(l, () => { openLead(id, true); if (location.hash.includes("leads")) loadLeads(); })); on("#edL", () => leadForm(l)); on("#enr", () => enrichConfirm([id])); on("#enrR", () => enrichConfirm([id], true));
  on("#syncCalls", () => callsSync([id], () => openLead(id, true))); on("#addT", () => taskModal(l)); on("#aiL", () => openAI(`Summarise this lead`, id)); on("#mkO", () => oppModal(l));
  if ($("#stg", d)) $("#stg", d).onchange = async e => { const ok = await moveStage(id, e.target.value); openLead(id, true); if (ok && $("#ltab")) loadLeads(); };
  if ($("#own", d)) $("#own", d).onchange = async e => { try { await api(`/leads/${id}/assign?` + qs({ owner_id: e.target.value }), { method: "POST" }); toast("Owner updated"); openLead(id, true); } catch (er) { toast(er.message, true); } };
  if (tab === "overview" && $("#opp")) { const o = (await api("/opportunities").catch(() => [])).filter(x => x.lead_id === id); $("#opp").innerHTML = o.length ? o.map(x => `<div class="card" style="margin-bottom:8px"><b>${esc(x.name)}</b> ${pill(x.stage)}<div>${money(x.value)}</div></div>`).join("") : empty("No opportunity yet", "Create one once the lead is qualified."); }
}
$("#drawer")?.addEventListener("keydown", e => { if (e.key === "Escape") $("#dx")?.click(); });

// ---------------------------------------------------------------- log activity (outcome != stage); outcomes depend on the channel
// "+ Task" in the lead drawer: a follow-up or callback for this lead. Callbacks show up first on the owner's My day.
function taskModal(l, done) {
  const due = new Date(Date.now() + 864e5); due.setHours(10, 0, 0, 0);
  modal(`<h2>Add a task · ${esc(l.full_name)}</h2><form id="tf" class="form2">
    <label class="full">What needs doing?*<input name="title" required maxlength="140" placeholder="e.g. Call back about pricing"></label>
    <label>Due*<input type="datetime-local" name="due_at" required value="${isoLocal(due)}"></label>
    <label>Type<select name="type">${opts([["follow_up", "Follow-up"], ["callback", "Callback"]], "follow_up")}</select></label>
    <label>Priority<select name="priority">${opts([["low", "Low"], ["medium", "Medium"], ["high", "High"]], "medium")}</select></label>
    ${can("assign") ? `<label>Owner<select name="owner_id">${userOpts(l.owner_id || S.me.id, "Me")}</select></label>` : ""}
    <label class="full">Notes<textarea name="notes" rows="2"></textarea></label>
    <p class="err full" id="fe"></p><div class="acts full"><button type="button" class="btn" id="cn">Cancel</button><button class="btn primary" id="sv">Add task</button></div></form>`);
  $("#cn").onclick = closeModal;
  $("#tf").onsubmit = async e => {
    e.preventDefault(); const d = Object.fromEntries(new FormData(e.target)), b = $("#sv"); b.disabled = true;
    try {
      await api("/tasks", { method: "POST", body: { lead_id: l.id, title: d.title.trim(), type: d.type, due_at: toISO(d.due_at), priority: d.priority, owner_id: d.owner_id || null, notes: d.notes || "" } });
      closeModal(); toast("Task added"); openLead(l.id, true); if (location.hash.startsWith("#/leads")) { try { loadLeads(); } catch { } } done && done();
    } catch (er) { b.disabled = false; $("#fe").textContent = er.message; }
  };
}
// "+ Opportunity" in the lead drawer: a deal for this lead.
function oppModal(l, done) {
  modal(`<h2>New opportunity · ${esc(l.full_name)}</h2><form id="of" class="form2">
    <label class="full">Deal name*<input name="name" required maxlength="120" value="${esc((l.company || l.full_name) + " deal")}"></label>
    <label>Value<input type="number" min="0" step="1" name="value" placeholder="0"></label>
    <label>Stage<select name="stage">${opts(S.oppStages, S.oppStages[0])}</select></label>
    <p class="err full" id="fe"></p><div class="acts full"><button type="button" class="btn" id="cn">Cancel</button><button class="btn primary" id="sv">Create opportunity</button></div></form>`);
  $("#cn").onclick = closeModal;
  $("#of").onsubmit = async e => {
    e.preventDefault(); const d = Object.fromEntries(new FormData(e.target)), b = $("#sv"); b.disabled = true;
    try { await api("/opportunities", { method: "POST", body: { lead_id: l.id, name: d.name.trim(), value: +d.value || 0, stage: d.stage } }); closeModal(); toast("Opportunity created"); openLead(l.id, true); done && done(); }
    catch (er) { b.disabled = false; $("#fe").textContent = er.message; }
  };
}
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
const FIELDS = [["", "Ignore column"], ["first_name", "First name"], ["last_name", "Last name"], ["email", "Email"], ["phone", "Phone"], ["company", "Company"], ["title", "Job title"], ["linkedin_url", "LinkedIn URL"], ["website", "Website"], ["location", "Location"], ["country", "Country"], ["signal", "Signal"], ["signal_url", "Signal URL"]];
PAGES.discovery = async v => {
  const hist = await api("/imports");
  v.innerHTML = tbar("Add people from Apollo or upload a list. Company data (signals, pitch) is imported separately under Companies.") + `<div class="grid g2">
  <div class="card"><h2>Connected sources</h2><p class="muted">Search Apollo, retrieve a saved Apollo list, add research leads, or bring in an official LinkedIn export.</p><div class="bar"><button class="btn primary" id="goA">Search Apollo</button><button class="btn" id="goLists">Select saved Apollo lists</button><button class="btn" id="goM">Add research / manual lead</button></div>
  <p class="muted"><b>LinkedIn / Sales Navigator:</b> only official exports or approved CRM integrations. Upload the export here and choose source “LinkedIn”. No scraping.</p></div>
  <div class="card"><h2>Upload a lead list</h2><p class="muted">CSV or XLSX. Map columns, validate, resolve duplicates, then import.</p><button class="btn primary" id="goB">Upload file</button></div></div>
  <div id="work" style="margin-top:16px"></div>
  <div class="card" style="margin-top:16px"><h2>Import history</h2>${hist.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>When</th><th>Source / file</th><th>By</th><th>Rows</th><th>Created</th><th>Updated</th><th>Skipped</th><th>Invalid</th><th>Status</th><th></th></tr></thead><tbody>${hist.map(b => `<tr><td>${fdate(b.at)}</td><td>${esc(b.file)}</td><td>${esc(b.by_name)}</td><td>${b.row_count}</td><td>${b.created}</td><td>${b.updated}</td><td>${b.skipped}</td><td>${b.invalid}</td><td>${esc(b.status.replace("_", " "))}</td><td>${b.status === "done" && b.created ? `<button class="btn sm" data-rb="${b.id}">Roll back</button>` : ""}</td></tr>`).join("")}</tbody></table></div>` : empty("No imports yet", "Your first import will show up here with row counts and a rollback option.")}</div>`;
  $("#goM").onclick = () => leadForm(); $("#goA").onclick = apolloUI; $("#goB").onclick = wizard;
  $("#goLists").onclick = async e => {
    const w = $("#work"); w.innerHTML = `<div class="card"><h2>Saved Apollo lists</h2>${sk(3)}</div>`;
    e.target.disabled = true;
    try { AS.lists = await api("/integrations/apollo/lists"); }
    catch (er) { w.innerHTML = `<div class="card">${empty(er.code === "apollo_not_configured" ? "Apollo is pending configuration" : "Could not load Apollo lists", er.message)}</div>`; e.target.disabled = false; return; }
    e.target.disabled = false;
    const draw = () => { w.innerHTML = `<div class="card"><div class="ch"><h2>Saved Apollo lists</h2><button class="btn sm" id="reloadApolloLists">Reload lists</button></div><p class="muted">Select saved people lists to retrieve their contacts into the CRM. Each selected list becomes a Calls campaign.</p>${asListsHtml()}</div>`;
      if ($("#alLoad")) $("#alLoad").remove();
      if ($("#alSync")) $("#alSync").textContent = "Retrieve selected lists";
      $("#reloadApolloLists").onclick = async b => { b.target.disabled = true; try { AS.lists = await api("/integrations/apollo/lists"); draw(); } catch (er) { toast(er.message, true); b.target.disabled = false; } };
      if ($("#alSync")) $("#alSync").onclick = async b => { const ids = $$('[data-l]', w).filter(c => c.checked).map(c => c.dataset.l); if (!ids.length) return toast("Select at least one list", true); b.target.disabled = true; b.target.textContent = "Retrieving lists…"; try { const r = await api("/integrations/apollo/lists/sync", { method: "POST", body: { label_ids: ids } }); toast(`${r.lists} list(s) retrieved: ${r.leads_created} leads added, ${r.leads_duplicate} already in the CRM${r.leads_duplicate && r.skipped_by ? " (" + Object.entries(r.skipped_by).map(([k, n]) => `${n} same ${k.replace("_", " ")}`).join(", ") + ")" : ""}${r.truncated ? ". Stopped early, retrieve again" : ""}.`); await refreshCampaigns(); AS.lists = await api("/integrations/apollo/lists").catch(() => AS.lists); draw(); } catch (er) { toast(er.message, true); b.target.disabled = false; b.target.textContent = "Retrieve selected lists"; } };
    }; draw();
  };
  $$("[data-rb]").forEach(b => b.onclick = async () => { if (!await confirmBox("Roll back import", "Remove every lead created by this import? Blocked if any of them already has activity.", "Roll back", true)) return; try { const r = await api(`/imports/${b.dataset.rb}/rollback`, { method: "POST" }); toast("Import rolled back" + (r.removed_campaigns && r.removed_campaigns.length ? ` · removed ${r.removed_campaigns.length} empty campaign${r.removed_campaigns.length > 1 ? "s" : ""}: ${r.removed_campaigns.join(", ")}` : "")); await refreshCampaigns(); PAGES.discovery(v); } catch (e) { toast(e.message, true); } });
};
function apolloUI() {
  const w = $("#work"); let res = [];
  w.innerHTML = `<div class="card"><h2>Apollo search</h2><form id="as" class="bar"><input name="title" placeholder="Job title"><input name="company" placeholder="Company or domain (comma separated)"><select name="seniority" aria-label="Seniority"><option value="">Any seniority</option>${["owner", "founder", "c_suite", "partner", "vp", "head", "director", "manager", "senior", "entry", "intern"].map(x => `<option value="${x}">${x.replace("_", " ")}</option>`).join("")}</select><input name="location" placeholder="Location"><input name="keywords" placeholder="Keywords"><button class="btn primary">Search</button></form>${targetHtml("apt")}<div id="ar"></div></div>`; bindTarget($("#apt"));
  let page = 1;
  const run = async () => {
    const f = Object.fromEntries(new FormData($("#as"))); delete f.__c; $("#ar").innerHTML = sk(5);
    try {
      const r = await api("/integrations/apollo/search", { method: "POST", body: { ...f, page } }); res = r.results;
      $("#ar").innerHTML = (r.note ? `<p class="warnc">${esc(r.note)}</p>` : "") + `<div class="tbl-wrap"><table class="tbl"><thead><tr><th></th><th>Name</th><th>Title</th><th>Company</th><th>Location</th><th>Email</th><th>Duplicate?</th></tr></thead><tbody>${res.map((x, i) => `<tr><td><input type="checkbox" class="ap" data-i="${i}" ${x.duplicate_of ? "" : "checked"} aria-label="Select ${esc(x.first_name)}"></td><td>${esc(x.first_name)} ${esc(x.last_name)}</td><td>${esc(x.title)}</td><td>${esc(x.company)}</td><td>${esc(x.location)}</td><td>${esc(x.email) || `<span class="muted">${x.has_email ? "available, enrich to reveal" : "needs enrichment"}</span>`}</td><td>${x.duplicate_of ? `<span class="warnc">Matches ${esc(x.duplicate_of)}</span>` : "New"}</td></tr>`).join("")}</tbody></table></div>
      <div class="pager"><span><button class="btn sm" id="ap-p" ${page < 2 ? "disabled" : ""}>Previous page</button> <button class="btn sm" id="ap-n" ${r.has_more ? "" : "disabled"}>Next page</button> <span class="muted">Page ${page}${r.total != null ? ` · ${Number(r.total).toLocaleString()} people match` : ""}</span></span><button class="btn primary" id="ap-i">Import selected</button></div>`;
      $("#ap-p").onclick = () => { page--; run(); }; $("#ap-n").onclick = () => { page++; run(); };
      $("#ap-i").onclick = async () => { const sel = $$(".ap").filter(c => c.checked).map(c => res[+c.dataset.i]); if (!sel.length) return toast("Select at least one result", true); try { const b = await api("/integrations/apollo/import", { method: "POST", body: { records: sel, ...readTarget($("#apt")) } }); toast(`Imported ${b.created}, skipped ${b.skipped} duplicates`); await refreshCampaigns(); route(); } catch (e) { toast(e.message, true); } };
    } catch (e) { $("#ar").innerHTML = e.code === "apollo_not_configured" ? empty("Apollo is pending configuration", e.message) : empty("Search failed", e.message); }
  };
  $("#as").onsubmit = e => { e.preventDefault(); page = 1; run(); };
}
function wizard() {
  const w = $("#work"); const st = { b: null, map: {}, policy: "skip", source: "File Upload", pv: null }; const names = ["Upload", "Map fields", "Preview & validate", "Duplicates", "Confirm"];
  const steps = n => `<div class="steps">${names.map((x, i) => `<span class="step ${i === n ? "on" : i < n ? "done" : ""}">${i + 1}. ${x}</span>`).join("")}</div>`;
  const s0 = () => { w.innerHTML = `<div class="card"><h2>Upload a lead list</h2>${steps(0)}${targetHtml("wt")}<div class="bar" style="margin-top:10px"><label style="min-width:220px">Source label<select id="fs">${opts(S.sources, "File Upload")}</select></label></div><label style="margin-top:10px">CSV or XLSX file (max 5 MB, 5,000 rows). Choose the channels and campaign above first.<input type="file" id="fi" accept=".csv,.xlsx"></label><p class="err" id="fe"></p></div>`; bindTarget($("#wt")); $("#fi").onchange = async e => { const f = e.target.files[0]; if (!f) return; if (!/\.(csv|xlsx)$/i.test(f.name)) { $("#fe").textContent = "Only .csv and .xlsx files are supported."; return; } st.source = $("#fs").value; try { st.target = readTarget($("#wt")); } catch (er) { $("#fe").textContent = er.message; e.target.value = ""; return; } const fd = new FormData(); fd.append("file", f); try { st.b = await api("/imports/upload", { method: "POST", form: fd }); st.map = { ...st.b.suggested_mapping }; s1(); } catch (er) { $("#fe").textContent = er.message; } }; };
  const s1 = () => { const b = st.b; w.innerHTML = `<div class="card"><h2>Map columns</h2>${steps(1)}<p class="muted">${b.row_count} rows found in ${esc(b.file)}. We guessed the mapping; check it.</p><div class="tbl-wrap"><table class="tbl"><thead><tr><th>Column in file</th><th>Sample</th><th>Maps to</th></tr></thead><tbody>${b.columns.map((c, i) => `<tr><td><b>${esc(c)}</b></td><td>${esc(b.sample[0]?.[c])}</td><td><select data-c="${i}">${opts(FIELDS, st.map[c] || "")}</select></td></tr>`).join("")}</tbody></table></div><div class="acts" style="margin-top:12px"><button class="btn" id="bk">Back</button><button class="btn primary" id="nx">Validate</button></div></div>`; $$("[data-c]").forEach(s => s.onchange = () => { st.map[b.columns[+s.dataset.c]] = s.value; }); $("#bk").onclick = s0; $("#nx").onclick = s2; };
  const body = () => ({ mapping: st.map, policy: st.policy, source: st.source, ...st.target, confirm_separate: !!st.cs });
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
    w.innerHTML = `<div class="card"><h2>Confirm import</h2>${steps(4)}<p>Source <b>${esc(st.source)}</b> · channels <b>${esc(st.target.channels.join(" + "))}</b> · policy <b>${esc(st.policy)}</b>. New leads: <b>${p.valid_new}</b>${st.policy === "separate" ? " + " + p.duplicate_count + " separate" : ""}. Invalid rows (<b>${p.error_count}</b>) are not imported and stay in the error report.</p><div class="acts"><button class="btn" id="bk">Back</button><button class="btn primary" id="go">Import leads</button></div></div>`; $("#bk").onclick = s3;
    $("#go").onclick = async e => { e.target.disabled = true; try { const b = await api(`/imports/${st.b.id}/confirm`, { method: "POST", body: body() }); toast(`Imported ${b.created} new · ${b.updated} updated · ${b.skipped} skipped`); await refreshCampaigns(); route(); } catch (er) { e.target.disabled = false; toast(er.message, true); } }; };
  s0();
}

// ---------------------------------------------------------------- ACTIVITIES & FOLLOW-UPS
let FT = "today";
PAGES.followups = async v => {
  const scope = can("assign") || S.me.role === "admin" ? (sessionStorage.fscope || (can("assign") ? "all" : "mine")) : "mine";
  const [open, done, acts] = await Promise.all([api(`/tasks?scope=${scope}&status=open`), api(`/tasks?scope=${scope}&status=done`), can("activities:read") ? api("/activities?limit=40") : []]);
  const n = new Date().toISOString(), td = n.slice(0, 10); const groups = { today: open.filter(t => t.due_at.slice(0, 10) === td), overdue: open.filter(t => t.due_at < n && t.due_at.slice(0, 10) !== td), upcoming: open.filter(t => t.due_at.slice(0, 10) > td), done: done.slice(-30).reverse(), history: [] };
  const row = t => `<tr><td><a href="#" data-l="${t.lead_id}"><b>${esc(t.lead_name)}</b></a><div class="muted">${esc(t.company)}</div></td><td>${esc(t.title)}</td><td class="${t.due_at < n && t.status === "open" ? "bad" : ""}">${fdate(t.due_at)}</td><td>${esc(uname(t.owner_id))}</td><td>${esc(t.priority)}</td><td>${t.status === "open" && can("tasks:write") ? `<button class="btn sm" data-a="complete" data-id="${t.id}">Done</button> <button class="btn sm" data-a="snooze" data-id="${t.id}">Snooze 1d</button> <button class="btn sm" data-a="reschedule" data-id="${t.id}">Reschedule</button> ${can("assign") ? `<button class="btn sm" data-a="reassign" data-id="${t.id}">Reassign</button>` : ""}` : ""}</td></tr>`;
  v.innerHTML = head("Follow-ups", "Tasks and callbacks, plus your activity history.", can("assign") ? `<select id="fs" aria-label="Scope">${opts([["mine", "My tasks"], ["all", "Whole team"]], scope)}</select>` : "") +
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
const tbl = (cols, rows) => rows.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr>${cols.map(c => `<th>${esc(c[1])}</th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr>${cols.map(c => `<td>${c[2] ? c[2](r) : esc(r[c[0]])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : empty("No data for these filters");
const nbar = (label, val, max, text, col) => `<div class="nb"><span class="n">${esc(label)}</span><span class="t"><i style="width:${Math.max(2, Math.round(100 * val / Math.max(1, max)))}%;--c:${col}"></i></span><span class="c">${esc(text)}</span></div>`;
const renderReport = async (b, key) => {
  const d = await api(`/analytics/${key === "scorecard" ? "story" : key}?` + qs(RF)), def = x => `<p class="muted" style="font-size:12px">Definition: ${esc(x)}</p>`;
  if (key === "scorecard") return story(b, d);
  if (key === "campaigns") b.innerHTML = `<div class="card"><h2>Which campaigns turn leads into conversations?</h2>${tbl([["name", "Campaign", r => `<span style="color:${r.color}">●</span> ${esc(r.name)} ${chBadge(r.channel)}`], ["leads", "Leads"], ["contacted_pct", "Contacted", r => `<span class="meter"><i style="width:${r.contacted_pct}%;background:var(--primary)"></i></span>${r.contacted_pct}%`], ["engaged_pct", "Engaged", r => `<span class="meter"><i style="width:${r.engaged_pct}%;background:var(--ok)"></i></span>${r.engaged_pct}%`], ["qualified", "Qualified"], ["won", "Won"], ["touches", "Touches (period)"]], d.rows)}${def(d.definition)}</div>`;
  if (key === "channels") b.innerHTML = `<div class="grid g3">${d.rows.map((r, i) => `<div class="card"><div class="eyebrow">${esc(r.label)}</div><div style="font-size:30px;font-weight:800">${r.touches}</div><div class="muted">touches · previous ${r.prev_touches}</div>${nbar("Engaged", r.engaged, Math.max(1, r.touches), `${r.engaged} · ${r.rate}%`, NEON[i])}<div class="muted">${r.unique} unique prospects</div></div>`).join("")}</div>${def(d.definition)}`;
  if (key === "funnel") b.innerHTML = `<div class="card"><h2>Where leads progress or drop off</h2>${d.stages.map((r, i) => nbar(r.stage, r.count, d.stages[0].count, `${r.count} · ${r.conversion}%`, NEON[i % 6])).join("")}${d.biggest_drop ? `<p><b>Biggest drop:</b> ${d.biggest_drop.drop} leads did not reach ${esc(d.biggest_drop.stage)}.</p>` : ""}<p class="muted">Lost ${d.lost} · Nurture ${d.nurture}</p>${def(d.definition)}</div>`;
  if (key === "sources") b.innerHTML = `<div class="card"><h2>Which sources add leads, and do they convert?</h2>${tbl([["source", "Source"], ["leads", "Leads"], ["contactable", "Contactable"], ["connected", "Engaged"], ["qualified", "Qualified"], ["connect_rate", "Engaged %"], ["qualify_rate", "Qualify %"]], d.rows)}${def(d.definition)}</div>`;
  if (key === "calls") b.innerHTML = `<div class="grid g3">${[["Attempts", d.attempts], ["Unique prospects", d.unique], ["Connection rate", d.connect_rate + "%"]].map(([l, x]) => `<div class="card"><div class="eyebrow">${l}</div><div style="font-size:30px;font-weight:800">${x}</div></div>`).join("")}</div><div class="card" style="margin-top:16px"><h2>Call outcomes</h2>${d.outcomes.length ? d.outcomes.map((o, i) => nbar(o.outcome, o.pct, 100, `${o.count} · ${o.pct}%`, NEON[i % 6])).join("") : empty("No calls logged in this period")}${def(d.definition)}</div>`;
  if (key === "team") b.innerHTML = `<div class="card"><h2>Activity and outcomes per salesperson</h2>${tbl([["user", "Name"], ["role", "Role"], ["owned_leads", "Leads owned"], ["touches", "Touches"], ["engaged", "Engaged"], ["attempts", "Calls"], ["unique", "Unique contacted"], ["open_tasks", "Open tasks"], ["overdue", "Overdue", r => `<span class="${r.overdue ? "bad" : ""}">${r.overdue}</span>`]], d.rows)}${def(d.definition)}</div>`;
  if (key === "health") { const c = d.counts; const sec = (t, k) => `<div class="card"><h2>${t} (${c[k]})</h2>${tbl([["name", "Lead", r => `<a href="#" data-l="${r.id}">${esc(r.name)}</a>`], ["company", "Company"], ["stage", "Stage"], ["owner_id", "Owner", r => esc(uname(r.owner_id))], ["last_activity_at", "Last activity", r => fdate(r.last_activity_at)]], d[k].slice(0, 10))}</div>`; b.innerHTML = `<div class="grid g2">${sec("Stale", "stale")}${sec("Never contacted", "untouched")}${sec("Overdue next action", "overdue")}${sec("No next action", "no_next_action")}</div>${def(d.definition)}`; $$("[data-l]", b).forEach(a => a.onclick = e => { e.preventDefault(); openLead(a.dataset.l); }); }
  if (key === "enrichment") b.innerHTML = `<div class="grid g4">${[["Jobs", d.jobs], ["Leads processed", d.items], ["Enriched", d.succeeded], ["No match", d.no_match], ["Errors", d.errors], ["Phones found", d.found_phone], ["Emails found", d.found_email], ["Credit usage", d.credit_usage ?? "n/a"]].map(([l, x]) => `<div class="card"><div class="eyebrow">${l}</div><div style="font-size:26px;font-weight:800">${x}</div></div>`).join("")}</div>${def(d.definition)}`;
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

// ---------------------------------------------------------------- COMPANIES (company-level lists, monday-style, with the cues callers see on every matching lead)
const CO = { q: "", list: "", lists: [] }; const COG = {}; let coSeq = 0;
const stCls = s => /^a/i.test(s) ? "s7" : /^b/i.test(s) ? "s2" : "s1";
const coRow = c => `<tr class="click" data-id="${esc(c.id)}"><td><b>${esc(c.name)}</b><div class="muted" style="font-size:11px">${esc(c.website)}</div></td><td>${esc(c.industry)}</td><td>${c.employees ? Number(c.employees).toLocaleString() : ""}</td><td>${esc(c.location)}</td><td>${c.priority ? `<span class="${scCls(+c.priority || 0)}">${esc(c.priority)}</span>` : ""}</td><td>${c.status ? `<span class="pill ${stCls(c.status)}">${esc(c.status)}</span>` : ""}</td><td class="sigc" title="${esc(c.signal)}">${esc(c.signal)}</td><td>${c.leads || '<span class="muted">0</span>'}</td></tr>`;
PAGES.companies = async v => {
  const w = can("companies:write");
  v.innerHTML = head("Companies", "One list per company file. Open any lead and its company's signal, hook and pitch show up for the caller automatically, matched by company name.", w ? '<button class="btn" id="coNewL">+ Company list</button><button class="btn" id="coAp">Sync from Apollo</button><button class="btn primary" id="coImp">Import CSV</button>' : "") +
    `<div class="bar"><input id="coq" placeholder="Search companies" value="${esc(CO.q)}" style="min-width:240px" aria-label="Search companies"><span id="colw"></span></div><div id="cob">${sk(6)}</div>`;
  if ($("#coImp")) $("#coImp").onclick = () => companyImport(CO.list.includes(",") ? "" : CO.list);
  if ($("#coAp")) $("#coAp").onclick = companyApollo;
  if ($("#coNewL")) $("#coNewL").onclick = async () => { const n = await promptBox("New company list", "List name", "Create"); if (!n) return; try { await api("/company-lists", { method: "POST", body: { name: n } }); toast("List created"); loadCo(); } catch (e) { toast(e.message, true); } };
  let t; $("#coq").oninput = e => { clearTimeout(t); t = setTimeout(() => { CO.q = e.target.value; loadCo(); }, 250); };
  await loadCo();
};
async function loadCo() {
  const box = $("#cob"); if (!box) return; const my = ++coSeq;
  const r = await api("/companies/board?" + qs({ q: CO.q, list: CO.list })); if (my !== coSeq || !$("#cob")) return;
  CO.lists = r.all_lists; const w = $("#colw");
  const sig = r.all_lists.map(x => x.id + x.name).join("|");
  if (w && w.dataset.sig !== sig) { w.dataset.sig = sig; CO.list = CO.list.split(",").filter(i => r.all_lists.some(x => x.id === i)).join(","); w.innerHTML = msHtml("col", "company lists", r.all_lists.map(x => [x.id, x.name]), CO.list); msBind("col", "company lists", val => { CO.list = val; loadCo(); }); }
  r.lists.forEach(g => COG[g.id] = g); drawCo(r.lists);
}
function drawCo(lists) {
  const box = $("#cob"), w = can("companies:write"), col = gcol();
  if (!lists.length) { box.innerHTML = `<div class="card">${empty(CO.q ? "No companies match" : "No company lists yet", CO.q ? "Try a different search." : "Import your Apollo accounts export. Every column after the standard ones becomes a call cue.")}${!CO.q && w ? '<div style="text-align:center"><button class="btn primary" id="coImp2">Import CSV</button></div>' : ""}</div>`; if ($("#coImp2")) $("#coImp2").onclick = () => companyImport(""); return; }
  box.innerHTML = lists.map(g => { const s = COG[g.id];
    return `<div class="grp ${col["co" + g.id] ? "collapsed" : ""}" data-g="${esc(g.id)}" style="--gc:${esc(g.color)}"><div class="grp-h" data-t="${esc(g.id)}"><span class="chev">▾</span><h3>${esc(g.name)}</h3>${g.apollo ? '<span class="tag">Apollo</span>' : ""}<span class="n">${g.total} ${g.total === 1 ? "company" : "companies"} · ${g.leads} lead${g.leads === 1 ? "" : "s"} matched${g.fields.length ? " · " + g.fields.length + " cue fields" : ""}</span>${w ? `<span style="margin-left:auto;display:flex;gap:6px">${g.apollo ? `<button class="btn sm" data-resync="${esc(g.id)}">Re-sync</button>` : `<button class="btn sm" data-imp="${esc(g.id)}">Import into list</button>`}<button class="btn sm" data-arch="${esc(g.id)}">Archive</button></span>` : ""}</div>
    <div class="grp-body">${s.items.length ? `<table class="tbl"><thead><tr><th>Company</th><th>Industry</th><th>Employees</th><th>Location</th><th>Priority</th><th>Lead status</th><th>Signal</th><th>Leads</th></tr></thead><tbody>${s.items.map(coRow).join("")}</tbody></table>` : `<div class="empty">No companies in this list yet.</div>`}${s.next_cursor ? `<div class="grp-add" data-more="${esc(g.id)}">Load more (${s.total - s.items.length} remaining)</div>` : ""}</div></div>`; }).join("");
  $$("[data-t]", box).forEach(h => h.onclick = e => { if (e.target.closest("button")) return; const c = gcol(), k = "co" + h.dataset.t; c[k] = !c[k]; localStorage.gcollapsed = JSON.stringify(c); h.parentElement.classList.toggle("collapsed", c[k]); });
  $$("tbody tr", box).forEach(tr => tr.onclick = () => openCompany(tr.dataset.id));
  $$("[data-imp]", box).forEach(b => b.onclick = () => companyImport(b.dataset.imp));
  $$("[data-resync]", box).forEach(b => b.onclick = async () => { b.disabled = true; b.textContent = "Syncing…"; try { const r = await api("/companies/apollo-sync", { method: "POST", body: { label_ids: null } }); toast(`Apollo: ${r.created} new, ${r.updated} updated across ${r.lists} list${r.lists === 1 ? "" : "s"}${r.truncated ? ". Stopped at the page limit, run again" : ""}`); loadCo(); } catch (e) { toast(e.message, true); b.disabled = false; b.textContent = "Re-sync"; } });
  $$("[data-arch]", box).forEach(b => b.onclick = async () => { if (!await confirmBox("Archive company list", "Its companies stop matching leads and the list is hidden. Nothing is deleted from the database.", "Archive", true)) return; try { await api("/company-lists/" + b.dataset.arch, { method: "PATCH", body: { archived: true } }); toast("List archived"); loadCo(); } catch (e) { toast(e.message, true); } });
  $$("[data-more]", box).forEach(b => b.onclick = async () => { const id = b.dataset.more, s = COG[id]; b.textContent = "Loading…"; try { const r = await api("/companies?" + qs({ list: id, q: CO.q, cursor: s.next_cursor })); COG[id] = { ...s, items: [...s.items, ...r.items], next_cursor: r.next_cursor }; drawCo(lists); } catch (e) { toast(e.message, true); } });
}
async function companyApollo() {
  modal(`<h2>Sync company lists from Apollo</h2><p class="muted" style="margin:0">Pick the Apollo account lists to bring in. Apollo's custom fields (signal, hook, pitch…) become call cues. Syncing again updates companies in place.</p><div id="apL">${sk(3)}</div><p class="err" id="apE"></p><div class="acts"><button class="btn" id="cn">Close</button><button class="btn primary" id="apGo" disabled>Sync selected</button></div>`, { wide: true });
  $("#cn").onclick = closeModal;
  try {
    const ls = await api("/companies/apollo-lists");
    $("#apL").innerHTML = ls.length ? ls.map(l => `<label class="wk" style="cursor:pointer"><span><input type="checkbox" data-ap="${esc(l.id)}" ${l.synced ? "checked" : ""}> <b>${esc(l.name)}</b></span><span class="muted">${l.count} companies${l.synced ? " · synced" : ""}</span></label>`).join("") : empty("No company lists in Apollo", "Create an account list in Apollo and add companies to it.");
    const upd = () => { $("#apGo").disabled = !$$("[data-ap]:checked").length; }; $$("[data-ap]").forEach(c => c.onchange = upd); upd();
  } catch (e) { $("#apL").innerHTML = ""; $("#apE").textContent = e.message; }
  $("#apGo").onclick = async e => {
    e.target.disabled = true; e.target.textContent = "Syncing… (can take a minute)";
    try { const r = await api("/companies/apollo-sync", { method: "POST", body: { label_ids: $$("[data-ap]:checked").map(c => c.dataset.ap) } }); closeModal(); toast(`Apollo: ${r.created} new, ${r.updated} updated, ${r.cue_fields} call cue fields${r.truncated ? ". Stopped at the page limit, run again" : ""}`); loadCo(); }
    catch (er) { $("#apE").textContent = er.message; e.target.disabled = false; e.target.textContent = "Sync selected"; }
  };
}
function companyImport(defList) {
  modal(`<h2>Import companies</h2><p class="muted" style="margin:0">Upload an Apollo accounts export (CSV or XLSX). Standard columns fill the company profile. Every other column, such as Signal, Hook or Pitch script, becomes a call cue that callers see automatically.</p>
    <div class="form2"><label class="full">Company list<select id="ciL">${opts([["__new", "New list…"], ...CO.lists.map(x => [x.id, x.name])], defList || "__new")}</select></label><label class="full" id="ciNw">New list name<input id="ciN" maxlength="80" placeholder="Defaults to the file name"></label><label class="full">File (max 5,000 companies)<input type="file" id="ciF" accept=".csv,.xlsx"></label></div>
    <div id="ciP"></div><p class="err" id="ciE"></p><div class="acts"><button class="btn" id="cn">Cancel</button><button class="btn primary" id="ciGo" disabled>Import</button></div>`, { wide: true });
  let file = null; const post = prev => { const fd = new FormData(); fd.append("file", file); fd.append("list_id", $("#ciL").value); fd.append("new_list_name", $("#ciN").value || ""); fd.append("preview", prev ? "true" : "false"); return api("/companies/import", { method: "POST", form: fd }); };
  const sync = () => $("#ciNw").classList.toggle("hidden", $("#ciL").value !== "__new");
  const preview = async () => {
    if (!file) return; $("#ciE").textContent = ""; $("#ciGo").disabled = true; $("#ciP").innerHTML = sk(2);
    try { const p = await post(true);
      $("#ciP").innerHTML = `<div class="card"><b>${p.rows} companies</b> into “${esc(p.list)}”: ${p.create} new, ${p.update} updated.<div style="margin-top:6px"><span class="muted">${p.fields.length} call cues found:</span> ${p.fields.length ? p.fields.map(f => `<span class="tag" style="margin-right:8px">${esc(f)}</span>`).join("") : '<span class="muted">none, only profile columns</span>'}</div><div style="margin-top:6px" class="${p.leads_matched ? "ok" : "muted"}">${p.leads_matched} of these companies already match leads in your CRM.</div></div>`; $("#ciGo").disabled = false;
    } catch (e) { $("#ciP").innerHTML = ""; $("#ciE").textContent = e.message; }
  };
  $("#cn").onclick = closeModal; sync(); $("#ciL").onchange = () => { sync(); preview(); };
  $("#ciF").onchange = e => { file = e.target.files[0]; if (file && !/\.(csv|xlsx)$/i.test(file.name)) { $("#ciE").textContent = "Only .csv and .xlsx files are supported."; file = null; return; } preview(); };
  $("#ciGo").onclick = async e => { e.target.disabled = true; e.target.textContent = "Importing…"; try { const r = await post(false); closeModal(); toast(`${r.created} added, ${r.updated} updated · ${r.fields.length} call cues · ${r.leads_matched} companies matched to leads`); CO.list = ""; const w = $("#colw"); if (w) w.innerHTML = ""; if ($("#cob")) loadCo(); } catch (er) { $("#ciE").textContent = er.message; e.target.disabled = false; e.target.textContent = "Import"; } };
}
async function openCompany(id, edit) {
  const d = $("#drawer"); d.classList.remove("hidden"); if (!edit) d.innerHTML = `<div class="db">${sk(8)}</div>`;
  let r; try { r = await api("/companies/" + id); } catch (e) { d.innerHTML = `<div class="db">${empty("Company unavailable", e.message)}</div>`; return; }
  const b = r.brief, i = b.info, w = can("companies:write");
  const link = (u, t) => u ? `<a href="${esc(/^https?:/.test(u) ? u : "http://" + u)}" target="_blank" rel="noopener noreferrer">${esc(t || u)}</a>` : "";
  const profile = `<dl class="kv">${dd("Website", link(i.website))}${dd("LinkedIn", link(i.linkedin_url, "Company page"))}${dd("Phone", i.phone ? `<a href="tel:${esc(i.phone)}">${esc(i.phone)}</a>` : "")}${dd("Location", esc(i.location))}${dd("Employees", i.employees ? Number(i.employees).toLocaleString() : "")}${dd("Revenue", esc(i.annual_revenue))}${dd("Funding", esc(i.total_funding))}${dd("Founded", esc(i.founded_year))}${dd("About", esc(i.description))}${dd("Keywords", esc(i.keywords))}${dd("Technologies", esc(i.technologies))}</dl>`;
  const people = r.leads.length ? r.leads.map(l => `<div class="wk" style="cursor:pointer" data-l="${esc(l.id)}"><span><b>${esc(l.full_name)}</b><div class="muted" style="font-size:11px">${esc(l.title)}</div></span><span>${pill(l.stage)} <span class="muted" style="font-size:12px">${esc(uname(l.owner_id))}</span></span></div>`).join("") : empty("No leads at this company yet", "When a lead's company name matches, it shows up here.");
  const body = edit ? `<p class="muted">Edit the cues callers see. Add a field for anything new, for example “Gatekeeper”.</p><form id="cef" class="form2">${r.cues.map((x, n) => `<label class="full">${esc(x.k)}<textarea data-cue="${esc(x.k)}" rows="${(x.v || "").length > 200 ? 5 : 2}">${esc(x.v)}</textarea></label>`).join("")}<label>New field name<input id="cnk" maxlength="80" placeholder="e.g. Gatekeeper"></label><label>Value<input id="cnv"></label><div class="acts full"><button type="button" class="btn" id="cec">Cancel</button><button class="btn primary">Save</button></div></form>`
    : `${coBrief(b, { bare: true })}<h3 style="margin-top:14px">Profile</h3>${profile}<h3 style="margin-top:14px">Leads at this company (${r.leads.length})</h3>${people}`;
  d.innerHTML = `<div class="dh"><div><h2>${esc(b.name)}</h2><div class="muted">${esc(b.list)}${i.industry ? " · " + esc(i.industry) : ""}</div></div><div class="bar" style="margin:0">${w && !edit ? '<button class="btn sm" id="ced">Edit cues</button><button class="btn sm danger" id="cedel">Delete</button>' : ""}<button class="icon-btn" id="dx" aria-label="Close">✕</button></div></div><div class="db">${body}</div>`;
  $("#dx").onclick = () => d.classList.add("hidden"); bindCopy(d);
  $$("[data-l]", d).forEach(e => e.onclick = () => openLead(e.dataset.l));
  if ($("#ced")) $("#ced").onclick = () => openCompany(id, true);
  if ($("#cedel")) $("#cedel").onclick = async () => { if (!await confirmBox("Delete company", `Delete ${b.name}? Its call cues disappear from matching leads. Re-import the file to bring them back.`, "Delete", true)) return; try { await api("/companies/" + id, { method: "DELETE" }); d.classList.add("hidden"); toast("Company deleted"); if ($("#cob")) loadCo(); } catch (e) { toast(e.message, true); } };
  if ($("#cec")) $("#cec").onclick = () => openCompany(id);
  if ($("#cef")) $("#cef").onsubmit = async e => {
    e.preventDefault(); const cues = {}; $$("[data-cue]", d).forEach(t => { if (t.value !== (r.cues.find(x => x.k === t.dataset.cue)?.v || "")) cues[t.dataset.cue] = t.value; }); const nk = $("#cnk").value.trim(); if (nk) cues[nk] = $("#cnv").value;
    try { await api("/companies/" + id, { method: "PATCH", body: { cues } }); toast("Saved"); openCompany(id); if ($("#cob")) loadCo(); } catch (er) { toast(er.message, true); }
  };
}

const SUGG = ["How many leads were added this week and from which sources?", "How many prospects were called, connected with and qualified?", "Which leads are due for a callback today?", "Which salesperson has the largest overdue queue?", "Which source has the strongest connection rate?", "Where is the biggest pipeline drop-off?", "What changed compared with the previous period?", "How many enrichment requests succeeded or failed?"];
const CHAT = [];
function mountChat(el, ctxLead) {
  const draw = () => { $(".msgs", el).innerHTML = CHAT.length ? CHAT.map(m => m.u ? `<div class="msg u">${esc(m.u)}</div>` : `<div class="msg a">${esc(m.a.answer)}${m.a.records?.length ? `<ul>${m.a.records.map(r => `<li><a href="#" data-l="${esc(r.id)}">${esc(r.name)}</a>${r.detail ? " · " + esc(r.detail) : ""}</li>`).join("")}</ul>` : ""}${m.a.ai_phrased ? '<div class="meta"><b>AI-written</b> from CRM facts. Review before using.</div>' : ""}${m.a.proposed_action ? `<div class="na"><span>${esc(m.a.proposed_action.label)}</span><button class="btn sm primary" data-act="${m.a.proposed_action.id}">Confirm</button></div>` : ""}${m.a.period || m.a.definition ? `<div class="meta"><b>${m.a.kind === "fact" ? "Fact from CRM data" : ""}</b> ${m.a.period ? "· Period: " + esc(m.a.period) : ""}${m.a.definition ? "<br>Definition: " + esc(m.a.definition) : ""}</div>` : ""}</div>`).join("") : `<div class="muted">Ask about your CRM data. I only read what your role can see, and I never change records without your confirmation.</div><div class="chips">${(ctxLead ? LEAD_SUGG : SUGG).map(s => `<span class="chip">${esc(s)}</span>`).join("")}</div>`;
    const m = $(".msgs", el); m.scrollTop = m.scrollHeight;
    $$(".chip", el).forEach(c => c.onclick = () => send(c.textContent)); $$("[data-l]", el).forEach(a => a.onclick = e => { e.preventDefault(); openLead(a.dataset.l); });
    $$("[data-act]", el).forEach(b => b.onclick = async () => { if (!await confirmBox("Confirm AI-suggested action", b.previousElementSibling.textContent + ". This will be recorded in the audit log.", "Confirm")) return; try { const r = await api(`/ai/actions/${b.dataset.act}/confirm`, { method: "POST" }); toast(`${r.created} tasks created`); b.disabled = true; } catch (e) { toast(e.message, true); } });
  };
  const send = async t => { t = (t || "").trim(); if (!t) return; CHAT.push({ u: t }); draw(); $("input", el).value = ""; try { CHAT.push({ a: await api("/ai/chat", { method: "POST", body: { message: t, lead_id: ctxLead || null } }) }); } catch (e) { CHAT.push({ a: { answer: e.message, kind: "error" } }); } draw(); };
  el.innerHTML = `<div class="chat">${S.ai && !S.ai.configured ? `<p class="ai-pending">${esc(S.ai.note)}</p>` : ""}<div class="msgs" aria-live="polite"></div><form class="bar" style="margin:0"><input class="grow" placeholder="Ask a question about your CRM" aria-label="Question"><button class="btn primary">Ask</button></form></div>`;
  $("form", el).onsubmit = e => { e.preventDefault(); send($("input", el).value); }; draw(); return send;
}
const LEAD_SUGG = ["Summarise this lead", "Summarise the call transcript", "List objections and next steps", "Draft a follow-up email"];
function openAI(msg, leadId) { const p = $("#aiPanel"); p.classList.remove("hidden"); p.innerHTML = `<div class="dh"><h2>AI assistant</h2><button class="icon-btn" id="ax" aria-label="Close">✕</button></div><div class="db" id="ap" style="display:flex;flex-direction:column"></div>`; $("#ax").onclick = () => p.classList.add("hidden"); const send = mountChat($("#ap"), leadId); if (msg) send(msg); }

// ---------------------------------------------------------------- ASSIGN LEADS (rank unassigned leads by fit, hand batches to callers)
const AS = { f: { campaign: "", channel: "", source: "", batch: "", min_fit: 0, signal: false, phone: false }, n: {}, mode: "round_robin", lists: null };
const asQ = () => qs({ ...AS.f, signal: AS.f.signal ? "true" : "", phone: AS.f.phone ? "true" : "", min_fit: AS.f.min_fit || "" });
const asListsHtml = () => !AS.lists ? '<p class="muted">Press "Show my Apollo lists" to see the lists in your Apollo account.</p>' : !AS.lists.length ? empty("No people lists found in Apollo", "Upload your outreach list in Apollo first.") :
  `<div class="tbl-wrap" style="max-height:240px"><table class="tbl"><thead><tr><th></th><th>List</th><th>Contacts (Apollo's count)</th><th></th></tr></thead><tbody>${AS.lists.map(l => `<tr><td><input type="checkbox" data-l="${esc(l.id)}" ${l.synced ? "checked" : ""} aria-label="Sync ${esc(l.name)}"></td><td><b>${esc(l.name)}</b></td><td>${l.count}</td><td>${l.synced ? tag("synced") : ""}</td></tr>`).join("")}</tbody></table></div><div class="acts" style="margin-top:10px"><button class="btn primary" id="alSync">Sync ticked lists as call lists</button></div>`;
PAGES.assign = async v => {
  const [pool, people] = await Promise.all([api("/assign/pool?" + asQ() + "&limit=100"), api("/assign/people")]);
  const tip = x => x.why.map(w => `+${w.points} ${w.label}`).join("\n");
  v.innerHTML = tbar("Unassigned leads ranked by fit. Give each caller a batch: it shows in their My Day straight away.") +
    `<div class="card"><h2>1. Who gets how many</h2><div class="tbl-wrap"><table class="tbl"><thead><tr><th>Person</th><th>Role</th><th>Has now</th><th>Give</th></tr></thead><tbody>${people.map(u => `<tr><td><b>${esc(u.name)}</b></td><td>${esc(u.role)}</td><td>${u.open_leads} open · ${u.untouched} untouched</td><td><input type="number" min="0" max="500" class="an" data-u="${esc(u.id)}" value="${AS.n[u.id] || ""}" placeholder="0" style="width:90px" aria-label="Leads for ${esc(u.name)}"></td></tr>`).join("") || `<tr><td colspan="4">${empty("No callers yet", "Add callers under Settings > Users.")}</td></tr>`}</tbody></table></div>
    <div class="bar" style="margin-top:10px"><label style="min-width:320px">How to deal them out<select id="amode">${opts([["round_robin", "Round-robin: everyone gets a similar mix of strong and weaker leads"], ["top_first", "Best first: the first person gets the top N, the next person the next N"]], AS.mode)}</select></label><span class="grow"></span><button class="btn primary" id="aPrev">Preview</button></div><div id="aOut"></div></div>
    <div class="card"><div class="ch"><h2>2. Unassigned leads, best fit first <span class="muted">${pool.total.toLocaleString()}${pool.total > pool.items.length ? ` · showing the top ${pool.items.length}` : ""}</span></h2></div>
    ${AS.f.batch ? `<p class="warnc">Showing one batch only. <button class="btn sm" id="abClear">Show all unassigned</button></p>` : ""}<div class="bar"><select id="afc" aria-label="Campaign">${opts(S.campaigns.map(c => [c.id, c.name]).concat([["none", "No campaign"]]), AS.f.campaign, "All campaigns")}</select><select id="afh" aria-label="Channel">${opts(CH_OPTS, AS.f.channel, "Any channel")}</select><select id="afs" aria-label="Source">${opts(S.sources, AS.f.source, "Any source")}</select><label class="chk">Min fit <input type="number" id="afm" min="0" max="100" value="${AS.f.min_fit || ""}" style="width:70px"></label><label class="chk"><input type="checkbox" id="afg" ${AS.f.signal ? "checked" : ""}> Has a signal</label><label class="chk"><input type="checkbox" id="afp" ${AS.f.phone ? "checked" : ""}> Has a phone</label></div>
    ${pool.items.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>#</th><th>Lead</th><th>Fit</th><th>Signal</th><th>Phone</th><th>Campaign</th></tr></thead><tbody>${pool.items.map((x, i) => `<tr class="ar" data-id="${esc(x.id)}" style="cursor:pointer"><td>${i + 1}</td><td><b>${esc(x.full_name)}</b><div class="muted">${esc(x.title)}${x.title && x.company ? " · " : ""}${esc(x.company)}</div></td><td><b title="${esc(tip(x))}">${x.fit}</b><span class="fitbar"><i style="width:${x.fit}%"></i></span></td><td>${esc(x.signal) || '<span class="muted">none</span>'}</td><td>${x.has_phone ? "yes" : '<span class="muted">no</span>'}</td><td>${esc(campOf(x.campaign_id).name)}</td></tr>`).join("")}</tbody></table></div><p class="muted">Fit (0 to 100): signal 35 (+5 with a source link), title seniority up to 25, direct phone 15 (5 if it is only the company switchboard), email 5, company 5, LinkedIn 5. Hover a score for its breakdown.</p>` : empty("No unassigned leads match", "Sync a list from Apollo, import leads, or loosen the filters.")}</div>`;
  const filt = (id, fn) => $(id).onchange = e => { fn(e.target); PAGES.assign(v); };
  filt("#afc", t => AS.f.campaign = t.value); filt("#afh", t => AS.f.channel = t.value); filt("#afs", t => AS.f.source = t.value); filt("#afm", t => AS.f.min_fit = Math.max(0, Math.min(100, +t.value || 0))); filt("#afg", t => AS.f.signal = t.checked); filt("#afp", t => AS.f.phone = t.checked);
  if ($("#abClear")) $("#abClear").onclick = () => { AS.f.batch = ""; PAGES.assign(v); };
  $$(".ar", v).forEach(r => r.onclick = () => openLead(r.dataset.id));
  $$(".an", v).forEach(i => i.oninput = () => { AS.n[i.dataset.u] = Math.max(0, Math.min(500, +i.value || 0)); });
  $("#amode").onchange = e => { AS.mode = e.target.value; };
  const payload = dry => ({ people: Object.entries(AS.n).filter(([, n]) => n > 0).map(([user_id, count]) => ({ user_id, count })), mode: AS.mode, filters: AS.f, dry_run: dry });
  $("#aPrev").onclick = async e => {
    if (!payload(true).people.length) return toast("Type how many leads each person should get", true);
    e.target.disabled = true; let r; try { r = await api("/assign/run", { method: "POST", body: payload(true) }); } catch (er) { e.target.disabled = false; return toast(er.message, true); } e.target.disabled = false;
    const total = r.plan.reduce((a, x) => a + x.assigned, 0);
    $("#aOut").innerHTML = `<h3 style="margin-top:14px">Preview</h3>${r.shortfall ? `<p class="warnc">Only ${r.pool} lead(s) match the filters, so ${r.shortfall} fewer will be assigned than requested.</p>` : ""}<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Person</th><th>Asked</th><th>Getting</th><th>Average fit</th><th>First few</th></tr></thead><tbody>${r.plan.map(x => `<tr><td><b>${esc(x.name)}</b></td><td>${x.requested}</td><td>${x.assigned}</td><td>${x.avg_fit}</td><td>${x.sample.map(l => `${esc(l.name)} (${l.fit})`).join(", ")}</td></tr>`).join("")}</tbody></table></div>
      <div class="acts" style="margin-top:10px"><button class="btn primary" id="aGo" ${total ? "" : "disabled"}>Assign ${total} leads</button></div>`;
    if ($("#aGo")) $("#aGo").onclick = async b => { b.target.disabled = true; try { const d = await api("/assign/run", { method: "POST", body: payload(false) }); toast(`Assigned ${d.assigned} leads. They are in My Day now.`); AS.n = {}; PAGES.assign(v); } catch (er) { b.target.disabled = false; toast(er.message, true); } };
  };
};

// ---------------------------------------------------------------- NEW DATA (the admin's intake view: decisions are per batch, never per lead)
const INTAKE_ST = { new: ["New", "s2"], reviewed: ["Reviewed", "s3"], done: ["All assigned", "s7"] };
function batchAssign(b, people, done) {
  modal(`<h2>Assign this batch</h2><p class="muted">${esc(b.label)} · ${b.unassigned} lead(s) waiting. The best-fit leads are dealt out first (up to 500 per run).</p>
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Person</th><th>Has now</th><th>Give</th></tr></thead><tbody>${people.map(u => `<tr><td><b>${esc(u.name)}</b> <span class="muted">${esc(u.role)}</span></td><td>${u.open_leads} open · ${u.untouched} untouched</td><td><input type="number" min="0" max="500" class="bn" data-u="${esc(u.id)}" style="width:90px" aria-label="Leads for ${esc(u.name)}"></td></tr>`).join("") || `<tr><td colspan="3">${empty("No callers yet", "Add callers under Settings > Users.")}</td></tr>`}</tbody></table></div>
    <div class="bar" style="margin-top:10px"><button class="btn sm" id="bEven" type="button">Split evenly between everyone</button><select id="bMode" aria-label="How to deal them out">${opts([["round_robin", "Round-robin (similar mix for everyone)"], ["top_first", "Best first (first person gets the top N)"]], "round_robin")}</select></div><div id="bOut"></div>
    <div class="acts"><button class="btn" id="bX">Cancel</button><button class="btn primary" id="bPrev">Preview</button></div>`, { wide: true });
  const inputs = () => $$(".bn"), payload = dry => ({ people: inputs().filter(i => +i.value > 0).map(i => ({ user_id: i.dataset.u, count: Math.min(500, +i.value) })), mode: $("#bMode").value, filters: { batch: b.id }, dry_run: dry });
  $("#bX").onclick = closeModal;
  $("#bEven").onclick = () => { const k = inputs().length, total = Math.min(b.unassigned, 500); inputs().forEach((i, n) => { i.value = k ? Math.floor(total / k) + (n < total % k ? 1 : 0) : ""; }); };
  $("#bPrev").onclick = async e => {
    if (!payload(true).people.length) return toast("Type how many leads each person should get", true);
    e.target.disabled = true; let r; try { r = await api("/assign/run", { method: "POST", body: payload(true) }); } catch (er) { e.target.disabled = false; return toast(er.message, true); } e.target.disabled = false;
    const total = r.plan.reduce((a, x) => a + x.assigned, 0);
    $("#bOut").innerHTML = `<h3 style="margin-top:12px">Preview</h3>${r.shortfall ? `<p class="warnc">Only ${r.pool} lead(s) are waiting, so ${r.shortfall} fewer will be assigned than asked.</p>` : ""}<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Person</th><th>Getting</th><th>Average fit</th></tr></thead><tbody>${r.plan.map(x => `<tr><td><b>${esc(x.name)}</b></td><td>${x.assigned}</td><td>${x.avg_fit}</td></tr>`).join("")}</tbody></table></div><div class="acts"><button class="btn primary" id="bGo" ${total ? "" : "disabled"}>Assign ${total} leads</button></div>`;
    if ($("#bGo")) $("#bGo").onclick = async g => { g.target.disabled = true; try { const d = await api("/assign/run", { method: "POST", body: payload(false) }); closeModal(); toast(`Assigned ${d.assigned} leads. Callers see them in My Day now.`); done(); } catch (er) { g.target.disabled = false; toast(er.message, true); } };
  };
}
PAGES.inbox = async v => {
  const d = await api("/intake"), s = d.summary, tile = (l, x) => `<div class="card kpi"><span class="l">${l}</span><span class="v">${x}</span></div>`;
  const when = x => x.at ? fdate(x.at) : "";
  v.innerHTML = tbar("What has arrived and what still needs an owner. Review and assign whole batches: nobody has to approve leads one by one.", '<button class="btn" id="inAs">Rank & assign individually</button>') +
    `<div class="grid g4">${tile("Waiting for an owner", s.unassigned.toLocaleString())}${tile("Added this week", s.new_7d.toLocaleString())}${tile("With a signal", s.with_signal.toLocaleString())}${tile("With a phone", s.with_phone.toLocaleString())}</div>
    <div class="card" style="margin-top:14px"><div class="ch"><h2>Batches <span class="muted">${s.batches_to_review} to review</span></h2></div>${d.batches.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Batch</th><th>Source</th><th>Waiting</th><th>Assigned</th><th>Signal</th><th>Phone</th><th>Avg fit</th><th>Status</th><th></th></tr></thead><tbody>${d.batches.map(b => `<tr><td><b>${esc(b.label)}</b><div class="muted">${esc(b.campaigns.join(", ")) || "No campaign"}${b.by ? " · " + esc(b.by) : ""}${b.at ? " · " + when(b) : ""}</div></td><td>${esc(b.source)}</td><td><b>${b.unassigned}</b> <span class="muted">of ${b.total}</span></td><td>${b.assigned}</td><td>${b.with_signal}</td><td>${b.with_phone}</td><td>${b.unassigned ? b.avg_fit : "-"}</td><td><span class="pill ${INTAKE_ST[b.status][1]}">${INTAKE_ST[b.status][0]}</span></td><td style="white-space:nowrap">${b.unassigned ? `<button class="btn sm" data-rv="${esc(b.id)}">Review leads</button> <button class="btn sm primary" data-as="${esc(b.id)}">Assign…</button>` : ""}${b.status === "new" && b.id !== "none" ? ` <button class="btn sm" data-ok="${esc(b.id)}" title="Mark this whole batch as looked at">Mark reviewed</button>` : ""}</td></tr>`).join("")}</tbody></table></div>` : empty("No data yet", "Sync an Apollo list or import leads. Each import or sync shows up here as one batch.")}</div>
    <div class="card" style="margin-top:14px"><h2>Callers' capacity</h2>${d.people.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Person</th><th>Role</th><th>Open leads</th><th>Never contacted</th></tr></thead><tbody>${d.people.map(u => `<tr><td><b>${esc(u.name)}</b></td><td>${esc(u.role)}</td><td>${u.open_leads}</td><td>${u.untouched}</td></tr>`).join("")}</tbody></table></div>` : empty("No callers yet", "Add callers under Settings > Users.")}</div>`;
  const toAssign = batch => { AS.f = { campaign: "", channel: "", source: "", batch: batch || "", min_fit: 0, signal: false, phone: false }; INT.tab = "assign"; PAGES.intake(INT.root); };
  $("#inAs").onclick = () => toAssign("");
  $$("[data-rv]", v).forEach(b => b.onclick = () => toAssign(b.dataset.rv));
  $$("[data-as]", v).forEach(b => b.onclick = () => batchAssign(d.batches.find(x => x.id === b.dataset.as), d.people, () => PAGES.inbox(v)));
  $$("[data-ok]", v).forEach(b => b.onclick = async () => { b.disabled = true; try { await api(`/intake/batches/${b.dataset.ok}/review`, { method: "POST" }); toast("Batch marked as reviewed"); PAGES.inbox(v); } catch (e) { b.disabled = false; toast(e.message, true); } });
};

// ---------------------------------------------------------------- DASHBOARD (one place for the numbers: overview, suggestions, and every report as a tab)
const RF = { days: 30, campaign: "", channel: "", owner: "" }; const DT = { tab: "overview" };
const DTABS = [["overview", "Overview"], ["suggestions", "Suggestions"], ["scorecard", "Scorecard"], ["campaigns", "Campaigns"], ["channels", "Channels"], ["funnel", "Funnel"], ["sources", "Sources"], ["calls", "Call outcomes"], ["team", "Team"], ["health", "Pipeline health"], ["enrichment", "Enrichment"]];
PAGES.dashboard = async v => {
  const tabs = DTABS.filter(([k]) => k !== "suggestions" || (can("agents:act") && S.me.role !== "caller")), tab = tabs.some(t => t[0] === DT.tab) ? DT.tab : "overview"; DT.tab = tab;
  const filt = tab !== "suggestions", exp = can("export") && ["campaigns", "channels", "funnel", "sources", "calls", "team"].includes(tab);
  v.innerHTML = head("Dashboard", "How outreach is performing, and what needs attention.", filt ? `<div class="seg" role="group" aria-label="Period">${[7, 30, 90].map(x => `<button class="${RF.days === x ? "on" : ""}" data-d="${x}">${x}d</button>`).join("")}</div>${exp ? '<button class="btn" id="rx">Export CSV</button>' : ""}` : "") +
    (filt ? `<div class="bar"><select id="rc" aria-label="Campaign">${campOpts(RF.campaign, "All campaigns")}</select><select id="rh" aria-label="Channel">${opts([["call", "Calls"], ["linkedin", "LinkedIn"], ["email", "Email"], ["mixed", "Multi-channel"]], RF.channel, "All channels")}</select>${S.me.role === "caller" ? "" : `<select id="ro" aria-label="Salesperson">${opts([["unassigned", "Unassigned"], ...S.users.map(u => [u.id, u.name])], RF.owner, "All salespeople")}</select>`}</div>` : "") +
    `<div class="tabs">${tabs.map(([k, l]) => `<button class="tab ${tab === k ? "on" : ""}" data-r="${k}">${l}</button>`).join("")}</div><div id="dtb" class="${tab === "scorecard" ? "neon" : ""}">${sk(6)}</div>`;
  $$("[data-d]", v).forEach(b => b.onclick = () => { RF.days = +b.dataset.d; PAGES.dashboard(v); });
  [["#rc", "campaign"], ["#rh", "channel"], ["#ro", "owner"]].forEach(([i, k]) => $(i, v) && ($(i, v).onchange = e => { RF[k] = e.target.value; PAGES.dashboard(v); }));
  $$(".tab[data-r]", v).forEach(t => t.onclick = () => { DT.tab = t.dataset.r; PAGES.dashboard(v); });
  if ($("#rx")) $("#rx").onclick = () => download(`/analytics/export/${tab}?` + qs(RF), tab + ".csv");
  const b = $("#dtb", v);
  try { if (tab === "overview") await renderOverview(b); else if (tab === "suggestions") await renderSuggestions(b); else await renderReport(b, tab); }
  catch (e) { b.innerHTML = `<div class="card">${empty("Could not load this tab", e.message)}</div>`; }
};

// ---------------------------------------------------------------- LEAD INTAKE (inbox of new batches, import, and assigning: one flow)
const INT = { tab: "inbox", root: null };
PAGES.intake = async v => {
  INT.root = v; const tabs = [["inbox", "Inbox"], ["import", "Import"], ["assign", "Assign"]];
  v.innerHTML = head("Lead intake", "Bring leads in, review each batch, then hand them to callers.") + `<div class="tabs">${tabs.map(([k, l]) => `<button class="tab ${INT.tab === k ? "on" : ""}" data-i="${k}">${l}</button>`).join("")}</div><div id="itb">${sk(6)}</div>`;
  $$(".tab[data-i]", v).forEach(t => t.onclick = () => { INT.tab = t.dataset.i; PAGES.intake(v); });
  const b = $("#itb", v);
  try { await ({ inbox: PAGES.inbox, import: PAGES.discovery, assign: PAGES.assign }[INT.tab])(b); }
  catch (e) { b.innerHTML = `<div class="card">${empty("Could not load this tab", e.message)}</div>`; }
};

// ---------------------------------------------------------------- INTEGRATIONS
const renderIntegrations = async v => {
  const list = await api("/integrations");
  v.innerHTML = tbar("Secrets live in the server .env only. Nothing here is ever sent to the browser.") + `<div class="grid g2">${list.map(i => `<div class="card int"><div><h2><span class="dot ${i.status === "connected" ? "on" : ""}"></span>${esc(i.name)}</h2><p class="muted">${esc(i.note)}</p><p class="tag">Env: ${esc(i.env)}</p></div><div style="text-align:right"><div class="pill ${i.status === "connected" ? "s7" : "s2"}">${i.status === "connected" ? "Connected" : "Pending configuration"}</div><div style="margin-top:8px"><button class="btn sm" data-t="${i.id}">Test connection</button></div></div></div>`).join("")}</div>`;
  $$("[data-t]", v).forEach(b => b.onclick = async () => { try { const r = await api(`/integrations/${b.dataset.t}/test`, { method: "POST" }); toast(r.message, !r.ok); } catch (e) { toast(e.message, true); } });
};

// ---------------------------------------------------------------- SETTINGS
let ST = "users";
PAGES.settings = async v => {
  const TABS = [["users", "Users & roles", "settings:read"], ["campaigns", "Campaigns", "assign"], ["pipeline", "Pipeline & goals", "settings:read"], ["integrations", "Integrations", "integrations:read"], ["audit", "Audit log", "settings:read"], ["roles", "Role matrix", "settings:read"]].filter(t => can(t[2]));
  if (!TABS.some(t => t[0] === ST)) ST = TABS[0]?.[0];
  v.innerHTML = head("Settings", "Users, campaigns, pipeline rules, integrations and audit history.") + `<div class="tabs">${TABS.map(([k, l]) => `<button class="tab ${ST === k ? "on" : ""}" data-s="${k}">${l}</button>`).join("")}</div><div id="sb">${sk(5)}</div>`;
  $$(".tab[data-s]").forEach(t => t.onclick = () => { ST = t.dataset.s; PAGES.settings(v); }); const b = $("#sb");
  if (ST === "integrations") await renderIntegrations(b);
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
    history.replaceState(null, "", "#/"); await start();                      // no page reload, and always begins at the start page
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
  if (prev !== m.me.id) history.replaceState(null, "", "#/");                // a different person (or a first visit) always starts at the beginning
  Object.assign(S, { me: m.me, perms: m.perms, stages: m.stages, outcomes: m.outcomes, sources: m.sources, users: m.users, types: m.activity_types, oppStages: m.opp_stages, campaigns: m.campaigns, obt: m.outcomes_by_type, ai: m.ai, apolloLinks: m.apollo_links });
  $("#app").classList.remove("hidden");
  $("#me").innerHTML = `<span>${esc(m.me.name)} · ${esc(m.me.role)}</span><span class="av">${esc(m.me.name[0])}</span>${CFG.AUTH_ENABLED ? '<button class="btn sm" id="so">Sign out</button>' : ""}`;
  if (m.demo) $("#demoBadge").classList.remove("hidden");
  if (m.auth_disabled && !CFG.AUTH_ENABLED) { $("#mockWrap").classList.remove("hidden"); $("#mockSel").innerHTML = opts(m.users.map(u => [u.id, `${u.name} · ${u.role}`]), m.me.id); $("#mockSel").onchange = e => { localStorage.setItem("mockUser", e.target.value); location.hash = "#/"; location.reload(); }; }
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