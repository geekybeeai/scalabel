"use strict";
// node --test tools/batch_review/tests
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");
const S = require("../server.js");

test("norm matches frames, CAD images and CSV rows", () => {
  assert.equal(S.norm("items/a/b/854_verification.png"), "854");
  assert.equal(S.norm("100466_200674_input.png"), "100466_200674");
  assert.equal(S.norm("100070-200340.png"), "100070_200340");
  assert.equal(S.norm("MLC 5_verification_z20.png"), "mlc_5");
  assert.equal(S.norm("C:\\x\\854.PNG"), "854");
});

test("Scalabel deep link (1-based item within the task)", () => {
  const link = new URL(S.scalabelLink("http://h:8686/", "p q", 117, 50));
  assert.equal(link.pathname, "/label");
  assert.equal(link.searchParams.get("project_name"), "p q");
  assert.equal(link.searchParams.get("task_index"), "2");
  assert.equal(link.searchParams.get("item"), "18");
  assert.equal(new URL(S.scalabelLink("http://h", "p", 0, 0)).searchParams.get("item"), "1");
});

test("maps links", () => {
  const m = S.mapsLinks(33.1, -117.2);
  assert.match(m.maps, /query=33\.1,-117\.2$/);
  assert.match(m.streetview, /map_action=pano&viewpoint=33\.1,-117\.2$/);
});

test("CSV: quoted fields, ; delimiter, header variants, bad rows skipped", () => {
  const a = S.readGeoCsv('Image Name,Latitude,Longitude\n"854_verification.png",33.5,-117.1\nx.png,abc,1\n', "a.csv");
  assert.deepEqual(a.geo, { 854: [33.5, -117.1] });
  assert.equal(a.warn.length, 1);
  const b = S.readGeoCsv("\uFEFFfile;lat;lng\r\n100466_200674_input.png;10;20\r\nbad.png;95;0\r\n", "b.csv");
  assert.deepEqual(b.geo, { "100466_200674": [10, 20] });
  assert.match(b.warn[0], /out of range/);
  assert.match(S.readGeoCsv("a,b\n1,2\n", "c.csv").warn[0], /need image, lat and lon/);
  assert.deepEqual(S.parseCsv('a,"b ""q"", c"\n'), [["a", 'b "q", c']]);
});

test("paths cannot escape their root", () => {
  const root = path.resolve("/tmp/root");
  assert.equal(S.inside(root, "a/b.png"), path.join(root, "a/b.png"));
  assert.equal(S.inside(root, "../etc/passwd"), null);
  assert.equal(S.inside(root, "/a/../../x"), null);
  assert.equal(S.inside(null, "a"), null);
});

test("colours follow Scalabel", () => {
  assert.deepEqual(S.categoryColor("curb_road_edge", 0), [210, 50, 50]);
  assert.deepEqual(S.categoryColor("without_curb_road_edge", 0), [50, 200, 50]);
  assert.deepEqual(S.categoryColor("something_new", 7), [180, 60, 220]);
});

function fixture() {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), "br-"));
  fs.mkdirSync(path.join(d, "cad/Batch_1"), { recursive: true });
  fs.writeFileSync(path.join(d, "cad/Batch_1/854_input.png"), "CAD1");
  fs.writeFileSync(path.join(d, "geo.csv"), "image,lat,lon\n854.png,33.5,-117.25\n");
  return d;
}

