"use strict";
// Batch Review page: one batch at a time, original | annotated side by side, synced zoom.
const $ = (s) => document.querySelector(s);
const S = {
  batches: [], batch: null, idx: 0, live: false, frame: null, token: 0,
  hidden: new Set(), view: { s: 1, tx: 0, ty: 0 }, W: 0, H: 0,
};
const LS = "batchReview.";
function lsGet(k, d) { try { const v = localStorage.getItem(LS + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } }
function lsSet(k, v) { try { localStorage.setItem(LS + k, JSON.stringify(v)); } catch (e) { /* private mode */ } }
function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

let toastT = 0;
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("on"); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("on"), 1800); }

async function api(path) {
  const r = await fetch(path);
  const d = await r.json().catch(() => ({ error: "bad response" }));
  if (!r.ok || d.error) throw new Error(d.error || ("HTTP " + r.status));
  return d;
}

async function copyText(text, what) {
  try { await navigator.clipboard.writeText(text); }
  catch (e) { // not a secure context (opened by LAN address): old-style copy
    const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta);
    ta.select(); document.execCommand("copy"); ta.remove();
  }
  toast("Copied " + (what || text));
}

// ---------- batches ----------
async function loadBatches(keepId) {
  const d = await api("/api/batches");
  S.batches = d.batches;
  const sel = $("#batch");
  sel.innerHTML = d.batches.map((b) => `<option value="${esc(b.id)}">${esc(b.name)}</option>`).join("");
  $("#status").textContent = `${d.batches.length} batches · ${d.geo_rows} lat/lon rows · ${d.cad_images} CAD images`
    + (d.warnings.length ? ` · ${d.warnings.length} warning(s), see server log` : "");
  if (!d.batches.length) { $("#status").textContent = "No batches found: check batches_dir in config.json"; return; }
  const h = hashState();
  const want = keepId ?? h.b ?? lsGet("batch", null);
  const b = d.batches.find((x) => x.id === want || x.name === want) || d.batches[0];
  sel.value = b.id;
  const idx = keepId !== undefined ? S.idx : (h.b !== undefined ? (h.i ?? 0) : lsGet("idx:" + b.name, 0));
  await openBatch(b.id, idx);
}

async function openBatch(id, idx) {
  let d;
  try { d = await api(`/api/batch?id=${encodeURIComponent(id)}&live=${S.live ? 1 : 0}`); }
  catch (e) {
    if (S.live) { S.live = false; setSrcButtons(); toast("Live Scalabel failed (" + e.message + "), showing the export file"); return openBatch(id, idx); }
    toast("Cannot open batch: " + e.message); return;
  }
  S.batch = d;
  lsSet("batch", d.name);
  $("#names").innerHTML = d.frames.map((f) => `<option value="${esc(f.short)}">`).join("");
  const n = d.frames.length;
  $("#slider").max = Math.max(1, n); $("#jump").max = Math.max(1, n);
  $("#total").textContent = "/ " + n;
  renderCats();
  await go(Math.min(Math.max(0, idx | 0), Math.max(0, n - 1)), true);
}

// ---------- categories ----------
function rgb(c) { return `rgb(${c[0]},${c[1]},${c[2]})`; }
function renderCats() {
  const counts = {};
  for (const l of (S.frame && S.frame.labels) || []) counts[l.category] = (counts[l.category] || 0) + 1;
  $("#cats").innerHTML = (S.batch ? S.batch.categories : []).map((c, i) =>
    `<div class="cat${S.hidden.has(c.name) ? " off" : ""}" data-cat="${esc(c.name)}" title="${esc(c.name)}">`
    + `<span class="key">${i < 9 ? i + 1 : ""}</span><input type="checkbox"${S.hidden.has(c.name) ? "" : " checked"} tabindex="-1">`
    + `<span class="sw" style="background:${rgb(c.color)}"></span><span class="nm">${esc(c.display)}</span>`
    + `<span class="ct">${counts[c.name] || 0}</span></div>`).join("");
}
function toggleCat(name, on) {
  if (on === undefined) on = S.hidden.has(name);
  if (on) S.hidden.delete(name); else S.hidden.add(name);
  lsSet("hidden", [...S.hidden]);
  renderCats(); applyHidden();
}
function applyHidden() {
  for (const g of $("#paneR svg").querySelectorAll("g[data-cat]")) g.style.display = S.hidden.has(g.dataset.cat) ? "none" : "";
}

