#!/usr/bin/env node
"use strict";
/**
 * Batch Review: browse the frames of Scalabel projects one batch (project) at a time. Projects,
 * labels and images come live from the Scalabel server.
 *
 * Node 16+ built-in modules only: nothing to install. Run
 *     node server.js                    (uses config.json next to this file)
 *     node server.js --config my.json   (or another config; --port / --host override it)
 * then open http://localhost:8687. README.md lists the config keys.
 */
const fs = require("fs");
const path = require("path");
const http = require("http");
const https = require("https");

const HERE = __dirname;
const STATIC = path.join(HERE, "static");
const DEFAULTS = {
  port: 8687, host: "127.0.0.1", scalabel_url: "http://localhost:8686", task_size: 50,
  projects: [], // when not empty: only these Scalabel projects, in this order
  hide_projects: "_fix_|^zz_", // regular expression: projects to leave out of the list
  live_cache_seconds: 30, // how long a project's labels are reused before asking Scalabel again
  image_root: null, // optional local folder the frame urls start from (else images load from Scalabel)
  cad_dir: null, geo_csv: [],
  manual_csv: "manual_locations.csv", // lat/lon typed in on the page (wins over geo_csv)
};
const MAX_UPLOAD = 50 * 1024 * 1024; // bytes

// Scalabel's fixed category colours (app/src/drawable/util.ts CATEGORY_NAME_COLOR_MAP), then
// its index palette (CATEGORY_COLOR_PALETTE) for any other category.
const NAME_COLORS = {
  curb_road_edge: [210, 50, 50], without_curb_road_edge: [50, 200, 50],
  continuous_white_line: [255, 215, 0], dashed_white_line: [100, 180, 255],
  double_yellow_line: [102, 51, 0], center_turn_lane: [255, 140, 0], solid_single_line: [255, 215, 0],
};
const INDEX_COLORS = [[255, 215, 0], [255, 140, 0], [102, 51, 0], [100, 180, 255], [255, 80, 0],
  [210, 50, 50], [0, 200, 170], [180, 60, 220], [50, 200, 50], [255, 100, 180]];
// sidebar names in this Scalabel fork (app/src/components/toolbar_category.tsx)
const DISPLAY_NAMES = { continuous_white_line: "continuous_line", dashed_white_line: "dashed_line" };
const MIME = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8", ".json": "application/json", ".png": "image/png",
  ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
  ".tif": "image/tiff", ".tiff": "image/tiff", ".svg": "image/svg+xml",
};
const IMAGE_EXT = new Set([".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"]);

function categoryColor(name, index) {
  return NAME_COLORS[name] || INDEX_COLORS[index % INDEX_COLORS.length];
}

/** Image id that matches a frame with its CAD image and its CSV row: the basename without
 *  extension, a trailing _z<N>, or a trailing input/verification; separators unified, lower
 *  case ("854_verification.png" -> "854"). */
function norm(filename) {
  let s = path.posix.basename(String(filename).replace(/\\/g, "/"));
  s = s.replace(/\.[^.]*$/, "");
  s = s.replace(/_z\d+$/i, "");
  s = s.replace(/[_\s-]*(input|verification)$/i, "");
  return s.trim().replace(/[\s_-]+/g, "_").toLowerCase();
}

/** Deep link to item ``index`` (0-based, export order) of a Scalabel project. ``item`` is
 *  1-based within the task, as this Scalabel fork's deep link expects. */
function scalabelLink(base, project, index, taskSize) {
  const size = taskSize > 0 ? taskSize : 50;
  const q = new URLSearchParams({ project_name: project, task_index: String(Math.floor(index / size)),
    item: String((index % size) + 1) });
  return `${String(base).replace(/\/+$/, "")}/label?${q}`;
}

function mapsLinks(lat, lon) {
  const ll = `${lat},${lon}`;
  return {
    maps: `https://www.google.com/maps/search/?api=1&query=${ll}`,
    streetview: `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${ll}`,
  };
}

/** Rows of a CSV text (quoted fields, "" escapes, CRLF), with the delimiter that splits the
 *  header into the most fields among , ; and tab. */
