# annotation_fix

> **Note:** the Scalabel server uses a TypeScript port of this engine
> (`app/src/server/annotation_fix`), compiled to
> `app/dist/annotation_fix_worker.js`. This Python package is the reference
> implementation and a standalone CLI; keep both in sync when changing rules.

Corrects imported polyline annotations before they reach Scalabel. Two fixes,
one pass, driven by the **Auto-correct annotations** checkbox on the create
project form.

## What it does

**ROI clamp.** The orthomosaic images pad a narrow captured footprint into a
large rectangle with pure black. Annotations that drift into that padding are
pulled back to the nearest valid pixel.

Measured on 12 frames of `batch_1_images_637_server`: 97 of 2479 vertices (3.9%)
fell outside the ROI and every frame was affected. Spill was **median 8 px, p90
16 px, max 198 px** against images 5000-20000 px wide — annotators tracing a curb
a hair past where imagery ends. That distribution is why this clamps rather than
clips: at 8 px there is no meaningful segment to truncate, and clamping preserves
vertex count, curve types and label identity.

**Auto-connect.** Same-category polyline endpoints within 15 image px are spliced
into a single label — the batch equivalent of the editor's
drag-an-endpoint-onto-another gesture (`Polygon2D.mergeWith`). On the same batch
this merged 251 pairs across 8 frames at a median gap of 5.8 px, including 8
pairs whose endpoints were already coincident at exactly 0.00 px.

## Order matters

**Clamp runs first, then connect.** Out-of-ROI vertices are frequently line
endpoints, and endpoints are exactly what auto-connect reasons about. Median
spill (8 px) and connect tolerance (15 px) are the same order of magnitude, so
connecting first would evaluate junctions at positions about to move, and
clamping afterwards could pull an already-merged junction apart.

## Requirements

    pip install -r tools/requirements.txt   # core: pillow, numpy, scipy
    pip install fastapi uvicorn             # only for the HTTP service

These are NOT installed by `npm install`. Without them the correction skips and
projects are created with UNCORRECTED annotations (see Failure behaviour).

### Setting up a new machine

Auto-correction needs more than the repo: an interpreter the server can spawn,
the three packages above, and `tools/` plus `local-data/` resolvable from the
server's working directory. Check all of it at once:

    python3 tools/annotation_fix/preflight.py

It reports which dependency or path is broken and how to fix it, and exits
non-zero when correction would silently skip. Run it on any machine where a
project comes out uncorrected.

### Docker (the recommended way to run this elsewhere)

The image bundles python3 and the three packages, so auto-correction works with
no host setup — on Windows, macOS or Linux alike, since the container is Linux
inside either way:

    docker compose up --build

The build runs `preflight.py --build` and FAILS if the interpreter or a package
is missing, so a broken image cannot ship. Verify a running container with:

    docker compose exec frontend python3 tools/annotation_fix/preflight.py

### Scalabel server overrides

The Scalabel Node server runs the compiled JavaScript worker with the same Node
binary as `main.js`; it does not spawn this Python reference package. Its
auto-connect settings are:

| variable | purpose |
| --- | --- |
| `SCALABEL_ANNOTATION_FIX_IMAGE_ROOT` | image root (default `<cwd>/local-data`) |
| `SCALABEL_ANNOTATION_FIX_WORKER` | compiled JavaScript worker path (default beside `main.js`) |
| `SCALABEL_ANNOTATION_FIX_TOLERANCE` | positive endpoint gap in image pixels (default `40`) |
| `SCALABEL_ANNOTATION_FIX_MIN_ANGLE` | continuation guard from `0` through `180` degrees (default `150`; `0` disables it) |

`SCALABEL_ANNOTATION_FIX_IMAGE_ROOT` defaults beneath the current working
directory, so start the server from the repository root or set it explicitly.

## Use as a library

The API is one function. No file I/O, no global state — the same call serves a
batch of 221 frames and a single-frame request.

```python
from annotation_fix import Options, process_document

corrected, report = process_document(doc, Options(image_root="local-data/"))
print(report.total_clamped, report.total_merged, report.total_flagged)
```

The input document is deep-copied, so the caller's copy is never modified.
Accepts a full document (`{"frames": [...], "config": {...}}`) or a bare list of
frames, and returns the same shape.

