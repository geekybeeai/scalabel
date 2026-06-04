# Smooth Zoom/Scroll/Pan Rendering for Large Images — Design

Date: 2026-06-04
Status: Approved (pending spec review)
Scope: 2D image annotator only

## Problem

On very large images (~27 MB file, large pixel dimensions) the 2D editor lags
badly while zooming/scrolling, and the image **goes blank** during the gesture.

Profiling (Chrome, no throttling, after the full pipeline up to DWG gen):
- **JS heap: 47.6 MB** — memory is NOT the bottleneck; the image lives on
  GPU/bitmap, not the JS heap. Memory work is explicitly out of scope.
- **INP 152 ms**, with **keyboard (zoom) interactions 120–232 ms** vs a single
  **pointer interaction at 8 ms**. The cost is per-interaction main-thread work
  (image re-blit + label/control redraw), concentrated on zoom keystrokes.
- LCP 5.10 s (slow first paint) — noted but **out of scope** (see Non-goals).

### Root cause of the blank flash
The image canvas is cleared synchronously but redrawn one frame later:
1. A zoom step changes `viewScale` → React re-renders `ImageCanvas`;
   `cloneElement` sets `width/height` ([image_canvas.tsx:105](../../../app/src/components/image_canvas.tsx))
   and `updateScale`→`updateCanvasScale` sets `canvas.width` again. Assigning
   `canvas.width` **wipes the canvas**.
2. The actual blit (`redraw`→`drawImageOnCanvas`) is **deferred to a
   `requestAnimationFrame`** ([viewer.ts:107](../../../app/src/components/viewer.ts)) — the
   "batch rapid updates" optimization.

Between the synchronous clear and the deferred redraw the canvas is blank.
Normally ~1 frame (invisible); but the blit takes 120–232 ms and during
continuous zoom redraws queue behind the clears, so the blank is visible the
whole gesture. The RAF batching was a reasonable *throughput* optimization but
traded a freeze for a blank — both avoidable.

## Goals
- Zoom/scroll/pan feels smooth (target: interaction latency back toward the
  ~8 ms pointer figure, no sustained jank).
- **No blank** at any point during a gesture — the last good frame stays
  visible until the next is ready.
- Full crispness restored when the gesture stops.

## Non-goals
- Initial load / LCP (the 5.1 s first paint).
- Memory/heap work.
- 3D / point-cloud path.
- Changing annotation data, export, or coordinate semantics.

## Locked decisions
- **Speed over sharpness while moving** — a brief resolution drop during an
  active gesture is acceptable; it snaps back to crisp on idle.
- **No-blank is a first-class requirement**, not just speed.

## Existing optimizations (keep — do not redo)
RAF-batched zoom dispatch (viewer2d `_zoomRAFPending`), ImageBitmap cache,
adaptive up-res (1× above 3× zoom), 4096px canvas cap, label viewport culling
(>2× zoom), label-redraw RAF throttle, `shouldComponentUpdate` dirty-checks,
componentDidUpdate redraw RAF, type/category visibility filters.

## Approach (progressive interaction rendering)

A transient **interaction state** drives a cheap "in-motion" render path and a
crisp "idle" pass. Implemented as layered, independently-shippable requirements
ordered by value/risk:

### R1 — Eliminate the blank (highest priority, lowest risk)
Never leave the canvas empty between frames. During a gesture the image redraw
must happen **without a visible clear→draw gap**: keep the previously painted
frame on screen until the new frame is drawn (e.g. redraw synchronously with the
resize so clear+blit land in the same commit, before paint). This alone removes
the flash even before any speed work.

### R2 — Interaction state module
A small, **non-Redux** transient signal (module singleton, e.g.
`app/src/common/interaction_state.ts`, or a field on `Session`):
- `notifyGesture()` — called by every zoom/scroll/pan handler; sets
  `active = true` and (re)starts a ~150 ms idle timer.
- When the idle timer fires: `active = false` and notify subscribers to do the
  crisp pass.
- `isActive()` read by the canvases at redraw time.
Kept out of Redux to avoid dispatch/re-render churn on every gesture frame.

