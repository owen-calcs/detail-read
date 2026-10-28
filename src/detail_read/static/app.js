"use strict";

const KN_TO_KIP = 0.224809;
const KNM_TO_KIPFT = 0.737562;
const LOAD_FIELDS = [
  ["shear", "Shear Vf", "kN"],
  ["tension", "Tension Tf", "kN"],
  ["compression", "Compression Cf", "kN"],
  ["horizontal", "Horizontal Hf", "kN"],
  ["moment", "Moment Mf", "kN·m"],
];
const ROLE_LABEL = { beam: "Beam", column: "Column / support", girder: "Girder", chord: "Chord", branch: "Branch", brace: "Brace" };

const $ = (s) => document.querySelector(s);
const state = { job: null, page: null, detail: null, calculators: [], calcInfo: {}, solving: false, serverKey: false };

// ---------- connectionshub API key ----------
// Each user's key lives in their own browser and rides along on requests that reach connectionshub.

const KEY_STORE = "detailRead.connectionshubKey";

function getKey() {
  try { return localStorage.getItem(KEY_STORE) || ""; } catch { return ""; }
}

function setKey(v) {
  try { v ? localStorage.setItem(KEY_STORE, v) : localStorage.removeItem(KEY_STORE); } catch { /* private mode: key lasts this page only */ }
  state.sessionKey = v;
}

function currentKey() { return getKey() || state.sessionKey || ""; }

async function api(path, opts = {}) {
  const headers = new Headers(opts.headers || {});
  const key = currentKey();
  if (key) headers.set("X-Connectionshub-Key", key);
  const r = await fetch(path, { ...opts, headers });
  if (r.status === 401) {
    const msg = (await r.clone().json().catch(() => ({}))).detail || "connectionshub needs an API key.";
    openKeyPanel(msg);
  }
  return r;
}

function renderKeyButton() {
  const b = $("#key-btn");
  const mine = !!currentKey();
  b.className = `key-btn ${mine ? "user" : state.serverKey ? "server" : "missing"}`;
  b.textContent = mine ? "API key: yours" : state.serverKey ? "API key: server default" : "Add API key";
  b.title = mine ? "Calculations run in your connectionshub account" : state.serverKey ? "Using the key configured on this server; add yours to use your own account" : "Needed to solve on connectionshub";
}

function openKeyPanel(message, isError = !!message) {
  const p = $("#key-panel");
  p.classList.remove("hidden");
  $("#key-btn").setAttribute("aria-expanded", "true");
  const help = $("#key-help");
  help.classList.toggle("err", isError);
  help.textContent = message || "Saved in this browser only and sent only to this Detail Read server, which uses it to run calculations in your Calcs.com connectionshub account.";
  $("#key-input").value = currentKey();
  $("#key-input").focus();
}

function closeKeyPanel() {
  $("#key-panel").classList.add("hidden");
  $("#key-btn").setAttribute("aria-expanded", "false");
}

$("#key-btn").addEventListener("click", () => ($("#key-panel").classList.contains("hidden") ? openKeyPanel() : closeKeyPanel()));
$("#key-panel").addEventListener("submit", (e) => {
  e.preventDefault();
  setKey($("#key-input").value.trim());
  state.calculators = []; state.calcInfo = {};
  closeKeyPanel();
  renderKeyButton();
  if (state.detail) renderCalculatorSelect(state.detail.d);
});
$("#key-clear").addEventListener("click", () => { setKey(""); $("#key-input").value = ""; closeKeyPanel(); renderKeyButton(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeKeyPanel(); });

fetch("/api/config").then((r) => r.json()).then((c) => {
  state.serverKey = c.serverKey;
  renderKeyButton();
  if (!c.serverKey && !currentKey()) openKeyPanel("Add your connectionshub API key to solve connections. You can still upload and read drawings without it.", false);
}).catch(renderKeyButton);

// ---------- upload & polling ----------

const drop = $("#drop");
$("#file").addEventListener("change", (e) => e.target.files[0] && upload(e.target.files[0]));
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => {
  e.preventDefault(); drop.classList.remove("over");
  const f = e.dataTransfer.files[0];
  if (f) upload(f);
});
$("#show-all").addEventListener("change", () => state.page != null && renderPage(state.page));

