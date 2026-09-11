# Annotation auto-correct: JavaScript engine

**Date:** 2026-09-11
**Branch:** `update_annotations_v5_jsnikhil`

## Goal

Make the "Auto-correct annotations" checkbox work with the plain server
command — `node app/dist/main.js --config ...` after `npm run build` — on any
machine where `npm install` succeeds, with no Python runtime. Today the
correction runs in a Python subprocess (`tools/annotation_fix`) and silently
skips when Python, Pillow, NumPy or SciPy are absent.

The Python package stays in the repository as a standalone CLI and as the
parity reference; the server stops using it.

## Non-goals

- Changing the correction rules. Category rules, tolerances, insets, flag
  thresholds, splice orientations and survivor selection are ported verbatim.
- Changing the background worker, status tracking, dashboard, or the
  stdin/stdout protocol between the server and the child.
- Bit-identical clamp coordinates on exact nearest-pixel ties (see §4.2).

## 1. Architecture

```
listeners.ts ─> correction_worker.ts ─> annotation_fix.ts ─spawn─> annotation_fix_worker.js
                                                                    (app/src/server/annotation_fix/stdio.ts)
                                                                        └─ core.ts → mask.ts / clamp.ts / autoconnect.ts
```

- New directory `app/src/server/annotation_fix/` holds the engine, one module
  per stage, mirroring the Python package.
- A second **server** webpack entry, `annotation_fix_worker`, compiles
  `stdio.ts` to `app/dist/annotation_fix_worker.js` beside `main.js`. The
  server config already uses `webpack-node-externals`, so `sharp` is
  `require`d from `node_modules` at runtime rather than bundled.
- `annotation_fix.ts` keeps its protocol, 30-minute timeout, 1 GiB stdout
  cap and every fallback path. Only the spawn changes:
  `spawn(process.execPath, [path.join(__dirname, "annotation_fix_worker.js")])`.
  `__dirname` is the real directory at runtime (`node.__dirname: false` in the
  server webpack config), i.e. `app/dist`.
- Removed: `getPythonExecutable`, `PYTHON_CANDIDATES`, `getToolsDir`,
  `SCALABEL_PYTHON`, `SCALABEL_ANNOTATION_FIX_DIR`, `PYTHONPATH`.
  `SCALABEL_ANNOTATION_FIX_IMAGE_ROOT` and `getImageRoot` stay.
- The child still isolates the heavy work from the server event loop; an OOM
  or crash in the child surfaces as a rejected promise and the task keeps its
  original annotations, exactly as today.

## 2. Modules

| File | Exports | Depends on |
|---|---|---|
| `mask.ts` | `RoiMask` (width, height, `Uint8Array` data, `contains`, `nearestInside`), `computeRoiMask(path, threshold)` | `sharp` |
| `clamp.ts` | `clampVertices`, `clampLabels`, `VertexCorrection`, `ClampResult`, `DEFAULT_INSET = 1.5`, `DEFAULT_FLAG_DISTANCE = 50` | `mask.ts` |
| `autoconnect.ts` | `connectLabels`, `Connection`, `ConnectResult`, `DEFAULT_TOLERANCE = 15`, `DEFAULT_MIN_ANGLE = 0`, `DEFAULT_MERGEABLE_CATEGORIES` | — |
| `core.ts` | `Options`, `FrameReport`, `Report`, `resolveImagePath`, `processFrame`, `processDocument` | all above |
| `stdio.ts` | worker entry: read one JSON request on stdin, write one JSON response on stdout | `core.ts` |

Types come from `app/src/types/export.ts` (`ItemExport`, `LabelExport`,
`PolygonExportType`). The engine works on `Partial<ItemExport>` frames, the
same shape `correctAnnotations` already passes.

## 3. Protocol (unchanged)

Request on stdin:

```json
{"document": [...frames], "image_root": "/abs/path", "cache_dir": null,
 "clamp": true, "connect": true}
```