/** A stand-in Scalabel: /getProjectNames, /getExport and /items/... (counts export calls). */
function fakeScalabel() {
  const lab = { id: "L1", category: "curb_road_edge", attributes: {},
    poly2d: [{ vertices: [[0, 0], [5, 5], [6, 6], [10, 0]], types: "LCCL", closed: false }] };
  const exp = { frames: [{ name: "items/a b/854_verification.png", url: "items/a b/854_verification.png", labels: [lab] },
    { name: "items/a b/855_verification.png", url: "items/a b/855_verification.png", labels: [] }],
  config: { categories: [{ name: "curb_road_edge" }, { name: "dashed_white_line" }] } };
  const calls = { exports: 0 };
  const srv = http.createServer((req, res) => {
    const u = new URL(req.url, "http://x");
    if (u.pathname === "/getProjectNames") {
      res.end(JSON.stringify(["proj_a", "proj_a_export_x_fix_curb_soft_v2", "zz_test"]));
    } else if (u.pathname === "/getExport" && u.searchParams.get("project_name") === "proj_a") {
      calls.exports++; res.end(JSON.stringify(exp));
    } else if (u.pathname === "/items/a%20b/854_verification.png") res.end("PNG1");
    else { res.statusCode = 404; res.end("{}"); }
  });
  return { srv, calls };
}

function get(port, p) {
  return new Promise((resolve, reject) => {
    http.get({ host: "127.0.0.1", port, path: p }, (res) => {
      const c = []; res.on("data", (x) => c.push(x));
      res.on("end", () => resolve({ code: res.statusCode, type: res.headers["content-type"], body: Buffer.concat(c).toString() }));
    }).on("error", reject);
  });
}
const listen = (srv) => new Promise((r) => srv.listen(0, "127.0.0.1", () => r(srv.address().port)));

test("server: projects, labels and images from Scalabel; CAD, geo, static, errors", async () => {
  const d = fixture();
  const sc = fakeScalabel();
  const scPort = await listen(sc.srv);
  const store = new S.Store({ scalabel_url: `http://127.0.0.1:${scPort}`, cad_dir: "cad", geo_csv: ["geo.csv"],
    task_size: 50, live_cache_seconds: 30 }, d);
  const srv = http.createServer(S.makeHandler(store));
  const port = await listen(srv);
  try {
    const bl = JSON.parse((await get(port, "/api/batches")).body);
    assert.deepEqual(bl.batches.map((b) => b.name), ["proj_a"]); // _fix_ and zz_ projects hidden
    assert.equal(bl.error, null);
    assert.equal(bl.geo_rows, 1);
    assert.equal(bl.cad_images, 1);
    const b = JSON.parse((await get(port, "/api/batch?id=proj_a")).body);
    assert.deepEqual(b.frames.map((f) => f.short), ["854_verification.png", "855_verification.png"]);
    assert.deepEqual(b.categories.map((c) => c.display), ["curb_road_edge", "dashed_line"]);
    const f = JSON.parse((await get(port, "/api/frame?id=proj_a&i=0")).body);
    assert.equal(f.labels[0].poly2d[0].types, "LCCL");
    assert.equal(f.geo.lat, 33.5);
    assert.equal(f.cad.file, "Batch_1/854_input.png");
    assert.match(f.scalabel, /project_name=proj_a&task_index=0&item=1$/);
    assert.equal(f.image, `http://127.0.0.1:${scPort}/items/a%20b/854_verification.png`); // from Scalabel
    assert.equal((await new Promise((r) => http.get(f.image, (x) => { let t = ""; x.on("data", (c) => { t += c; }); x.on("end", () => r(t)); }))), "PNG1");
    assert.equal(sc.calls.exports, 1); // one /getExport for the batch and its frame (cached)
    assert.equal((await get(port, f.cad.url)).body, "CAD1");
    const f2 = JSON.parse((await get(port, "/api/frame?id=proj_a&i=1")).body);
    assert.equal(f2.geo, null);
    assert.equal(f2.cad, null);
    assert.equal((await get(port, "/api/frame?id=proj_a&i=5")).code, 404);
    assert.equal((await get(port, "/api/batch?id=nope")).code, 500); // Scalabel has no such project
    await get(port, "/api/reload");
    await get(port, "/api/batch?id=proj_a");
    assert.equal(sc.calls.exports, 2); // Refresh asks Scalabel again
    assert.equal((await get(port, "/img?p=../geo.csv")).code, 404);
    assert.equal((await get(port, "/static/../server.js")).code, 404);
    assert.match((await get(port, "/")).body, /Batch Review/);
    assert.equal((await get(port, "/static/app.js")).code, 200);
    // Scalabel down: the page gets an error message, the server keeps running
    sc.srv.close();
    store.cfg.scalabel_url = "http://127.0.0.1:1";
    store.reload();
    const down = JSON.parse((await get(port, "/api/batches")).body);
    assert.match(down.error, /Scalabel not reachable/);
    assert.equal((await get(port, "/static/app.js")).code, 200);
  } finally {
    srv.close(); sc.srv.close();
  }
});