function parseCsv(text) {
  text = text.replace(/^﻿/, "");
  const head = text.split(/\r?\n/, 1)[0] || "";
  const delim = [",", ";", "\t"].reduce((a, d) => (head.split(d).length > head.split(a).length ? d : a), ",");
  const rows = [];
  let row = [], field = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') q = false;
      else field += c;
    } else if (c === '"') q = true;
    else if (c === delim) { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); field = "";
      if (row.length > 1 || row[0] !== "") rows.push(row);
      row = [];
    } else field += c;
  }
  if (field !== "" || row.length) { row.push(field); if (row.length > 1 || row[0] !== "") rows.push(row); }
  return rows;
}

function pick(headers, names, contains) {
  const low = headers.map((h) => h.trim().toLowerCase());
  for (const n of names) { const k = low.indexOf(n); if (k >= 0) return k; }
  for (let k = 0; k < low.length; k++) if (contains.some((c) => low[k].includes(c))) return k;
  return -1;
}

/** {norm(image): [lat, lon]} from CSV text with a header row. The image column is the first
 *  header named image / image_name / name / file / filename (else containing image, name or
 *  file); lat is lat / latitude (else containing "lat"), lon is lon / lng / long / longitude
 *  (else containing "lon" or "lng"). Bad rows are skipped with a warning, never fatal. */
function readGeoCsv(text, label) {
  const out = {}, warn = [], names = {};
  const rows = parseCsv(text);
  if (!rows.length) return { geo: out, names, warn: [`${label}: empty`] };
  const h = rows[0];
  const ic = pick(h, ["image", "image_name", "name", "file", "filename"], ["image", "name", "file"]);
  const la = pick(h, ["lat", "latitude"], ["lat"]);
  const lo = pick(h, ["lon", "lng", "long", "longitude"], ["lon", "lng"]);
  if (ic < 0 || la < 0 || lo < 0) return { geo: out, names, warn: [`${label}: need image, lat and lon columns; header is ${JSON.stringify(h)}`] };
  rows.slice(1).forEach((r, k) => {
    const key = norm(r[ic] || ""), lat = parseFloat(r[la]), lon = parseFloat(r[lo]);
    if (!key || !Number.isFinite(lat) || !Number.isFinite(lon)) warn.push(`${label}:${k + 2}: skipped (bad row)`);
    else if (Math.abs(lat) > 90 || Math.abs(lon) > 180) warn.push(`${label}:${k + 2}: skipped (lat/lon out of range)`);
    else { out[key] = [lat, lon]; names[key] = String(r[ic]).trim(); }
  });
  return { geo: out, names, warn };
}

/** {norm(name): absolute path} of every image under ``dir`` (any depth; first one wins). */
/** "lat, lon" from what a person types or pastes: "33.77, -118.19", "33.77 -118.19", or a
 *  Google Maps link (".../@33.77,-118.19,17z", "?q=33.77,-118.19", "query=", "viewpoint=").
 *  null when it is not a valid pair. */
function parseLatLon(text) {
  const t = String(text || "").trim();
  const m = t.match(/@(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)/)
    || t.match(/[?&](?:q|query|ll|viewpoint|destination)=(-?\d+(?:\.\d+)?)(?:,|%2C)\s*(-?\d+(?:\.\d+)?)/i)
    || t.match(/^\s*\(?\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*\)?\s*$/);
  if (!m) return null;
  const lat = parseFloat(m[1]), lon = parseFloat(m[2]);
  return Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? [lat, lon] : null;
}