Optional tunables accepted with the same defaults as the Python `stdio.py`:
`threshold` 10, `tolerance` 15, `min_angle` 0, `inset` 1.5,
`flag_distance` 50. `cache_dir` is accepted and ignored (the JS engine has no
mask cache; the server never set one).

Response on stdout, exactly one JSON value:

```json
{"ok": true, "document": [...], "report": {"summary": {...}, "frames": [...]}}
{"ok": false, "error": "..."}
```

Report field names are identical to the Python `to_dict()` output:
`summary.{frames,totalClamped,totalMerged,totalFlagged,missingImages}` and
per-frame `{name,imageFound,roiCoverage,totalVertices,clamped,flagged,merged,
labelsBefore,labelsAfter,connections,error}`.

Stdout is reserved for the response; all diagnostics go to stderr. Exit 0 on
`ok: true`, 1 otherwise.

## 4. Algorithms

### 4.1 ROI mask (`mask.ts`)

1. Decode with `sharp(path, { limitInputPixels: false }).removeAlpha().raw().toBuffer({ resolveWithObject: true })`
   → RGB bytes plus width/height.
2. `mask[i] = max(r,g,b) > threshold` into a `Uint8Array(width*height)`;
   the RGB buffer is released immediately after.
3. Fill holes: flood-fill background (0) pixels reachable from the canvas
   border using an explicit `Int32Array` stack; every background pixel not
   reached is a hole and becomes 1. This equals
   `scipy.ndimage.binary_fill_holes` (4-connectivity, default structure).
4. Largest component: single-pass BFS labelling over the 1-pixels with a
   reusable visited buffer, tracking component sizes; keep the largest.
   Equals `ndimage.label` (default 4-connectivity) + argmax. On an exact
   size tie scipy keeps the lowest label id, i.e. the first component in
   raster order; the BFS scans in raster order and keeps the first maximum,
   so this matches.
5. `contains(x, y)`: round, bounds-check, look up — identical to Python.
6. `nearestInside(x, y)`: round (half-to-even) and clip the point to the
   canvas; if that pixel is inside, return it with distance 0. Otherwise
   search square rings of radius r = 1, 2, … around the clipped pixel for
   the inside pixel nearest to **that pixel** — exactly what scipy's
   distance-transform index lookup returns for the reference implementation.
   After a hit, keep scanning while `r <= best` (a ring's Chebyshev radius
   under-bounds Euclidean distance). Report the distance from the chosen
   pixel to the **unrounded** point, as the Python code does. A hard cap of
   `max(width, height)` rings guarantees termination.

Peak memory for a 240 MP frame: ~720 MB RGB (transient) + 240 MB mask +
~1 GB labelling scratch, versus ~5 GB in Python. No full-canvas distance
transform.

### 4.2 Nearest-pixel parity

The distance returned equals scipy's `distance_transform_edt` distance for
the same point. When two inside pixels are exactly equidistant, scipy's
choice is an implementation detail of its separable transform; this engine
returns the first found in ring scan order. The corrected vertex may
therefore differ by at most one pixel in those cases. Accepted.

### 4.3 Clamp (`clamp.ts`)

Verbatim port of `clamp.py`: vertices inside are appended unchanged (exact
original values); outside vertices go through `nearestInside` then
`insetPoint` (step `inset` px further along the incoming direction; keep the
boundary point if the inset lands outside). Only `vertices` is reassigned,
and only when at least one correction occurred. `VertexCorrection` and
`ClampResult.flagged(flagDistance)` keep the Python field names.

### 4.4 Auto-connect (`autoconnect.ts`)

Verbatim port of `autoconnect.py`:

- Eligibility: exactly one `poly2d`, not `closed`, ≥ 2 vertices.
- Categories: identical always; otherwise only pairs in
  `DEFAULT_MERGEABLE_CATEGORIES = [ {curb_road_edge, without_curb_road_edge} ]`.
- Candidates: all endpoint pairs across different labels, gap ≤ tolerance,
  optional `min_angle` guard with the same angle formula
  (`180 − acos(dot(dirA, −dirB))` in degrees; a null angle never rejects).
