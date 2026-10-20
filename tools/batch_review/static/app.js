"use strict";
// Batch Review page: one batch at a time, original | annotated side by side, synced zoom.
const $ = (s) => document.querySelector(s);
const S = {
  batches: [], batch: null, idx: 0, frame: null, token: 0,
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
  $("#status").textContent = `${d.batches.length} projects · ${d.geo_rows} lat/lon rows · ${d.cad_images} CAD images`
    + (d.warnings.length ? ` · ${d.warnings.length} warning(s), see server log` : "");
  $("#status").title = d.warnings.join("\n");
  if (d.error) { $("#status").textContent = d.error + " · start Scalabel, then Refresh"; return; }
  if (!d.batches.length) { $("#status").textContent = "Scalabel lists no projects (check projects / hide_projects in config.json)"; return; }
  const h = hashState();
  const want = keepId ?? h.b ?? lsGet("batch", null);
  const b = d.batches.find((x) => x.id === want || x.name === want) || d.batches[0];
  sel.value = b.id;
  const idx = keepId !== undefined ? S.idx : (h.b !== undefined ? (h.i ?? 0) : lsGet("idx:" + b.name, 0));
  await openBatch(b.id, idx);
}

async function openBatch(id, idx) {
  let d;
  try { d = await api(`/api/batch?id=${encodeURIComponent(id)}`); }
  catch (e) { toast("Cannot get " + id + " from Scalabel: " + e.message); return; }
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
// ---------- drawing ----------
// One screen-sized canvas per pane, like Scalabel. Full-size image and overlay layers of these
// ~6000 x 13000 px images need hundreds of MB of GPU memory each and made Chrome drop tiles.
const LOW_MAX = 4096; // longest side of the zoomed-out copy
let paths = []; // one entry per poly: {cat, display, id, color, path: Path2D, verts}
function buildPaths() {
  const cat = {};
  (S.batch ? S.batch.categories : []).forEach((c) => { cat[c.name] = c; });
  paths = [];
  for (const l of (S.frame && S.frame.labels) || []) {
    const c = cat[l.category] || { color: [255, 255, 255], display: l.category };
    for (const poly of l.poly2d) {
      const d = pathD(poly);
      if (!d) continue;
      const t = poly.types || "";
      paths.push({ cat: l.category, display: c.display, id: l.id, color: rgb(c.color), path: new Path2D(d),
        verts: poly.vertices.map((p, k) => [p[0], p[1], t[k] === "C"]) });
    }
  }
  S.hover = null;
}
function renderLabels() { buildPaths(); draw(); }
function applyHidden() { if (S.hover && S.hidden.has(S.hover.cat)) S.hover = null; draw(); }

function makeLow(img) { // a smaller copy, drawn when zoomed out (fast, and smoother)
  const k = Math.min(1, LOW_MAX / Math.max(img.naturalWidth, img.naturalHeight));
  if (k >= 1) return null;
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(img.naturalWidth * k)); c.height = Math.max(1, Math.round(img.naturalHeight * k));
  const x = c.getContext("2d");
  x.imageSmoothingQuality = "high";
  x.drawImage(img, 0, 0, c.width, c.height);
  return { src: c, k: c.width / img.naturalWidth };
}