// ---------- one frame ----------
function pathD(poly) { // Scalabel poly2d: "C" vertices are cubic Bezier control points
  const v = poly.vertices, t = poly.types || "", n = v.length;
  if (!n) return "";
  const p = (k) => v[k % n][0] + " " + v[k % n][1];
  let d = "M" + p(0), i = 1;
  while (i < n) {
    if (t[i] === "C") { d += " C" + p(i) + " " + p(i + 1) + " " + p(i + 2); i += 3; }
    else { d += " L" + p(i); i += 1; }
  }
  return poly.closed ? d + " Z" : d;
}
function renderLabels() {
  const svg = $("#paneR svg");
  const cat = {};
  (S.batch ? S.batch.categories : []).forEach((c) => { cat[c.name] = c; });
  const w = +$("#lineW").value, op = +$("#lineO").value, showV = $("#showVerts").checked;
  const groups = {};
  for (const l of (S.frame && S.frame.labels) || []) {
    const c = cat[l.category] || { color: [255, 255, 255], display: l.category };
    const col = rgb(c.color);
    let s = "";
    for (const poly of l.poly2d) {
      s += `<path d="${pathD(poly)}" stroke="${col}" stroke-opacity="${op}"><title>${esc(c.display)} · ${esc(l.id)}</title></path>`;
      if (showV) {
        const t = poly.types || "";
        poly.vertices.forEach((pt, k) => {
          const ctl = t[k] === "C";
          s += `<circle class="v" cx="${pt[0]}" cy="${pt[1]}" fill="${ctl ? "#fff" : col}" stroke="#000" stroke-width="${0.6 / S.view.s}" r="${(ctl ? 2.5 : 3.5) / S.view.s}" data-r="${ctl ? 2.5 : 3.5}"/>`;
        });
      }
    }
    (groups[l.category] = groups[l.category] || []).push(s);
  }
  S.lineW = w;
  svg.setAttribute("stroke-width", w / S.view.s); // constant on screen; applyView keeps it so
  svg.innerHTML = Object.entries(groups).map(([k, v]) => `<g data-cat="${esc(k)}">${v.join("")}</g>`).join("");
  svg.classList.toggle("nolines", !$("#showLines").checked);
  applyHidden();
}

function taskItem(link) {
  try { const u = new URL(link); return `task ${u.searchParams.get("task_index")} · item ${u.searchParams.get("item")}`; }
  catch (e) { return ""; }
}

function renderInfo() {
  const f = S.frame, n = S.batch.frames.length;
  $("#pos").textContent = `Image ${f.index + 1} of ${n}`;
  $("#fname").textContent = f.name.split("/").pop();
  $("#fname").title = f.name + " (click to copy)";
  $("#openSc").href = f.scalabel;
  $("#taskItem").textContent = taskItem(f.scalabel);
  $("#taskItem").title = "Scalabel project " + S.batch.project;
  if (f.geo) {
    const lat = f.geo.lat.toFixed(7).replace(/0+$/, "").replace(/\.$/, ""), lon = f.geo.lon.toFixed(7).replace(/0+$/, "").replace(/\.$/, "");
    // click a value to copy it; the button copies both
    $("#geo").innerHTML = `<span class="geo"><span class="val mono copyable" data-copy="${lat}" data-what="latitude" title="Latitude, click to copy">${lat}</span>`
      + `<span class="val mono copyable" data-copy="${lon}" data-what="longitude" title="Longitude, click to copy">${lon}</span>`
      + `<button class="small" data-copy="${lat}, ${lon}" data-what="lat, lon" title="Copy lat, lon (L)">Copy</button>`
      + `<a class="btn" href="${esc(f.geo.maps)}" target="_blank" rel="noopener" title="Google Maps (M)">Maps</a>`
      + `<a class="btn" href="${esc(f.geo.streetview)}" target="_blank" rel="noopener" title="Street View (S)">Street View</a></span>`;
  } else {
    $("#geo").innerHTML = `<span class="muted" title="No row for this image in the lat/lon CSV">no lat/lon</span>`;
  }
  $("#cad").innerHTML = f.cad
    ? `<a class="btn" href="${esc(f.cad.url)}" target="_blank" rel="noopener" title="CAD image ${esc(f.cad.file)}">CAD</a>`
    : `<span class="muted">no CAD</span>`;
  $("#jump").value = f.index + 1; $("#slider").value = f.index + 1;
  renderCats();
}