test("projects from the config replace Scalabel's list", async () => {
  const store = new S.Store({ projects: ["b", "a"], scalabel_url: "http://127.0.0.1:1" }, os.tmpdir());
  assert.deepEqual(await store.batches(), ["b", "a"]);
});

test("parseLatLon reads typed pairs and Google Maps links", () => {
  assert.deepEqual(S.parseLatLon("33.77, -118.19"), [33.77, -118.19]);
  assert.deepEqual(S.parseLatLon(" 33.77 -118.19 "), [33.77, -118.19]);
  assert.deepEqual(S.parseLatLon("https://www.google.com/maps/@33.7701,-118.1937,17z"), [33.7701, -118.1937]);
  assert.deepEqual(S.parseLatLon("https://www.google.com/maps/search/?api=1&query=33.1,-117.2"), [33.1, -117.2]);
  assert.equal(S.parseLatLon("95, 1"), null);
  assert.equal(S.parseLatLon("hello"), null);
});

function post(port, p, body, type, headers) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: "127.0.0.1", port, path: p, method: "POST",
      headers: { "Content-Type": type, "X-Batch-Review": "1", ...(headers || {}) } }, (res) => {
      const c = []; res.on("data", (x) => c.push(x));
      res.on("end", () => resolve({ code: res.statusCode, d: JSON.parse(Buffer.concat(c).toString() || "{}") }));
    });
    req.on("error", reject); req.end(body);
  });
}