async function upload(file) {
  setStatus(`Uploading ${file.name}…`);
  $("#pages").innerHTML = "";
  $("#panel").classList.add("hidden");
  $("#empty").classList.remove("hidden");
  const body = new FormData();
  body.append("file", file);
  const r = await fetch("/api/upload", { method: "POST", body });
  if (!r.ok) return setStatus((await r.json()).detail || "Upload failed", true);
  const { job_id } = await r.json();
  location.hash = job_id;  // survives a refresh while the server is up
  showProgress(0, 0);
  poll(job_id);
}

if (location.hash.length > 1) poll(location.hash.slice(1));

async function poll(id) {
  const r = await fetch(`/api/jobs/${id}`);
  const job = await r.json();
  if (!r.ok) { history.replaceState(null, "", location.pathname); return setStatus(job.detail, true); }
  if (job.status === "running") {
    showProgress(job.progress, job.total);
    setStatus(`OCR ${job.progress}/${job.total || "?"} pages…`);
    return setTimeout(() => poll(id), 800);
  }
  $("#progress").classList.add("hidden");
  if (job.status === "error") return setStatus(job.error, true);
  state.job = job;
  const nDetails = job.pages.reduce((n, p) => n + p.details.length, 0);
  setStatus(`${job.filename}: ${job.pages.length} pages, ${nDetails} connection details found`);
  renderPageList();
  renderSolveAll();
  if (job.solveAll && job.solveAll.status === "running") pollSolveAll();
  if (job.best) selectDetail(job.best.page, job.best.detail, true);
  else if (job.pages.length) renderPage(0);
}

// ---------- solve all ----------

function solvableKeys() {
  return state.job.pages.flatMap((p) => p.details.filter((d) => d.candidates.length).map((d) => `${p.index}:${d.id}`));
}

function renderSolveAll() {
  const job = state.job;
  const wrap = $("#solve-all");
  const keys = solvableKeys();
  if (!keys.length) return wrap.classList.add("hidden");
  wrap.classList.remove("hidden");
  const results = job.results || {};
  const run = job.solveAll;
  const running = run && run.status === "running";
  const remaining = keys.filter((k) => !results[k]).length;

  const btn = $("#solve-all-btn");
  // A single solve in flight isn't in results yet; starting a batch now would solve that detail twice.
  btn.disabled = state.solving && !running;
  btn.classList.toggle("secondary", running);
  if (running) btn.textContent = "Stop after current solves";
  else if (remaining) btn.textContent = `Solve all ${remaining} detected connection${remaining === 1 ? "" : "s"}`;
  else btn.textContent = "Re-solve all with detected values";

  const prog = $("#solve-all-progress");
  prog.classList.toggle("hidden", !running);
  if (running) {
    prog.querySelector(".fill").style.width = run.total ? `${(100 * run.done) / run.total}%` : "0";
    prog.querySelector(".label").textContent = `Solved ${run.done} of ${run.total} on connectionshub…`;
  }

  const statuses = keys.map((k) => results[k] && results[k].summary.status).filter(Boolean);
  const pass = statuses.filter((s) => s === "pass").length;
  const fail = statuses.filter((s) => s === "fail").length;
  const errors = run ? Object.keys(run.errors).length : 0;
  const other = statuses.length - pass - fail;
  $("#solve-all-summary").innerHTML = statuses.length || errors
    ? `<b class="pass">${pass} pass</b> · <b class="fail">${fail} fail</b>${other ? ` · ${other} no verdict` : ""}${errors ? ` · <b class="err">${errors} error${errors === 1 ? "" : "s"}</b>` : ""} — ${statuses.length} of ${keys.length} solved`
    : `${keys.length} of ${state.job.pages.reduce((n, p) => n + p.details.length, 0)} details have a detected calculator. Each solve creates a calculation in your connectionshub project.`;
  updateBadges();
}