let rafD = 0;
function draw() { cancelAnimationFrame(rafD); rafD = requestAnimationFrame(drawNow); }
function drawNow() {
  const dpr = window.devicePixelRatio || 1, v = S.view, bright = +$("#bright").value;
  for (const pane of [$("#paneL"), $("#paneR")]) {
    const cv = pane.querySelector("canvas"), r = pane.getBoundingClientRect();
    const cw = Math.max(1, Math.round(r.width * dpr)), ch = Math.max(1, Math.round(r.height * dpr));
    if (cv.width !== cw || cv.height !== ch) { cv.width = cw; cv.height = ch; }
    const x = cv.getContext("2d");
    x.setTransform(1, 0, 0, 1, 0, 0);
    x.fillStyle = "#000"; x.fillRect(0, 0, cw, ch);
    if (!S.img) continue;
    x.setTransform(dpr * v.s, 0, 0, dpr * v.s, dpr * v.tx, dpr * v.ty);
    // only the visible part of the image (image px)
    const x0 = Math.max(0, -v.tx / v.s), y0 = Math.max(0, -v.ty / v.s);
    const x1 = Math.min(S.W, (r.width - v.tx) / v.s), y1 = Math.min(S.H, (r.height - v.ty) / v.s);
    if (x1 > x0 && y1 > y0) {
      const src = S.low && v.s * dpr <= S.low.k * 1.25 ? S.low : { src: S.img, k: 1 };
      x.imageSmoothingEnabled = true; x.imageSmoothingQuality = "high";
      if ("filter" in x) x.filter = bright !== 1 ? `brightness(${bright})` : "none";
      x.drawImage(src.src, x0 * src.k, y0 * src.k, (x1 - x0) * src.k, (y1 - y0) * src.k, x0, y0, x1 - x0, y1 - y0);
      if ("filter" in x) x.filter = "none";
    }
    if (pane.id === "paneR" && $("#showLines").checked) drawLines(x, v.s);
  }
}
function drawLines(x, s) {
  const w = +$("#lineW").value, op = +$("#lineO").value, showV = $("#showVerts").checked;
  x.lineCap = "round"; x.lineJoin = "round";
  x.globalAlpha = op; x.lineWidth = w / s;
  for (const p of paths) if (!S.hidden.has(p.cat)) { x.strokeStyle = p.color; x.stroke(p.path); }
  x.globalAlpha = 1;
  if (showV) {
    x.lineWidth = 0.8 / s; x.strokeStyle = "#000";
    for (const p of paths) {
      if (S.hidden.has(p.cat)) continue;
      for (const [vx, vy, ctl] of p.verts) {
        x.beginPath(); x.arc(vx, vy, (ctl ? 2.5 : 3.5) / s, 0, 2 * Math.PI);
        x.fillStyle = ctl ? "#fff" : p.color; x.fill(); x.stroke();
      }
    }
  }
  if (S.hover) { // the line under the mouse: white halo
    x.lineWidth = (w + 4) / s; x.strokeStyle = "#fff"; x.stroke(S.hover.path);
    x.lineWidth = w / s; x.strokeStyle = S.hover.color; x.stroke(S.hover.path);
  }
}
const hitCtx = document.createElement("canvas").getContext("2d");
function hitTest(ix, iy) { // the topmost visible line within a few screen px of image point (ix, iy)
  if (!$("#showLines").checked) return null;
  hitCtx.lineWidth = Math.max(+$("#lineW").value, 8) / S.view.s;
  for (let k = paths.length - 1; k >= 0; k--) {
    const p = paths[k];
    if (!S.hidden.has(p.cat) && hitCtx.isPointInStroke(p.path, ix, iy)) return p;
  }
  return null;
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
  try { f = await api(`/api/frame?id=${encodeURIComponent(S.batch.id)}&i=${i}`); }
  catch (e) { toast("Cannot load image " + (i + 1) + ": " + e.message); return; }
  if (tok !== S.token) return; // a later request won
  S.frame = f; S.idx = i;
  lsSet("idx:" + S.batch.name, i);
  history.replaceState(null, "", `#b=${encodeURIComponent(S.batch.name)}&i=${i + 1}`);
  renderInfo();
  const keep = $("#keepZoom").checked && !fresh;
  if (S.imgUrl === f.image) { renderLabels(); return; }
  $("#loading").classList.add("on");
  const pending = new Image();
  pending.onload = () => {
    if (tok !== S.token) return;
    const sameSize = pending.naturalWidth === S.W && pending.naturalHeight === S.H;
    S.W = pending.naturalWidth; S.H = pending.naturalHeight;
    S.img = pending; S.imgUrl = f.image; S.low = makeLow(pending);
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
  const key = S.batch.id + ":" + i;
  if (preloaded.has(key)) return;
  preloaded.set(key, 1);
  if (preloaded.size > 40) preloaded.delete(preloaded.keys().next().value);
  try { const f = await api(`/api/frame?id=${encodeURIComponent(S.batch.id)}&i=${i}`); new Image().src = f.image; }
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
function applyView() {
  $("#zoomPct").textContent = Math.round(S.view.s * 1000) / 10 + "%";
  draw();
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
      // As in Scalabel (viewer2d onWheel): a horizontal wheel (trackpad side-swipe, tilt
      // wheel, side-scroll button) or Shift + wheel scrolls the image left / right, 1 px per
      // unit of delta; the vertical wheel, Ctrl/Cmd + wheel and pinch zoom.
      if (!e.ctrlKey && !e.metaKey) {
        const h = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.shiftKey ? e.deltaY : 0;
        if (h !== 0) { S.view.tx -= h * (e.deltaMode === 1 ? 16 : 1); applyView(); return; }
      }
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
      if (!drag) { hover(pane, e); return; }
      S.view.tx = drag.tx + e.clientX - drag.x; S.view.ty = drag.ty + e.clientY - drag.y; applyView();
    });
    const end = () => { drag = null; pane.classList.remove("drag"); };
    pane.addEventListener("pointerup", end); pane.addEventListener("pointercancel", end);
    pane.addEventListener("pointerleave", () => { $("#tip").hidden = true; if (S.hover) { S.hover = null; draw(); } });
    pane.addEventListener("dblclick", (e) => { const r = pane.getBoundingClientRect(); zoomAt(e.clientX - r.left, e.clientY - r.top, 2); });
  }
  window.addEventListener("resize", relayout);
}

let hoverBusy = false;
function hover(pane, e) {
  if (hoverBusy) return;
  hoverBusy = true;
  requestAnimationFrame(() => {
    hoverBusy = false;
    const r = pane.getBoundingClientRect();
    const h = hitTest((e.clientX - r.left - S.view.tx) / S.view.s, (e.clientY - r.top - S.view.ty) / S.view.s);
    const tip = $("#tip");
    if (h) {
      tip.textContent = `${h.display} · ${h.id}`;
      tip.style.left = e.clientX + 14 + "px"; tip.style.top = e.clientY + 14 + "px"; tip.hidden = false;
    } else tip.hidden = true;
    if (h !== S.hover) { S.hover = h; draw(); }
  });
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
function wire() {
  $("#batch").onchange = () => { const b = S.batches.find((x) => x.id === $("#batch").value); openBatch(b.id, lsGet("idx:" + b.name, 0)); };
  $("#refresh").onclick = async () => { await api("/api/reload"); preloaded.clear(); await loadBatches(S.batch ? S.batch.id : undefined); toast("Latest labels from Scalabel"); };
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
  $("#bright").oninput = () => { lsSet("#bright", $("#bright").value); draw(); };
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
}

restorePrefs(); wire(); setupPanes();
loadBatches().catch((e) => { $("#status").textContent = "Cannot reach the server: " + e.message; });
