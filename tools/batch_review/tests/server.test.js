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

test("project name and the Scalabel deep link (1-based item within the task)", () => {
  assert.equal(S.projectName("/x/batch_3_x_v1_export_2026-10-15_18-31-21.json", {}), "batch_3_x_v1");
  assert.equal(S.projectName("/x/b_export_1.json", { "b_export_1.json": "other" }), "other");
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
  fs.mkdirSync(path.join(d, "exports"));
  fs.mkdirSync(path.join(d, "data/items"), { recursive: true });
  fs.mkdirSync(path.join(d, "cad/Batch_1"), { recursive: true });
  fs.writeFileSync(path.join(d, "data/items/854_verification.png"), "PNG1");
  fs.writeFileSync(path.join(d, "cad/Batch_1/854_input.png"), "CAD1");
  fs.writeFileSync(path.join(d, "geo.csv"), "image,lat,lon\n854.png,33.5,-117.25\n");
  const lab = { id: "L1", category: "curb_road_edge", attributes: {},
    poly2d: [{ vertices: [[0, 0], [5, 5], [6, 6], [10, 0]], types: "LCCL", closed: false }] };
  const frames = [{ name: "items/854_verification.png", url: "items/854_verification.png", labels: [lab] },
    { name: "items/855_verification.png", url: "items/855_verification.png", labels: [] }];
  fs.writeFileSync(path.join(d, "exports/proj_a_export_2026.json"),
    JSON.stringify({ frames, config: { categories: [{ name: "curb_road_edge" }, { name: "dashed_white_line" }] } }));
  return d;
}

function get(port, p) {
  return new Promise((resolve, reject) => {
    http.get({ host: "127.0.0.1", port, path: p }, (res) => {
      const c = []; res.on("data", (x) => c.push(x));
      res.on("end", () => resolve({ code: res.statusCode, type: res.headers["content-type"], body: Buffer.concat(c).toString() }));
    }).on("error", reject);
  });
}

test("server: batches, frame, image, CAD, geo, static, errors", async () => {
  const d = fixture();
  const store = new S.Store({ batches_dir: "exports", image_root: "data", cad_dir: "cad", geo_csv: ["geo.csv"],
    scalabel_url: "http://sc:8686", task_size: 50 }, d);
  const srv = http.createServer(S.makeHandler(store));
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  const port = srv.address().port;
  try {
    const bl = JSON.parse((await get(port, "/api/batches")).body);
    assert.equal(bl.batches.length, 1);
    assert.equal(bl.batches[0].project, "proj_a");
    assert.equal(bl.geo_rows, 1);
    assert.equal(bl.cad_images, 1);
    const b = JSON.parse((await get(port, "/api/batch?id=0")).body);
    assert.deepEqual(b.frames.map((f) => f.short), ["854_verification.png", "855_verification.png"]);
    assert.deepEqual(b.categories.map((c) => c.display), ["curb_road_edge", "dashed_line"]);
    const f = JSON.parse((await get(port, "/api/frame?id=0&i=0")).body);
    assert.equal(f.labels[0].poly2d[0].types, "LCCL");
    assert.equal(f.geo.lat, 33.5);
    assert.equal(f.cad.file, "Batch_1/854_input.png");
    assert.match(f.scalabel, /project_name=proj_a&task_index=0&item=1$/);
    const img = await get(port, f.image);
    assert.equal(img.body, "PNG1");
    assert.equal(img.type, "image/png");
    assert.equal((await get(port, f.cad.url)).body, "CAD1");
    const f2 = JSON.parse((await get(port, "/api/frame?id=0&i=1")).body);
    assert.equal(f2.geo, null);
    assert.equal(f2.cad, null);
    assert.equal((await get(port, "/api/frame?id=0&i=5")).code, 404);
    assert.equal((await get(port, "/api/batch?id=9")).code, 404);
    assert.equal((await get(port, "/img?p=../geo.csv")).code, 404);
    assert.equal((await get(port, "/static/../server.js")).code, 404);
    const page = await get(port, "/");
    assert.match(page.body, /Batch Review/);
    assert.equal((await get(port, "/static/app.js")).code, 200);
    // live mode with Scalabel down: an error answer, the server keeps running
    store.cfg.scalabel_url = "http://127.0.0.1:1";
    assert.equal((await get(port, "/api/batch?id=0&live=1")).code, 500);
    assert.equal((await get(port, "/api/batch?id=0")).code, 200);
  } finally {
    srv.close();
  }
});

test("page script has no syntax error", () => {
  const src = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
  assert.doesNotThrow(() => new Function(src));
});