$("#solve-all-btn").addEventListener("click", async () => {
  const job = state.job;
  const run = job.solveAll;
  if (run && run.status === "running") {
    await fetch(`/api/jobs/${job.id}/solve-all/cancel`, { method: "POST" });
    $("#solve-all-btn").disabled = true;
    $("#solve-all-btn").textContent = "Stopping…";
    return;
  }
  const keys = solvableKeys();
  const remaining = keys.filter((k) => !(job.results || {})[k]).length;
  const resolve = remaining === 0;
  const n = resolve ? keys.length : remaining;
  if (!confirm(`Solve ${n} connection${n === 1 ? "" : "s"} on connectionshub using the detected calculator, sections and loads?\n\n` +
    `This creates ${n} calculation${n === 1 ? "" : "s"} in your Detail Read project. They can't be deleted from here.`)) return;
  const r = await api(`/api/jobs/${job.id}/solve-all?skip_solved=${!resolve}`, { method: "POST" });
  const data = await r.json();
  if (!r.ok) return setStatus(data.detail || "Solve all failed to start", true);
  job.solveAll = data;
  renderSolveAll();
  pollSolveAll();
});

async function pollSolveAll() {
  const r = await fetch(`/api/jobs/${state.job.id}`);
  if (!r.ok) return;
  const fresh = await r.json();
  state.job.results = fresh.results || {};
  state.job.solveAll = fresh.solveAll;
  renderSolveAll();
  // Show the result for the open detail as soon as it lands.
  if (state.detail && !state.solving) {
    const key = `${state.detail.page}:${state.detail.d.id}`;
    const res = state.job.results[key];
    const shown = $("#result").dataset.key === key && $("#result").dataset.sheet === (res && res.summary.sheetId);
    if (res && !shown) renderResult(res, key);
  }
  const run = fresh.solveAll;
  if (run && run.status === "running") setTimeout(pollSolveAll, 2000);
  else if (run && run.fatal) { setStatus(`Solve all stopped: ${run.fatal}`, true); openKeyPanel(run.fatal); }
  else if (run) setStatus(`Solve all ${run.status}: ${run.done} of ${run.total} processed`);
}

function updateBadges() {
  const results = state.job.results || {};
  const run = state.job.solveAll;
  const running = new Set(run && run.status === "running" ? run.running : []);
  document.querySelectorAll(".detail-item").forEach((b) => {
    const k = b.dataset.key;
    const res = results[k];
    const err = run && run.errors[k];
    const badge = b.querySelector(".badge");
    const gl = b.querySelector(".gl");
    if (running.has(k)) {
      badge.className = "badge running"; badge.textContent = "solving";
    } else if (res) {
      badge.className = `badge ${res.summary.status}`; badge.textContent = res.summary.status;
    } else if (err) {
      badge.className = "badge error"; badge.textContent = "error"; badge.title = err;
    } else {
      badge.className = "badge hidden";
    }
    const g = res && res.summary.governingLoad;
    gl.textContent = g ? `${fmt(g.value, g.units)} — ${g.label}` : "";
    gl.classList.toggle("hidden", !g);
  });
}

function showProgress(done, total) {
  const p = $("#progress");
  p.classList.remove("hidden");
  p.querySelector(".fill").style.width = total ? `${(100 * done) / total}%` : "4%";
  p.querySelector(".label").textContent = total ? `Reading page ${done} of ${total}` : "Opening PDF…";
}

function setStatus(msg, isError = false) {
  const el = $("#status");
  el.textContent = msg;
  el.style.color = isError ? "var(--fail)" : "";
}

// ---------- page list ----------