test("upload a CSV and type in lat/lon from the page", async () => {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), "br-geo-"));
  const sc = fakeScalabel();
  const scPort = await listen(sc.srv);
  const store = new S.Store({ scalabel_url: `http://127.0.0.1:${scPort}`, geo_csv: [] }, d);
  const srv = http.createServer(S.makeHandler(store));
  const port = await listen(srv);
  try {
    let bl = JSON.parse((await get(port, "/api/batches")).body);
    assert.equal(bl.geo_file, "image_locations.csv");
    assert.equal(bl.geo_file_exists, false);
    // no header from the page -> refused; a CSV with no usable row -> refused, nothing written
    assert.equal((await post(port, "/api/geo/upload", "image,lat,lon\n854.png,1,2\n", "text/csv", { "X-Batch-Review": "0" })).code, 403);
    const bad = await post(port, "/api/geo/upload", "a,b\n1,2\n", "text/csv");
    assert.equal(bad.code, 400);
    assert.equal(fs.existsSync(path.join(d, "image_locations.csv")), false);
    // first upload is saved as image_locations.csv
    const up = await post(port, "/api/geo/upload", "image,lat,lon\n854_verification.png,10,20\n", "text/csv");
    assert.equal(up.code, 200);
    assert.equal(up.d.rows, 1);
    let f = JSON.parse((await get(port, "/api/frame?id=proj_a&i=0")).body);
    assert.deepEqual([f.geo.lat, f.geo.lon, f.geo.file], [10, 20, "image_locations.csv"]);
    // a second upload asks first, then replaces and keeps the old file as a backup
    const again = await post(port, "/api/geo/upload", "image,note,lat,lon\n854.png,keep me,11,21\n", "text/csv");
    assert.equal(again.code, 409);
    assert.equal(again.d.old_rows, 1);
    const rep = await post(port, "/api/geo/upload?replace=1", "image,note,lat,lon\n854.png,keep me,11,21\n", "text/csv");
    assert.equal(rep.code, 200);
    assert.match(rep.d.backup, /^image_locations\.csv\.bak-/);
    assert.match(fs.readFileSync(path.join(d, rep.d.backup), "utf8"), /854_verification\.png,10,20/);
    // typed in: updates the image's row in place (other columns kept), or adds a row
    const csv = () => fs.readFileSync(path.join(d, "image_locations.csv"), "utf8");
    const set = (name, text) => post(port, "/api/geo/set", JSON.stringify({ name, text }), "application/json");
    assert.equal((await set("items/a b/854_verification.png", "nonsense")).code, 400);
    assert.equal(csv(), "image,note,lat,lon\n854.png,keep me,11,21\n"); // nothing written
    const u = await set("items/a b/854_verification.png", "https://www.google.com/maps/@33.5,-117.25,18z");
    assert.equal(u.code, 200);
    assert.equal(u.d.result, "updated");
    assert.deepEqual([u.d.geo.lat, u.d.geo.lon], [33.5, -117.25]);
    assert.equal(csv(), "image,note,lat,lon\n854.png,keep me,33.5,-117.25\n");
    const n = await set("items/a b/855_verification.png", "12, 22");
    assert.equal(n.d.result, "added");
    assert.equal(csv(), "image,note,lat,lon\n854.png,keep me,33.5,-117.25\n855_verification.png,,12,22\n");
    assert.equal((await set("items/a b/855_verification.png", "12.5, 22.5")).d.result, "updated"); // no duplicate row
    assert.equal(csv().split("\n").filter(Boolean).length, 3);
    f = JSON.parse((await get(port, "/api/frame?id=proj_a&i=1")).body);
    assert.deepEqual([f.geo.lat, f.geo.lon], [12.5, 22.5]);
    bl = JSON.parse((await get(port, "/api/batches")).body);
    assert.deepEqual([bl.geo_rows, bl.geo_file_exists], [2, true]);
    store.reload(); // kept across a reload / restart
    assert.deepEqual([store.geoFor("855.png").lat], [12.5]);
    // one backup per day, taken before the first change
    const baks = fs.readdirSync(d).filter((x) => /^image_locations\.csv\.bak-\d{8}$/.test(x));
    assert.equal(baks.length, 1);
    assert.equal(fs.readFileSync(path.join(d, baks[0]), "utf8"), "image,note,lat,lon\n854.png,keep me,11,21\n");
  } finally {
    srv.close(); sc.srv.close();
  }
});

test("typed lat/lon goes to the file that has the image; an old manual_locations.csv is merged once", () => {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), "br-geo2-"));
  fs.writeFileSync(path.join(d, "a.csv"), "image,lat,lon\n1.png,1,1\n");
  fs.writeFileSync(path.join(d, "b.csv"), "file;lat;lng\n2.png;2;2\n");
  fs.writeFileSync(path.join(d, "manual_locations.csv"), "image,lat,lon\n1_verification.png,5,5\n9.png,9,9\n");
  const store = new S.Store({ geo_csv: ["a.csv", "b.csv"], scalabel_url: "http://127.0.0.1:1" }, d);
  // migration: merged into the first file, old file renamed (not deleted)
  assert.equal(fs.readFileSync(path.join(d, "a.csv"), "utf8"), "image,lat,lon\n1.png,5,5\n9.png,9,9\n");
  assert.equal(fs.existsSync(path.join(d, "manual_locations.csv")), false);
  assert.equal(fs.readdirSync(d).filter((x) => x.startsWith("manual_locations.csv.migrated-")).length, 1);
  // an image of b.csv is updated in b.csv, keeping its ; separator
  assert.equal(store.setGeo("x/2_verification.png", 3, 4).file, "b.csv");
  assert.equal(fs.readFileSync(path.join(d, "b.csv"), "utf8"), "file;lat;lng\n2.png;3;4\n");
  assert.equal(store.setGeo("x/7.png", 7, 7).file, "a.csv"); // new image -> first file
  assert.equal(store.setGeo("x/7.png", 95, 7).code, 400);
});

test("page script has no syntax error", () => {
  const src = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
  assert.doesNotThrow(() => new Function(src));
});
