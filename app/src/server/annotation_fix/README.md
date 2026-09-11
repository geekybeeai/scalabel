# annotation_fix

Corrects imported polyline annotations before they reach Scalabel. Two fixes,
one pass, driven by the **Auto-correct annotations** checkbox on the create
project form.

This is a TypeScript port of `tools/annotation_fix`, which was a Python child
process (`python -m annotation_fix.stdio`). The Python package is still in the
tree as a reference implementation and standalone CLI, but nothing in the server
calls it and the Docker image no longer installs an interpreter.

## What it does

**ROI clamp.** The orthomosaic images pad a narrow captured footprint into a
large rectangle with pure black. Annotations that drift into that padding are
pulled back to the nearest valid pixel. Vertex count, curve types and label
identity are preserved — only coordinates move. Corrections beyond
`flagDistance` are still applied but reported, so genuine mistakes surface.

**Auto-connect.** Compatible-category polyline endpoints within 15 image px are
spliced into a single label — the batch equivalent of the editor's
drag-an-endpoint-onto-another gesture (`Polygon2D.mergeWith`).

## Order matters

**Clamp runs first, then connect.** Out-of-ROI vertices are frequently line
endpoints, and endpoints are exactly what auto-connect reasons about. Median
spill (8 px) and connect tolerance (15 px) are the same order of magnitude, so
connecting first would evaluate junctions at positions about to move, and
clamping afterwards could pull an already-merged junction apart.

## Layout

| file | role |
| --- | --- |
| `index.ts` | `correctAnnotations`, the entry point the server calls; worker dispatch and the best-effort fallback |
| `worker.ts` | worker-thread entry; webpack emits it as `app/dist/annotation_fix_worker.js` |
| `core.ts` | `processDocument` / `processFrame`, options and the run report |
| `mask.ts` | ROI mask: hole filling, largest component, `contains`, `nearestInside`, the on-disk cache |
| `png.ts` | streaming PNG reader that goes straight from file to threshold mask |
| `clamp.ts` | the clamp stage |
| `autoconnect.ts` | the connect stage |
| `preflight.ts` | diagnostics; emitted as `app/dist/annotation_fix_preflight.js` |

## Why a worker thread

The corrections are CPU-bound for minutes at a time. The Python child process
kept that off the event loop for free; doing the work inline would freeze the
dashboard polling that this feature depends on, along with every websocket and
every other user. The worker preserves that isolation and keeps a runaway job
killable. If the worker bundle is missing the corrections still run, on the main
thread, with a warning.

## Differences from the Python implementation

Behaviour is intended to match, including `nearestInside` returning the pixel
nearest the ROUNDED, CLAMPED query point (which is what indexing a distance
transform gives you) while reporting the distance from the unrolled point.

Two implementation choices differ, both forced by scale:

**No image library.** PNGs are decoded here against Node's built-in zlib rather
than via `canvas`, which is present in the tree but never built (`npm ci
--ignore-scripts`) and would drag Cairo and Pango into the image. Decoding is
streamed a scanline at a time and each row is reduced to mask bytes immediately,
so the full decoded surface is never held: peak memory is about one byte per
pixel. Only non-interlaced PNGs are supported; every frame in the corpus is one.

**No distance transform.** SciPy's `distance_transform_edt(..., return_indices=
True)` materialises a float distance array plus two index arrays over the whole
canvas — several gigabytes at 238.9 megapixels — to answer a few dozen queries
per frame. `nearestInside` searches outward in rings from the query point
instead, which is exact and touches only nearby pixels.

Measured on the largest frame in `local-data` (11250x21240, 238.9 MP): the full
mask pipeline runs in about 8 s at roughly 340 MB peak RSS, against the Python
implementation's documented ~15 s and ~5 GB for a smaller 189 MP frame.

## Environment

| variable | purpose |
| --- | --- |
| `SCALABEL_ANNOTATION_FIX_IMAGE_ROOT` | image root (default `<cwd>/local-data`) |
| `SCALABEL_ANNOTATION_FIX_CACHE_DIR` | ROI mask cache directory; unset disables caching |

`SCALABEL_PYTHON` and `SCALABEL_ANNOTATION_FIX_DIR` are gone — there is no
interpreter to select and no package directory to locate.

The image root defaults to a path under the CURRENT WORKING DIRECTORY, so
starting the server from anywhere but the repo root breaks it. It must be
absolute: a relative path means every frame comes back `imageFound: false` and
the clamp stage silently skips while auto-connect still runs, which looks like
partial success rather than an error.

## Diagnostics

    node app/dist/annotation_fix_preflight.js

Checks the worker bundle, the paths, decodes a real image from the image root,
and runs an end-to-end correction. Exits non-zero when correction would not work
properly. In Docker:

    docker compose exec frontend node app/dist/annotation_fix_preflight.js

The image build runs it with `--build`, which skips the checks needing the data
volume, and fails the build rather than shipping a broken image.

## Failure behaviour

If anything fails — the worker crashes, a run times out, an image cannot be
decoded, or the response does not match the frames it was given —
`correctAnnotations` logs the reason and returns the **original** annotations.
Project creation continues either way: failing a whole import because a helper
broke would be the wrong trade.