function renderPageList() {
  const nav = $("#pages");
  nav.innerHTML = "";
  for (const p of state.job.pages) {
    const g = el("div", "page-group");
    const head = el("div", "page-head");
    head.innerHTML = `<span>Page ${p.index + 1}</span><span class="count">${p.details.length} detail${p.details.length === 1 ? "" : "s"}</span>`;
    head.onclick = () => renderPage(p.index);
    g.append(head);
    for (const d of p.details) {
      const b = el("button", "detail-item");
      b.dataset.key = `${p.index}:${d.id}`;
      const c = d.candidates[0];
      const loads = d.loads.map((l) => l.metric).join(", ");
      b.innerHTML = `<div class="t"><span class="badge hidden"></span>${esc(d.title)}</div>
        <div class="s">${c ? esc(c.name) : '<span class="none">No calculator match</span>'}</div>
        <div class="s">${esc(d.members.map((m) => m.imperial).slice(0, 3).join(" · "))}${loads ? " — " + esc(loads) : ""}</div>
        <div class="gl hidden"></div>`;
      b.onclick = () => selectDetail(p.index, d.id, false);
      g.append(b);
    }
    nav.append(g);
  }
}

// ---------- viewer ----------

function renderPage(index, focus) {
  state.page = index;
  const p = state.job.pages[index];
  $("#page-title").textContent = `Page ${index + 1} of ${state.job.pages.length}`;
  const stage = $("#stage");
  stage.innerHTML = "";
  const img = new Image();
  img.src = `/api/jobs/${state.job.id}/pages/${index}.png?width=1800`;
  img.alt = `Page ${index + 1}`;
  stage.append(img);

  const showAll = $("#show-all").checked;
  const d = state.detail && state.detail.page === index ? state.detail.d : null;
  const inDetail = new Set(d ? [...d.members, ...d.plates, ...d.bolts, ...d.loads].map(key) : []);
  for (const e of p.entities) {
    if (!["member", "plate", "bolt", "load"].includes(e.kind)) continue;
    const mine = inDetail.has(key(e));
    if (!mine && !showAll) continue;
    stage.append(box(e.bbox, `${e.kind}${mine ? "" : " dim"}`, `${e.metric} → ${e.imperial}`));
  }
  if (d) stage.append(box(pad(d.bbox, 0.01), "detail", d.title));
  if (focus) img.onload = () => scrollToBox(focus);
}

function key(e) { return `${e.kind}|${e.raw}|${e.bbox.map((v) => v.toFixed(4)).join(",")}`; }
function pad(b, m) { return [b[0] - m, b[1] - m, b[2] + m, b[3] + m]; }

function box(b, cls, tag) {
  const div = el("div", `box ${cls}`);
  Object.assign(div.style, {
    left: `${b[0] * 100}%`, top: `${b[1] * 100}%`,
    width: `${(b[2] - b[0]) * 100}%`, height: `${(b[3] - b[1]) * 100}%`,
  });
  const t = el("span", "tag");
  t.textContent = tag;
  div.append(t);
  div.title = tag;
  return div;
}

function scrollToBox(b) {
  const v = $("#viewer");
  const stage = $("#stage");
  const cx = ((b[0] + b[2]) / 2) * stage.clientWidth;
  const cy = ((b[1] + b[3]) / 2) * stage.clientHeight;
  v.scrollTo({ left: cx - v.clientWidth / 2, top: cy - v.clientHeight / 2, behavior: "smooth" });
}

// ---------- detail panel ----------

async function selectDetail(pageIndex, detailId, autoSolve) {
  const p = state.job.pages[pageIndex];
  const d = p.details.find((x) => x.id === detailId);
  state.detail = { page: pageIndex, d };
  document.querySelectorAll(".detail-item").forEach((b) => b.classList.toggle("active", b.dataset.key === `${pageIndex}:${detailId}`));
  renderPage(pageIndex, d.bbox);
  $("#empty").classList.add("hidden");
  $("#panel").classList.remove("hidden");
  $("#result").classList.add("hidden");
  $("#detail-title").textContent = `Page ${pageIndex + 1} — ${d.title}`;
  renderConversions(d);
  renderLoads(d);
  await renderCalculatorSelect(d);
  const key = `${pageIndex}:${detailId}`;
  const done = (state.job.results || {})[key];
  if (done) {
    if ([...$("#calc").options].some((o) => o.value === done.calculator.code)) $("#calc").value = done.calculator.code;
    await renderCalcDetails(d);
    if (done.calculator.preset) $("#preset").value = done.calculator.preset;
    renderResult(done, key);
  } else if (autoSolve && d.candidates.length) {
    solve();
  }
}