async function go(i, fresh) {
  if (!S.batch || !S.batch.frames.length) return;
  const n = S.batch.frames.length;
  i = Math.min(Math.max(0, i), n - 1);
  const tok = ++S.token;
  let f;
  try { f = await api(`/api/frame?id=${encodeURIComponent(S.batch.id)}&i=${i}&live=${S.live ? 1 : 0}`); }
  catch (e) { toast("Cannot load image " + (i + 1) + ": " + e.message); return; }
  if (tok !== S.token) return; // a later request won
  S.frame = f; S.idx = i;
  lsSet("idx:" + S.batch.name, i);
  history.replaceState(null, "", `#b=${encodeURIComponent(S.batch.name)}&i=${i + 1}`);
  renderInfo();
  const imgs = document.querySelectorAll(".stage img");
  const keep = $("#keepZoom").checked && !fresh;
  if (imgs[0].getAttribute("src") === f.image) { renderLabels(); return; }
  $("#loading").classList.add("on");
  const pending = new Image();
  pending.onload = () => {
    if (tok !== S.token) return;
    const sameSize = pending.naturalWidth === S.W && pending.naturalHeight === S.H;
    S.W = pending.naturalWidth; S.H = pending.naturalHeight;
    imgs.forEach((im) => { im.src = f.image; im.width = S.W; im.height = S.H; });
    const svg = $("#paneR svg");
    svg.setAttribute("width", S.W); svg.setAttribute("height", S.H); svg.setAttribute("viewBox", `0 0 ${S.W} ${S.H}`);
    applyLayout();
    if (!(keep && sameSize)) fit();
    renderLabels(); applyView();
    $("#loading").classList.remove("on");
    preload(i + 1); preload(i - 1);
  };
  pending.onerror = () => { if (tok === S.token) { $("#loading").classList.remove("on"); toast("Image not found: " + f.name); } };
  pending.src = f.image;
}
const preloaded = new Map();
async function preload(i) {
  if (!S.batch || i < 0 || i >= S.batch.frames.length) return;
  const key = S.batch.id + ":" + i + ":" + S.live;
  if (preloaded.has(key)) return;
  preloaded.set(key, 1);
  if (preloaded.size > 40) preloaded.delete(preloaded.keys().next().value);
  try { const f = await api(`/api/frame?id=${encodeURIComponent(S.batch.id)}&i=${i}&live=${S.live ? 1 : 0}`); new Image().src = f.image; }
  catch (e) { /* only a cache warm-up */ }
}

// ---------- synced zoom / pan ----------
function paneSize() { const r = $("#paneL").getBoundingClientRect(); return [r.width, r.height]; }
// side by side or top and bottom: "auto" picks the one that shows the image larger
function applyLayout() {
  const mode = $("#layout").value, p = $("#panes").getBoundingClientRect();
  let col = mode === "col";
  if (mode === "auto" && S.W && S.H) {
    const g = 3, row = Math.min((p.width - g) / 2 / S.W, p.height / S.H);
    col = Math.min(p.width / S.W, (p.height - g) / 2 / S.H) > row;
  }
  $("#panes").classList.toggle("col", col);
}
function relayout() { applyLayout(); fit(); }
function fit() {
  const [pw, ph] = paneSize();
  if (!S.W || !S.H) return;
  const s = Math.min(pw / S.W, ph / S.H) * 0.98;
  S.view = { s, tx: (pw - S.W * s) / 2, ty: (ph - S.H * s) / 2 };
  applyView();
}
let rafV = 0;
function applyView() {
  const v = S.view, tf = `translate(${v.tx}px, ${v.ty}px) scale(${v.s})`;
  document.querySelectorAll(".stage").forEach((st) => { st.style.transform = tf; });
  $("#zoomPct").textContent = Math.round(v.s * 1000) / 10 + "%";
  cancelAnimationFrame(rafV);
  rafV = requestAnimationFrame(() => { // keep lines and vertex dots a constant size on screen
    // (vector-effect: non-scaling-stroke ignores the CSS transform, so scale by hand)
    $("#paneR svg").setAttribute("stroke-width", (S.lineW || 2.5) / v.s);
    for (const c of $("#paneR svg").querySelectorAll("circle.v")) {
      c.setAttribute("r", (+c.dataset.r) / v.s); c.setAttribute("stroke-width", 0.6 / v.s);
    }
  });
}
function zoomAt(px, py, f) {
  const v = S.view, ns = Math.min(Math.max(v.s * f, 0.005), 40);
  S.view = { s: ns, tx: px - (px - v.tx) * (ns / v.s), ty: py - (py - v.ty) * (ns / v.s) };
  applyView();
}
function zoomCenter(f) { const [pw, ph] = paneSize(); zoomAt(pw / 2, ph / 2, f); }
function setupPanes() {
  for (const pane of document.querySelectorAll(".pane")) {
    pane.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = pane.getBoundingClientRect();
      const k = e.ctrlKey ? 0.01 : 0.0018; // trackpad pinch sends ctrl+wheel
      zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * k));
    }, { passive: false });
    let drag = null;
    pane.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      drag = { x: e.clientX, y: e.clientY, tx: S.view.tx, ty: S.view.ty };
      pane.setPointerCapture(e.pointerId); pane.classList.add("drag");
    });
    pane.addEventListener("pointermove", (e) => {
      if (!drag) return;
      S.view.tx = drag.tx + e.clientX - drag.x; S.view.ty = drag.ty + e.clientY - drag.y; applyView();
    });
    const end = () => { drag = null; pane.classList.remove("drag"); };
    pane.addEventListener("pointerup", end); pane.addEventListener("pointercancel", end);
    pane.addEventListener("dblclick", (e) => { const r = pane.getBoundingClientRect(); zoomAt(e.clientX - r.left, e.clientY - r.top, 2); });
  }
  window.addEventListener("resize", relayout);
}

