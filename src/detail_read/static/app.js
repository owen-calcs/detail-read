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
const state = { job: null, page: null, detail: null, calculators: [], calcInfo: {}, solving: false };

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
  if (job.best) selectDetail(job.best.page, job.best.detail, true);
  else if (job.pages.length) renderPage(0);
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
      b.innerHTML = `<div class="t">${esc(d.title)}</div>
        <div class="s">${c ? esc(c.name) : '<span class="none">No calculator match</span>'}</div>
        <div class="s">${esc(d.members.map((m) => m.imperial).slice(0, 3).join(" · "))}${loads ? " — " + esc(loads) : ""}</div>`;
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
  if (autoSolve && d.candidates.length) solve();
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
    const r = await fetch("/api/calculators");
    if (r.ok) state.calculators = await r.json();
  }
  return state.calculators;
}

async function calcInfo(code) {
  if (!state.calcInfo[code]) {
    const r = await fetch(`/api/calculators/${encodeURIComponent(code)}`);
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
  const btn = $("#solve");
  btn.disabled = true;
  btn.textContent = "Solving on connectionshub… (up to a minute)";
  const res = $("#result");
  res.classList.remove("hidden");
  res.innerHTML = `<div class="muted">Creating the calculation and solving it…</div>`;
  try {
    const r = await fetch("/api/solve", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || "Solve failed");
    renderResult(data);
  } catch (e) {
    res.innerHTML = `<h3>Solve failed</h3><div class="error-box">${esc(e.message)}</div>`;
  } finally {
    state.solving = false;
    btn.disabled = false;
    btn.textContent = "Solve on connectionshub";
  }
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

function renderResult(data) {
  const s = data.summary;
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