function renderConversions(d) {
  const rows = [];
  const group = (title, items) => {
    if (!items.length) return;
    rows.push(`<tr><td class="kind" colspan="2">${title}</td></tr>`);
    for (const e of items) rows.push(`<tr><td>${esc(e.metric)}</td><td><b>${esc(e.imperial)}</b></td></tr>`);
  };
  group("Members", d.members);
  group("Plates", d.plates);
  group("Bolts", d.bolts);
  group("Design loads", d.loads);
  $("#conv").innerHTML = rows.join("") || `<tr><td colspan="2" class="muted">No callouts read in this detail.</td></tr>`;
}

function renderLoads(d) {
  const acc = {};
  for (const l of d.loads) {
    const a = l.data.action === "unknown" ? "shear" : l.data.action;
    acc[a] = Math.max(acc[a] || 0, l.data.value_kn);
  }
  const wrap = $("#loads");
  wrap.innerHTML = "";
  for (const [k, label, unit] of LOAD_FIELDS) {
    const lab = el("label");
    const v = acc[k] || "";
    lab.innerHTML = `${label} (${unit})<input type="number" step="any" min="0" data-load="${k}" value="${v}"><span class="imp"></span>`;
    const input = lab.querySelector("input");
    const imp = lab.querySelector(".imp");
    const upd = () => {
      const n = parseFloat(input.value);
      imp.textContent = isNaN(n) || !n ? "" : k === "moment" ? `= ${(n * KNM_TO_KIPFT).toFixed(2)} kip·ft` : `= ${(n * KN_TO_KIP).toFixed(2)} kip`;
    };
    input.addEventListener("input", upd);
    upd();
    wrap.append(lab);
  }
}

async function loadCalculators() {
  if (!state.calculators.length) {
    const r = await api("/api/calculators");
    if (r.ok) state.calculators = await r.json();
  }
  return state.calculators;
}

async function calcInfo(code) {
  if (!state.calcInfo[code]) {
    const r = await api(`/api/calculators/${encodeURIComponent(code)}`);
    if (!r.ok) throw new Error((await r.json()).detail || "calculator lookup failed");
    state.calcInfo[code] = await r.json();
  }
  return state.calcInfo[code];
}

async function renderCalculatorSelect(d) {
  const sel = $("#calc");
  const all = await loadCalculators();
  const suggested = new Set(d.candidates.map((c) => c.code));
  sel.innerHTML = "";
  if (d.candidates.length) {
    const og = el("optgroup"); og.label = "Detected";
    for (const c of d.candidates) og.append(new Option(`${c.name} (${Math.round(c.score * 100)}%)`, c.code));
    sel.append(og);
  }
  const og2 = el("optgroup"); og2.label = "All connectionshub calculators";
  for (const t of all.filter((t) => !suggested.has(t.code))) {
    const std = t.code.includes("CSA") ? "CSA S16" : "AISC 360";
    og2.append(new Option(`${t.name} — ${std}`, t.code));
  }
  sel.append(og2);
  sel.onchange = () => renderCalcDetails(d);
  await renderCalcDetails(d);
}