// ---------- url hash ----------
function hashState() {
  const h = new URLSearchParams(location.hash.slice(1));
  const out = {};
  if (h.get("b")) { const b = S.batches.find((x) => x.name === h.get("b") || x.id === h.get("b")); if (b) out.b = b.id; }
  if (h.get("i") && /^\d+$/.test(h.get("i"))) out.i = Math.max(0, +h.get("i") - 1);
  return out;
}

// ---------- wiring ----------
function setSrcButtons() { $("#srcFile").classList.toggle("on", !S.live); $("#srcLive").classList.toggle("on", S.live); }
function wire() {
  $("#batch").onchange = () => { const b = S.batches.find((x) => x.id === $("#batch").value); openBatch(b.id, lsGet("idx:" + b.name, 0)); };
  $("#srcFile").onclick = () => { if (S.live) { S.live = false; setSrcButtons(); openBatch(S.batch.id, S.idx); } };
  $("#srcLive").onclick = () => { S.live = true; setSrcButtons(); toast("Labels from Scalabel (live)"); openBatch(S.batch.id, S.idx); };
  $("#refresh").onclick = async () => { await api("/api/reload"); preloaded.clear(); await loadBatches(S.batch ? S.batch.id : undefined); toast("Reloaded"); };
  $("#prev").onclick = () => go(S.idx - 1);
  $("#next").onclick = () => go(S.idx + 1);
  $("#slider").oninput = () => { $("#jump").value = $("#slider").value; };
  $("#slider").onchange = () => go(+$("#slider").value - 1);
  $("#jump").onkeydown = (e) => { if (e.key === "Enter") { go(+$("#jump").value - 1); $("#jump").blur(); } };
  $("#search").onchange = () => {
    const q = $("#search").value.trim().toLowerCase();
    if (!q || !S.batch) return;
    const k = S.batch.frames.findIndex((f) => f.short.toLowerCase() === q);
    const j = k >= 0 ? k : S.batch.frames.findIndex((f) => f.name.toLowerCase().includes(q));
    if (j >= 0) { go(j); $("#search").blur(); } else toast("No image matches " + q);
  };
  $("#cats").onclick = (e) => { const c = e.target.closest(".cat"); if (c) toggleCat(c.dataset.cat); };
  $("#catAll").onclick = () => { S.hidden.clear(); lsSet("hidden", []); renderCats(); applyHidden(); };
  $("#catNone").onclick = () => { (S.batch ? S.batch.categories : []).forEach((c) => S.hidden.add(c.name)); lsSet("hidden", [...S.hidden]); renderCats(); applyHidden(); };
  for (const id of ["#showLines", "#showVerts", "#lineW", "#lineO"]) $(id).addEventListener("input", () => { lsSet(id, $(id).type === "checkbox" ? $(id).checked : $(id).value); renderLabels(); });
  $("#keepZoom").onchange = () => lsSet("#keepZoom", $("#keepZoom").checked);
  $("#layout").onchange = () => { lsSet("#layout", $("#layout").value); relayout(); };
  $("#sideBtn").onclick = toggleSide;
  $("#helpBtn").onclick = () => { $("#help").hidden = !$("#help").hidden; };
  $("#bright").oninput = () => { lsSet("#bright", $("#bright").value); document.querySelectorAll(".stage img").forEach((im) => { im.style.filter = `brightness(${$("#bright").value})`; }); };
  $("#zIn").onclick = () => zoomCenter(1.4); $("#zOut").onclick = () => zoomCenter(1 / 1.4);
  $("#zFit").onclick = fit;
  $("#z100").onclick = () => { const [pw, ph] = paneSize(); zoomAt(pw / 2, ph / 2, 1 / S.view.s); };
  $("#fname").onclick = () => S.frame && copyText(S.frame.name.split("/").pop(), "image name");
  $("#geo").onclick = (e) => { const b = e.target.closest("[data-copy]"); if (b) copyText(b.dataset.copy, b.dataset.what); };
  document.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t.tagName === "INPUT" && t.type !== "checkbox" && t.type !== "range" || t.tagName === "SELECT" || t.isContentEditable) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const k = e.key.toLowerCase();
    if (k === "arrowright" || k === "d") { e.preventDefault(); go(S.idx + 1); }
    else if (k === "arrowleft" || k === "a") { e.preventDefault(); go(S.idx - 1); }
    else if (k === "home") go(0);
    else if (k === "end") go(S.batch.frames.length - 1);
    else if (k === "g") { e.preventDefault(); $("#jump").focus(); $("#jump").select(); }
    else if (k === "f") fit();
    else if (k === "+" || k === "=") zoomCenter(1.4);
    else if (k === "-" || k === "_") zoomCenter(1 / 1.4);
    else if (k === "o" && S.frame) window.open(S.frame.scalabel, "_blank");
    else if (k === "m" && S.frame && S.frame.geo) window.open(S.frame.geo.maps, "_blank");
    else if (k === "s" && S.frame && S.frame.geo) window.open(S.frame.geo.streetview, "_blank");
    else if (k === "l" && S.frame && S.frame.geo) copyText(`${S.frame.geo.lat}, ${S.frame.geo.lon}`, "lat, lon");
    else if (k === "h") { $("#showLines").checked = !$("#showLines").checked; renderLabels(); }
    else if (k === "b") toggleSide();
    else if (k === "v") { const o = ["auto", "row", "col"]; $("#layout").value = o[(o.indexOf($("#layout").value) + 1) % 3]; $("#layout").onchange(); toast("Layout: " + $("#layout").selectedOptions[0].text); }
    else if (k === "?" || k === "escape") { $("#help").hidden = k === "escape" ? true : !$("#help").hidden; }
    else if (/^[1-9]$/.test(k) && S.batch) { const c = S.batch.categories[+k - 1]; if (c) toggleCat(c.name); }
  });
  window.addEventListener("hashchange", () => {
    const h = hashState();
    if (h.b !== undefined && (!S.batch || h.b !== S.batch.id)) { $("#batch").value = h.b; openBatch(h.b, h.i || 0); }
    else if (h.i !== undefined && h.i !== S.idx) go(h.i);
  });
}
function toggleSide() {
  document.body.classList.toggle("noside");
  lsSet("noside", document.body.classList.contains("noside"));
  requestAnimationFrame(relayout);
}
function restorePrefs() {
  if (lsGet("noside", false)) document.body.classList.add("noside");
  { const v = lsGet("#layout", null); if (v) $("#layout").value = v; }
  S.hidden = new Set(lsGet("hidden", []));
  for (const id of ["#showLines", "#showVerts", "#keepZoom"]) { const v = lsGet(id, null); if (v !== null) $(id).checked = v; }
  for (const id of ["#lineW", "#lineO", "#bright"]) { const v = lsGet(id, null); if (v !== null) $(id).value = v; }
  document.querySelectorAll(".stage img").forEach((im) => { im.style.filter = `brightness(${$("#bright").value})`; });
}

restorePrefs(); wire(); setupPanes();
loadBatches().catch((e) => { $("#status").textContent = "Cannot reach the server: " + e.message; });