## Use from the command line

```bash
python -m annotation_fix input.json -o corrected.json \
    --image-root local-data/ --cache-dir .roi-cache
```

Writes a new file rather than editing in place. Useful flags: `--dry-run`,
`--limit N`, `--no-clamp`, `--no-connect`, `--report report.json`.

## How Scalabel calls it

The server spawns `python -m annotation_fix.stdio` as a short-lived child
process for the duration of one import, sending the request on stdin and reading
the corrected document from stdout. **Nothing has to be started by hand** — no
port, no daemon, no stale process holding old code.

Requires only `python3` on PATH with pillow/numpy/scipy installed. Override with:

    SCALABEL_PYTHON=/path/to/python3                        # interpreter
    SCALABEL_ANNOTATION_FIX_DIR=/path/to/tools              # package location
    SCALABEL_ANNOTATION_FIX_IMAGE_ROOT=/abs/path/local-data # image root

### Image paths

Frame names are stored relative to the data root, so **`image_root` must be
absolute**. A relative path resolves against the child's working directory and
every frame comes back `imageFound: false`: the clamp stage silently skips while
auto-connect still runs, which looks like partial success rather than an error.
The default is `<cwd>/local-data`, and an explicit override is `path.resolve`d.

## Optional HTTP service

`api.py` exposes the same contract over HTTP for callers that want it. It is NOT
used by Scalabel and needs `fastapi` + `uvicorn`:

    uvicorn annotation_fix.api:app --port 8687

## Failure behaviour

If python is missing, a dependency is absent, the child times out, or the
response does not match the frames it was given, `correctAnnotations` logs the
reason and returns the **original** annotations. Project creation continues
either way — failing a whole import because a helper broke would be the wrong
trade. Verified against a missing interpreter, a bad tools directory, a timeout,
and a wrong image root: all four return the input unchanged.

## Performance

Full resolution, no downscaling. A 53 MP frame takes ~3.6 s; the 189 MP worst
case takes ~15 s at ~5 GB peak. ROI masks are cached bit-packed and keyed by path
+ mtime + threshold, which roughly halves repeat runs (8 frames: 30 s cold, 15 s
warm, 264 KB of cache).

## Tuning

| Option | Default | Notes |
|---|---|---|
| `threshold` | 10 | Measured insensitive: the non-black fraction moves only 0.1415 → 0.1396 across 2..30. |
| `tolerance` | 15.0 | Image px, matching the editor's screen-px snap radius. |
| `min_angle` | 0.0 (off) | When enabled, requires a near-straight junction and requires the endpoint gap to follow both line tangents. This rejects offset parallel lines and forks; `0` disables both checks. A curve-adjacent gap of at most 5 image px is accepted as sampling jitter, so fragmented Bezier spans can reconnect without reopening larger parallel-line gaps. The Scalabel server sends `150` by default. |
| `inset` | 1.5 | Nudges clamped vertices off the exact boundary. |
| `flag_distance` | 50.0 | Corrections beyond this are reported, not suppressed. |

## Implementation notes

**Channel reduction uses `max(R, G, B)`, never luminance.** Luminance weights
green ~0.59 and blue ~0.11, pushing dark blue-grey asphalt below the threshold:
on a real 20000x9440 frame `convert("L")` yields **54** connected components
where `max(R, G, B)` yields **2**. Any non-zero channel means real imagery.

**Hole filling is required, not cosmetic.** Dark asphalt, shadows and dark
vehicles inside the road threshold to black and would punch false holes through
the ROI.

**Clamping is an O(1) lookup.** `distance_transform_edt(..., return_indices=True)`
gives, for every outside pixel, the nearest inside pixel — no contour tracing and
no point-in-polygon test. The transform is built lazily, only when something
actually falls outside.

**Merging consumes each endpoint once,** shortest gap first, re-deriving
candidates after every merge so chains (A–B–C) resolve across passes. Closed
rings and multi-polygon labels never participate, matching the editor.

**Cross-category pairs are left alone.** The editor also snaps endpoints across
categories, but that relies on a visible indicator and one-step undo, neither of
which exists at import time.