async function renderCalcDetails(d) {
  const code = $("#calc").value;
  const cand = d.candidates.find((c) => c.code === code);
  $("#calc-why").textContent = cand ? `Why: ${cand.reasons.join("; ")}` : "Chosen manually.";
  const roles = $("#roles");
  roles.innerHTML = "";
  let info;
  try { info = await calcInfo(code); } catch (e) { $("#calc-why").textContent = e.message; return; }

  const ps = $("#preset");
  ps.innerHTML = "";
  for (const p of info.presets) ps.append(new Option(p.name, p.code));
  if (!info.presets.length) ps.append(new Option("(calculator defaults)", ""));
  ps.value = (cand && cand.preset_hint && info.presets.some((p) => p.code === cand.preset_hint)) ? cand.preset_hint : (info.defaultPreset || "");

  const shown = cand ? Object.keys(cand.roles) : [];
  const roleSet = new Set([...shown, ...guessRoles(info)]);
  for (const role of roleSet) {
    const lab = el("label", "field");
    lab.innerHTML = `${ROLE_LABEL[role] || role} section (imperial)<input data-role="${role}" list="members-dl" placeholder="calculator default" value="${esc((cand && cand.roles[role]) || "")}">`;
    roles.append(lab);
  }
  let dl = $("#members-dl");
  if (!dl) { dl = el("datalist"); dl.id = "members-dl"; document.body.append(dl); }
  dl.innerHTML = d.members.map((m) => `<option value="${esc(m.imperial)}">${esc(m.metric)}</option>`).join("");
}

function guessRoles(info) {
  const labels = info.sectionFields.map((f) => f.label.toLowerCase()).join("|");
  const out = [];
  if (/beam section|girt section/.test(labels)) out.push("beam");
  if (/column section|hss section/.test(labels)) out.push("column");
  if (/chord section/.test(labels)) out.push("chord");
  if (/branch section/.test(labels)) out.push("branch");
  if (/brace section/.test(labels)) out.push("brace");
  if (/girder section/.test(labels)) out.push("girder");
  return out;
}

$("#solve").addEventListener("click", () => solve());