### R3 — Cheap in-motion image render
While `isActive()`:
- Render the image at **reduced backing resolution** (force up-res to 1, and
  allow a downscale factor ~0.6–0.75× of normal) so each blit is cheap/fast.
- Hold the backing size **stable for the duration of the gesture** where
  possible (avoid per-step reallocation of a 4096² canvas).

### R4 — Skip the control canvas during motion
The control/hit-detection canvas is invisible (used only for `getImageData`
hit-testing) and is ~⅓ of per-frame canvas work. Skip its redraw while
`isActive()`; rebuild it on idle. Hit-testing isn't needed mid-gesture.

### R5 — Crisp idle pass
~150 ms after the last gesture: redraw the image at full backing resolution,
rebuild the control canvas, and (if R7 used) reset any CSS transform.

### R6 — RAF-batch pan
The pan/drag handler ([viewer2d.tsx `onMouseMove`](../../../app/src/components/viewer2d.tsx))
dispatches to Redux on **every** mousemove (zoom is already RAF-batched, pan is
not). Wrap pan updates in the same RAF-batching pattern as zoom.

### R7 — (Stretch) CSS-transform preview during motion
Optional enhancement: during a gesture, apply `transform: scale()/translate()`
to the already-painted canvas (pure GPU, no blit, no clear) for instant
feedback, then do the real blit only on idle (R5). Removes the per-frame blit
entirely. Deferred because the focal-point transform math must exactly mirror
the existing zoom offset computation; ship R1–R6 first and add R7 only if
motion still isn't smooth enough.

### R8 — `willReadFrequently` on the control context
Add `{ willReadFrequently: true }` to the control canvas `getContext("2d")` so
the per-interaction `getImageData` hit-test ([label2d_canvas.tsx])
doesn't fight GPU readback. Small, independent correctness/perf win.

## Components / files touched
- `app/src/common/interaction_state.ts` (new) — R2.
- `app/src/components/image_canvas.tsx` / `app/src/view_config/image.ts` —
  R1, R3, R5 (redraw timing, in-motion resolution).
- `app/src/components/label2d_canvas.tsx` — R4 (skip control canvas), R8.
- `app/src/components/viewer2d.tsx` — R6 (pan batching), wire `notifyGesture`
  into zoom + pan handlers.
- `app/src/components/viewer.ts` — R1 (redraw timing) if the gap fix lands here.

## Data flow
gesture event → `interactionState.notifyGesture()` (active=true, restart timer)
→ existing Redux `changeViewerConfig` (RAF-batched) → canvases redraw reading
`isActive()` → cheap in-motion render (R3/R4), last frame never blanked (R1).
Idle timer fires → active=false → subscribers do crisp pass (R5).

## Edge cases
- Gesture shorter than 150 ms → one cheap frame then crisp pass; fine.
- Frame switch / new image mid-interaction → cancel idle timer, force crisp.
- `isActive()` true but no gesture for a long time (stuck) → idle timer is the
  single source of truth for clearing it; every gesture restarts it.
- Very fast zoom past the 4096 cap → unchanged cap behavior; R3 just lowers
  resolution further during motion.

## Testing / verification
- **Runtime (primary):** headless Chrome (puppeteer-core + installed Chrome) on
  a large image: drive repeated zoom keystrokes, capture screenshots at several
  points during the gesture to confirm (a) **no blank frame**, (b) crisp image
  after settling. Compare INP/interaction timings before/after via the
  Performance panel or `PerformanceObserver` for `event`/`interaction`.
- Confirm hit-testing (label hover/select) still works after idle pass (control
  canvas rebuilt).
- Confirm pan is smooth and labels stay correctly positioned after R6.

## Future (out of this spec)
- **B. ROI windowing** — canvas always viewport-sized, blit only the visible
  region; removes giant per-step reallocation, stays crisp always. Bigger,
  touches shared coordinate mapping.
- **C. Tiled rendering** — cached bitmap tiles, draw visible tiles only; for
  genuinely massive images. Overkill given current heap size.