const csvCell = (v) => (/[",\r\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));

function writeAtomic(p, text) {
  const tmp = `${p}.tmp-${process.pid}`;
  fs.writeFileSync(tmp, text, "utf8");
  fs.renameSync(tmp, p);
}

function stamp() { return new Date().toISOString().replace(/[-:]/g, "").replace(/\..*/, "").replace("T", "-"); }

function readBody(req, limit) {
  return new Promise((resolve, reject) => {
    const chunks = []; let n = 0;
    req.on("data", (c) => { n += c.length; if (n > limit) { reject(new Error("upload too large")); req.destroy(); } else chunks.push(c); });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

function cadIndex(dir) {
  const idx = {};
  if (!dir || !isDir(dir)) return idx;
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (IMAGE_EXT.has(path.extname(e.name).toLowerCase())) { const k = norm(e.name); if (!(k in idx)) idx[k] = p; }
    }
  };
  walk(dir);
  return idx;
}

function isDir(p) { try { return fs.statSync(p).isDirectory(); } catch (e) { return false; } }
function isFile(p) { try { return fs.statSync(p).isFile(); } catch (e) { return false; } }

/** ``root/rel`` when it stays inside ``root`` (no .. escapes), else null. */
function inside(root, rel) {
  if (!root) return null;
  const r = path.resolve(root);
  const p = path.resolve(r, String(rel || "").replace(/^[\\/]+/, ""));
  return p === r || p.startsWith(r + path.sep) ? p : null;
}

const framesOf = (data) => (Array.isArray(data) ? data : (data && data.frames) || []);
const slim = (l) => ({
  id: l.id, category: l.category,
  poly2d: (l.poly2d || []).map((p) => ({ vertices: p.vertices || [], types: p.types || "", closed: !!p.closed })),
});

function getJson(url, timeoutMs) {
  return new Promise((resolve, reject) => {
    const mod = url.startsWith("https:") ? https : http;
    const req = mod.get(url, (res) => {
      if (res.statusCode !== 200) { res.resume(); reject(new Error(`HTTP ${res.statusCode} from ${url}`)); return; }
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => { try { resolve(JSON.parse(Buffer.concat(chunks).toString("utf8"))); } catch (e) { reject(e); } });
    });
    req.setTimeout(timeoutMs || 60000, () => req.destroy(new Error("timeout")));
    req.on("error", reject);
  });
}

class Store {
  constructor(cfg, base) {
    this.cfg = { ...DEFAULTS, ...cfg };
    this.base = base;
    this.live = new Map(); // project -> {t, data}
    this.projects = null; // {t, names}
    this.reload();
  }

  resolve(v) { return v ? path.resolve(this.base, v) : null; }
  scalabel(p) { return `${String(this.cfg.scalabel_url).replace(/\/+$/, "")}${p}`; }

  /** Re-read the CSV(s) and the CAD folder, and forget cached Scalabel answers. */
  reload() {
    this.warnings = [];
    this.loadGeo();
    this.cadDir = this.resolve(this.cfg.cad_dir);
    this.cad = cadIndex(this.cadDir);
    if (this.cfg.cad_dir && !isDir(this.cadDir)) this.warnings.push(`cad_dir not found: ${this.cadDir}`);
    this.imageRoot = this.resolve(this.cfg.image_root);
    if (this.cfg.image_root && !isDir(this.imageRoot)) this.warnings.push(`image_root not found: ${this.imageRoot}`);
    this.live.clear();
    this.projects = null;
  }

  geoFiles() { return typeof this.cfg.geo_csv === "string" ? [this.cfg.geo_csv] : this.cfg.geo_csv || []; }
  /** Where an uploaded CSV is saved: the first geo_csv file, else image_locations.csv. */
  uploadTarget() { return this.resolve(this.geoFiles()[0] || "image_locations.csv"); }
  manualPath() { return this.resolve(this.cfg.manual_csv || "manual_locations.csv"); }

  loadGeo() {
    this.geo = {};
    this.geoWarnings = [];
    for (const c of this.geoFiles()) {
      const p = this.resolve(c);
      if (!isFile(p)) { this.geoWarnings.push(`geo_csv not found: ${p} (upload one on the page)`); continue; }
      const { geo, warn } = readGeoCsv(fs.readFileSync(p, "utf8"), path.basename(p));
      Object.assign(this.geo, geo);
      this.geoWarnings.push(...warn.slice(0, 20));
    }
    this.manual = {};
    const mp = this.manualPath();
    if (isFile(mp)) {
      const { geo, names, warn } = readGeoCsv(fs.readFileSync(mp, "utf8"), path.basename(mp));
      for (const k of Object.keys(geo)) this.manual[k] = { image: names[k], lat: geo[k][0], lon: geo[k][1] };
      this.geoWarnings.push(...warn.slice(0, 20));
    }
    this.warnings = [...this.geoWarnings, ...(this.warnings || []).filter((w) => !/geo_csv|\.csv/.test(w))];
  }

  /** {lat, lon, source} for a frame name: typed in on the page first, then the CSV. */
  geoFor(name) {
    const k = norm(name), m = this.manual[k], g = this.geo[k];
    if (m) return { lat: m.lat, lon: m.lon, source: "manual" };
    if (g) return { lat: g[0], lon: g[1], source: "csv" };
    return null;
  }

  /** Save an uploaded CSV as the lat/lon file. Refuses a CSV with no usable row; refuses to
   *  replace an existing file unless ``replace`` (that file is kept as <name>.bak-<time>). */
  saveUpload(text, replace) {
    const { geo, warn } = readGeoCsv(text, "uploaded CSV");
    const rows = Object.keys(geo).length;
    if (!rows) return { code: 400, error: "No usable rows in this CSV", warnings: warn.slice(0, 10) };
    const target = this.uploadTarget();
    let backup = null;
    if (isFile(target)) {
      if (!replace) {
        const old = Object.keys(readGeoCsv(fs.readFileSync(target, "utf8"), "").geo).length;
        return { code: 409, exists: true, file: path.basename(target), old_rows: old, rows };
      }
      backup = `${target}.bak-${stamp()}`;
      fs.copyFileSync(target, backup);
    }
    writeAtomic(target, text);
    if (!this.geoFiles().length) this.cfg.geo_csv = [path.relative(this.base, target)];
    this.loadGeo();
    return { code: 200, ok: true, file: path.basename(target), rows, warnings: warn.slice(0, 10),
      backup: backup && path.basename(backup) };
  }

  /** Set (or with ``clear``, remove) the typed-in lat/lon of one image; saved to manual_csv. */
  setManual(name, lat, lon, clear) {
    const k = norm(name);
    if (!k) return { code: 400, error: "no image name" };
    if (clear) delete this.manual[k];
    else {
      if (!(Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180)) {
        return { code: 400, error: "lat must be -90..90 and lon -180..180" };
      }
      this.manual[k] = { image: path.posix.basename(String(name).replace(/\\/g, "/")), lat, lon };
    }
    const rows = Object.values(this.manual).map((m) => [m.image, m.lat, m.lon].map(csvCell).join(","));
    writeAtomic(this.manualPath(), ["image,lat,lon", ...rows].join("\n") + "\n");
    return { code: 200, ok: true, geo: this.geoFor(name), manual_rows: rows.length };
  }

  /** The batches: Scalabel's projects (``projects`` from the config, else every project not
   *  matching ``hide_projects``). Cached like the labels. */
  async batches() {
    const ttl = Number(this.cfg.live_cache_seconds) * 1000;
    if (this.projects && Date.now() - this.projects.t < ttl) return this.projects.names;
    let names;
    if ((this.cfg.projects || []).length) names = [...this.cfg.projects];
    else {
      const all = await getJson(this.scalabel("/getProjectNames"), 30000);
      const hide = this.cfg.hide_projects ? new RegExp(this.cfg.hide_projects) : null;
      names = (Array.isArray(all) ? all : []).filter((n) => typeof n === "string" && !(hide && hide.test(n)));
    }
    this.projects = { t: Date.now(), names };
    return names;
  }

  /** A project's labels from Scalabel's /getExport (cached ``live_cache_seconds``). */
  async exportOf(project, fresh) {
    const hit = this.live.get(project), ttl = Number(this.cfg.live_cache_seconds) * 1000;
    if (hit && !fresh && Date.now() - hit.t < ttl) return hit.data;
    const data = await getJson(this.scalabel("/getExport?" + new URLSearchParams({ project_name: project })), 120000);
    this.live.set(project, { t: Date.now(), data });
    return data;
  }

  categories(data) {
    const names = [];
    const cfg = !Array.isArray(data) && data ? data.config : null;
    for (const c of (cfg && cfg.categories) || []) if (c && c.name && !names.includes(c.name)) names.push(c.name);
    for (const f of framesOf(data)) for (const l of f.labels || []) if (l.category && !names.includes(l.category)) names.push(l.category);
    return names.map((n, i) => ({ name: n, display: DISPLAY_NAMES[n] || n, color: categoryColor(n, i) }));
  }

  async batchView(project, fresh) {
    const data = await this.exportOf(project, fresh);
    return {
      id: project, name: project, project, categories: this.categories(data),
      frames: framesOf(data).map((f, i) => {
        const name = String(f.name || f.url || "");
        return { index: i, name, short: path.posix.basename(name.replace(/\\/g, "/")) };
      }),
    };
  }

  /** The frame's image: its url when absolute, else from ``image_root`` when set and the
   *  file is there, else from Scalabel (which serves its items folder at /items/...). */
  imageUrl(f) {
    const u = String(f.url || f.name || "");
    if (/^https?:\/\//i.test(u)) return u;
    if (this.imageRoot && isFile(inside(this.imageRoot, u) || "")) return "/img?" + new URLSearchParams({ p: u });
    return this.scalabel("/" + u.replace(/^\/+/, "").split("/").map(encodeURIComponent).join("/"));
  }

  async frameView(project, index) {
    const frames = framesOf(await this.exportOf(project));
    if (!(index >= 0 && index < frames.length)) return null;
    const f = frames[index], name = String(f.name || f.url || ""), key = norm(name);
    const out = {
      index, count: frames.length, name, image: this.imageUrl(f),
      labels: (f.labels || []).filter((l) => l.poly2d && l.poly2d.length).map(slim),
      scalabel: scalabelLink(this.cfg.scalabel_url, project, index, Number(this.cfg.task_size) || 50),
      geo: null, cad: null,
    };
    const g = this.geoFor(name);
    if (g) out.geo = { ...g, ...mapsLinks(g.lat, g.lon) };
    const c = this.cad[key];
    if (c) {
      const rel = path.relative(this.cadDir, c).split(path.sep).join("/");
      out.cad = { url: "/cad?" + new URLSearchParams({ p: rel }), file: rel };
    }
    return out;
  }
}

function makeHandler(store) {
  const send = (res, code, body, type, cache) => {
    res.writeHead(code, { "Content-Type": type, "Content-Length": Buffer.byteLength(body),
      "Cache-Control": cache ? "max-age=3600" : "no-store" });
    res.end(body);
  };
  const json = (res, obj, code) => send(res, code || 200, JSON.stringify(obj), "application/json");
  const file = (res, p) => {
    if (!p || !isFile(p)) return json(res, { error: "not found" }, 404);
    const st = fs.statSync(p);
    res.writeHead(200, { "Content-Type": MIME[path.extname(p).toLowerCase()] || "application/octet-stream",
      "Content-Length": st.size, "Cache-Control": "max-age=3600" });
    fs.createReadStream(p).on("error", () => res.destroy()).pipe(res);
  };
  return async (req, res) => {
    let u;
    try { u = new URL(req.url, "http://localhost"); } catch (e) { return json(res, { error: "bad url" }, 400); }
    const q = u.searchParams;
    try {
      if (req.method === "POST" && u.pathname.startsWith("/api/geo/")) {
        // the page sends this header; a page on another site cannot (CORS), so it cannot write here
        if (req.headers["x-batch-review"] !== "1") return json(res, { error: "forbidden" }, 403);
        const body = await readBody(req, MAX_UPLOAD);
        let r;
        if (u.pathname === "/api/geo/upload") r = store.saveUpload(body, q.get("replace") === "1");
        else if (u.pathname === "/api/geo/manual") {
          let b;
          try { b = JSON.parse(body); } catch (e) { return json(res, { error: "bad JSON" }, 400); }
          // {name, text: "lat, lon" or a Google Maps link} | {name, lat, lon} | {name, clear: true}
          const ll = b.text !== undefined ? parseLatLon(b.text) : [Number(b.lat), Number(b.lon)];
          if (!b.clear && !ll) return json(res, { error: "Could not read a lat, lon from that (try: 33.77, -118.19)" }, 400);
          r = store.setManual(b.name, ll && ll[0], ll && ll[1], !!b.clear);
        } else return json(res, { error: "not found" }, 404);
        const { code, ...rest } = r;
        return json(res, rest, code);
      }
      if (req.method !== "GET") return json(res, { error: "method not allowed" }, 405);
      if (u.pathname === "/" || u.pathname === "/index.html") return file(res, path.join(STATIC, "index.html"));
      if (u.pathname.startsWith("/static/")) return file(res, inside(STATIC, decodeURIComponent(u.pathname.slice(8))));
      if (u.pathname === "/api/batches") {
        let names = [], error = null;
        try { names = await store.batches(); } catch (e) { error = `Scalabel not reachable at ${store.cfg.scalabel_url} (${e.message})`; }
        return json(res, { batches: names.map((n) => ({ id: n, name: n, project: n })), error,
          scalabel_url: store.cfg.scalabel_url, geo_rows: Object.keys(store.geo).length,
          manual_rows: Object.keys(store.manual).length, geo_file: path.basename(store.uploadTarget()),
          geo_file_exists: isFile(store.uploadTarget()),
          cad_images: Object.keys(store.cad).length, warnings: store.warnings });
      }
      if (u.pathname === "/api/batch") {
        const p = q.get("id");
        return p ? json(res, await store.batchView(p, q.get("fresh") === "1")) : json(res, { error: "no batch given" }, 400);
      }
      if (u.pathname === "/api/frame") {
        const p = q.get("id");
        const fv = p ? await store.frameView(p, parseInt(q.get("i"), 10)) : null;
        return fv ? json(res, fv) : json(res, { error: "no such frame" }, 404);
      }
      if (u.pathname === "/api/reload") { store.reload(); return json(res, { ok: true }); }
      if (u.pathname === "/img") return file(res, inside(store.imageRoot, q.get("p")));
      if (u.pathname === "/cad") return file(res, inside(store.cadDir, q.get("p")));
      return json(res, { error: "not found" }, 404);
    } catch (e) { // one bad request must not stop the server
      return json(res, { error: `${e.name}: ${e.message}` }, 500);
    }
  };
}

function loadConfig(p) {
  const abs = path.resolve(p);
  if (!isFile(abs)) {
    console.error(`config not found: ${abs}\nCopy config.example.json to config.json and edit the paths.`);
    process.exit(2);
  }
  return { cfg: JSON.parse(fs.readFileSync(abs, "utf8")), base: path.dirname(abs) };
}

function main(argv) {
  const arg = (k) => { const i = argv.indexOf(k); return i >= 0 ? argv[i + 1] : undefined; };
  const { cfg, base } = loadConfig(arg("--config") || path.join(HERE, "config.json"));
  const store = new Store(cfg, base);
  const port = Number(arg("--port") || store.cfg.port), host = arg("--host") || store.cfg.host;
  const srv = http.createServer(makeHandler(store));
  srv.on("error", (e) => { console.error(e.code === "EADDRINUSE" ? `port ${port} is in use: is Batch Review already running?` : e.message); process.exit(1); });
  srv.listen(port, host, () => {
    console.log(`Batch Review: http://localhost:${port}  (Scalabel ${store.cfg.scalabel_url}, `
      + `${Object.keys(store.geo).length} lat/lon rows, ${Object.keys(store.cad).length} CAD images)`);
    store.batches().then((n) => console.log(`${n.length} Scalabel projects listed`),
      (e) => console.log(`warning: Scalabel not reachable at ${store.cfg.scalabel_url}: ${e.message}`));
    for (const w of store.warnings.slice(0, 10)) console.log(`warning: ${w}`);
  });
  return srv;
}

module.exports = { norm, scalabelLink, parseLatLon, mapsLinks, parseCsv, readGeoCsv, cadIndex, inside,
  categoryColor, Store, makeHandler };
if (require.main === module) main(process.argv.slice(2));