- Greedy: apply the single shortest-gap pair, then rebuild the endpoint list
  from scratch and repeat until no candidate remains.
- Survivor: label A (the lower index in the endpoint list); B is absorbed.
- Splice: the four orientations with B's duplicate junction vertex dropped and
  the `types` string spliced identically.
- `Connection` fields: `keptId, absorbedId, category, junction, gap, angle?`.

### 4.5 Core (`core.ts`)

`processDocument` deep-copies the input (`JSON.parse(JSON.stringify())`,
adequate for export JSON), accepts a `{frames}` document or a bare array and
returns the same shape. Stage order is clamp then connect, for the reason
documented in `core.py`. `resolveImagePath` tries `name`, `root/name`,
`root/basename(name)`. A missing image sets `imageFound: false`, skips clamp
and still runs connect. A mask/decoder exception is captured into the
frame's `error` field and the run continues.

## 5. Server changes

- `annotation_fix.ts`: spawn `process.execPath` with the worker path; delete
  interpreter discovery and tools-dir resolution; update the log hint from
  `python3 tools/annotation_fix/preflight.py` to `node app/dist/annotation_fix_worker.js --preflight`.
- `stdio.ts` accepts `--preflight`: loads sharp, decodes a 4×4 in-memory PNG,
  runs the two-touching-road-edges round trip, prints OK/FAIL, exits 0/1.
- `webpack.config.js`: add the `annotation_fix_worker` entry to
  `serverConfig.entry`.
- `package.json`: add `sharp` (`^0.35.4`; requires Node ≥ 20.9).
- `Dockerfile`: `FROM node:20-bookworm-slim`; drop the apt Python install and
  the Python preflight gate; run `node app/dist/annotation_fix_worker.js --preflight`
  after the build instead.
- `tools/annotation_fix/*.py` docstrings and `tools/requirements.txt`: state
  that the server uses the JS engine; the Python package is a standalone
  CLI/reference.

## 6. Testing

Jest, under `app/test/server/annotation_fix/`, each file with
`/** @jest-environment node */`:

- `autoconnect.test.ts`: four splice orientations and `types` splicing; the
  duplicate junction vertex is dropped; chain A-B-C resolves; shortest gap
  wins when three endpoints converge; same-category, cross-category
  allowed pair, cross-category refused pair; closed / multi-poly / 1-vertex
  labels ignored; `min_angle` rejects a sharp fork and accepts a straight
  continuation; survivor is the lower index; report counts.
- `mask.test.ts`: PNGs generated in-test with `sharp` — a black canvas with a
  white blob: threshold, hole filling, largest-component selection,
  `contains`, `nearestInside` distance and result for interior, edge,
  outside and off-canvas points, including a case where the nearest pixel is
  not on the first ring hit.
- `clamp.test.ts`: inside vertices untouched with identical values; outside
  vertices move to nearest + inset; inset falls back when it would exit a
  thin region; `flagged` threshold; vertex count / `types` / `closed`
  preserved; `vertices` not reassigned when nothing moved.
- `core.test.ts`: document vs bare-list shape preserved; deep copy (input
  unchanged); clamp-then-connect ordering observable on a fixture where the
  order changes the result; missing image → `imageFound: false`, connect
  still runs, `missingImages` populated; report field names.
- `stdio.test.ts`: spawn the compiled worker is out of scope for Jest;
  instead test the `run(payload)` function: missing `document` → error;
  thrown exception → `ok: false`.

Parity harness `tools/annotation_fix_parity.js`: takes a Scalabel export JSON
and an image root, runs the Python `stdio.py` and the JS worker on it, and
prints a diff of `summary`, per-frame counts, connection lists and any vertex
whose coordinates differ by more than 1e-6, so tie-break differences are
listed rather than hidden. Not part of `npm test`.

Manual acceptance: `npm run build`, start Redis and the server with the
existing command, create a project with the checkbox ticked, confirm the log
line `Annotation auto-correct: N vertices clamped, M lines merged across K
frames` and that dashboard task links re-enable.