async function solve() {
  if (state.solving || !state.detail) return;
  const { page, d } = state.detail;
  const roles = {};
  document.querySelectorAll("[data-role]").forEach((i) => { if (i.value.trim()) roles[i.dataset.role] = i.value.trim().toUpperCase(); });
  const loads = {};
  document.querySelectorAll("[data-load]").forEach((i) => { const n = parseFloat(i.value); if (n) loads[i.dataset.load] = n; });
  const body = { job_id: state.job.id, page, detail_id: d.id, code: $("#calc").value, preset: $("#preset").value || null, roles, loads_kn: loads };

  state.solving = true;
  renderSolveAll();
  const btn = $("#solve");
  btn.disabled = true;
  btn.textContent = "Solving on connectionshub… (up to a minute)";
  const res = $("#result");
  res.classList.remove("hidden");
  res.innerHTML = `<div class="muted">Creating the calculation and solving it…</div>`;
  const key = `${page}:${d.id}`;
  const before = ((state.job.results || {})[key] || {}).summary?.sheetId;
  try {
    let data;
    try {
      const r = await api("/api/solve", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      data = await r.json();
      if (!r.ok) throw new Error(data.detail || "Solve failed");
    } catch (e) {
      if (!(e instanceof TypeError)) throw e;
      // The connection dropped (long request, sleep, flaky network). The server keeps solving and records
      // the result on the job, so wait for it there rather than solving again.
      res.innerHTML = `<div class="muted">Lost the connection while solving; waiting for the server to finish…</div>`;
      data = await waitForResult(key, before);
    }
    (state.job.results ||= {})[key] = data;
    renderResult(data, key);
    renderSolveAll();
  } catch (e) {
    res.innerHTML = `<h3>Solve failed</h3><div class="error-box">${esc(e.message)}</div>`;
  } finally {
    state.solving = false;
    btn.disabled = false;
    btn.textContent = "Solve on connectionshub";
    renderSolveAll();
  }
}

async function waitForResult(key, previousSheetId, timeoutMs = 180000) {
  const until = Date.now() + timeoutMs;
  while (Date.now() < until) {
    await new Promise((r) => setTimeout(r, 3000));
    try {
      const j = await (await fetch(`/api/jobs/${state.job.id}`)).json();
      const res = (j.results || {})[key];
      if (res && res.summary.sheetId !== previousSheetId) return res;
    } catch { /* server still unreachable; keep waiting */ }
  }
  throw new Error("Lost the connection and no result arrived. Check the server is running, then solve again.");
}

function fmt(v, units) {
  if (v == null) return "—";
  const n = typeof v === "number" ? (Math.abs(v) >= 100 ? v.toFixed(1) : v.toFixed(2)) : v;
  return `${n}${units ? " " + units.replace("*", "·") : ""}`;
}

function metricOf(v, units) {
  if (typeof v !== "number" || !units) return "";
  if (units === "kip") return `${(v / KN_TO_KIP).toFixed(1)} kN`;
  if (/kip\s*[*·]?\s*ft/.test(units)) return `${(v / KNM_TO_KIPFT).toFixed(1)} kN·m`;
  return "";
}

function renderResult(data, key) {
  const s = data.summary;
  const box = $("#result");
  box.classList.remove("hidden");
  box.dataset.key = key || "";
  box.dataset.sheet = s.sheetId || "";
  const g = s.governingLoad;
  const util = typeof s.utilization === "number" ? s.utilization : null;
  const parts = [];
  parts.push(`<div style="display:flex;justify-content:space-between;align-items:center">
    <h3 style="margin:0">${esc(data.calculator.name)}</h3><span class="pill ${esc(s.status)}">${esc(s.status)}</span></div>
    <div class="muted" style="font-size:12px">${esc(data.calculator.preset || "defaults")} · ${esc(s.designMethod)}</div>`);
  if (g) {
    parts.push(`<div class="gov"><div class="lbl">Governing load — ${esc(g.label)}</div>
      <div class="num">${esc(fmt(g.value, g.units))}<small>${esc(metricOf(g.value, g.units))}</small></div>
      <div class="combo">${g.combination ? `Governing combination: <b>${esc(g.combination)}</b>` : "Factored load as noted on the drawing"}</div></div>`);
  } else {
    parts.push(`<div class="gov"><div class="lbl">Governing load</div><div class="num">—</div><div class="combo muted">The calculator returned no demand values.</div></div>`);
  }
  if (s.governingCheck) {
    parts.push(`<div class="combo">Governing check: <b>${esc(s.governingCheck.label)}</b> (${esc(fmt(s.governingCheck.value, s.governingCheck.units))})</div>`);
  }
  if (util != null) {
    parts.push(`<div class="util"><div class="track"><div class="bar2 ${util > 1 ? "over" : ""}" style="width:${Math.min(util, 1) * 100}%"></div></div>
      <div class="cap">Utilization ${(util * 100).toFixed(0)}%</div></div>`);
  }
  if (s.failReasons.length) parts.push(`<ul class="reasons">${s.failReasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>`);
  if (s.sheetUrl) parts.push(`<a class="sheet" href="${esc(s.sheetUrl)}" target="_blank" rel="noopener">Open calculation on Calcs.com ↗</a>`);
  if (s.governingCombinations.length) {
    parts.push(table("Governing load combinations", s.governingCombinations.map((c) => [c.label, c.combination])));
  }
  if (s.demands.length) parts.push(table("Demands", s.demands.map((d) => [d.label, fmt(d.value, d.units)])));
  if (s.capacities.length) parts.push(table("Lowest capacities", s.capacities.map((d) => [d.label, fmt(d.value, d.units)])));
  const sent = Object.entries(data.roles).map(([k, v]) => [ROLE_LABEL[k] || k, v]);
  for (const [k, v] of Object.entries(data.loadsKn)) sent.push([`${k} (kN)`, v]);
  parts.push(table("Inputs sent", sent));
  if (data.notes.length) parts.push(`<ul class="notes">${data.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>`);
  $("#result").innerHTML = parts.join("");
}

function table(title, rows) {
  return `<details><summary>${esc(title)} (${rows.length})</summary><table>${rows
    .map(([a, b]) => `<tr><td>${esc(a)}</td><td class="v">${esc(b)}</td></tr>`).join("")}</table></details>`;
}

function el(tag, cls) { const e = document.createElement(tag); if (cls) e.className = cls; return e; }
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
