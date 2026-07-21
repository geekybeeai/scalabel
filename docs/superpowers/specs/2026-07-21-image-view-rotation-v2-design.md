# 2D Viewer: Display-Only 90° Image Rotation — Design v2

Date: 2026-07-21
Status: Approved
Supersedes: `2026-06-15-image-view-rotation-design.md` (same core design; this
revision adds the tools and overlays introduced on the canvas since June and
the keyboard shortcut, and records the decision to reimplement rather than
revive the stashed June code).
Scope: 2D image annotator (embedded lane tool). No 3D changes. No backend
changes.

## Overview

Two toolbar buttons (and R / Shift+R) rotate the **view** of the image in 90°
steps so the annotator can turn the image to whatever orientation makes drawing
easier. The rotation is **display-only**:

- Annotation coordinates are always stored and exported in the **original
  (unrotated) image frame**. The exported JSON and the BaseMap backend
  round-trip are completely unaffected — annotating rotated then saving yields
  exactly the same data as annotating unrotated.
- Rotation is view state (`ImageViewerConfigType`), resets on reload, and is
  never serialized into annotation data.

## Prior work (why v2)

The June design was approved and partially implemented, but the code was never
committed — it survives only as `stash@{0}: rotate-canvas` on
`feature-opimization` (base commit `79b7a0f8`). Since then the canvas gained
the cut tool, delete-segment tool, Ctrl+click / lasso / rectangle batch
delete, endpoint snap-merge, marching-ants overlays, and viewport culling.

**Decision: reimplement on the current branch, using the stash as reference
only.** The stash's core pieces (rotation math, dim-swap, context transform,
mouse funnel changes) are correct and are re-applied by hand; the stash itself
is not popped (it is based on a stale tree and carries unrelated test/doc
changes).

## Design decisions (locked)

- **Semantics:** user annotates in the rotated view; saved/exported JSON is
  always in the original image frame.
- **Controls:** Rotate left (90° CCW) and Rotate right (90° CW) toolbar
  buttons, plus **R** = clockwise and **Shift+R** = counter-clockwise.
- **Granularity:** 0/90/180/270 only. Exact and lossless — no resampling.
- **Persistence:** none. Resets to 0 on reload.
- **All tools work at any rotation** — drawing, vertex editing, curves,
  cut, delete-segment, batch delete (Ctrl+click / lasso / rectangle),
  snap-merge, undo/redo, zoom/pan/line width.
- **Rotation is inert mid-gesture:** buttons and keys do nothing while a
  drawing is in progress or a delete-segment preview countdown is running
  (same guard style as the curves-only arming guards).

## Architecture

### Rotation state

`rotation?: number` (0 | 90 | 180 | 270, default 0) on
`ImageViewerConfigType` in `app/src/types/state.ts`. Buttons/keys dispatch
`changeViewerConfig(viewerId, { ...config, rotation: (r ± 90 + 360) % 360 })`.

### Coordinate transform (pure helpers)

In `app/src/view_config/image.ts`, with `W × H` the ORIGINAL image size and
rotation clockwise:

| rotation | image → display (`rotatePoint`) | display → image (`unrotatePoint`) |
|---|---|---|
| 0 | `(x, y)` | `(x', y')` |
| 90 | `(H − y, x)` | `(y', H − x')` |
| 180 | `(W − x, H − y)` | `(W − x', H − y')` |
| 270 | `(y, W − x)` | `(W − y', x')` |

Round-trip identity holds for every rotation.

### How rotation is applied

1. **Canvas sizing (dim swap).** `updateCanvasScale` computes the canvas size,
   letterboxing, and `displayToImageRatio` from the swapped dimensions
   (`H × W`) when rotation is 90/270. The canvas DOM element stays
   axis-aligned, so `getBoundingClientRect`-based cursor-focal zoom and pan
   are untouched.
2. **Image draw.** `drawImageOnCanvas` wraps the bitmap draw in
   `save → translate → rotate → restore`, drawing into the un-rotated content
   box (swapped w/h for 90/270). Works for both the ImageBitmap fast path and
   the HTMLImageElement fallback.
3. **Label + overlay draw.** `Label2dCanvas.redraw` applies the same
   `translate + rotate` (an `applyRotation` helper) to the label context AND
   the control (hit-test) context around **all** painting — the drawable list
   AND the overlays drawn after it (delete-segment marching ants, pick halos,
   lasso/rectangle selection overlay). Overlay code needs zero changes: its
   image-frame points rotate with the layer. Because the control canvas is
   rotated identically, color-coded hit-testing works rotated with no extra
   code.
4. **Pointer input.** `getMousePos` passes the display-frame point through
   `unrotatePoint` before returning. This is the single funnel used by
   drawing, vertex drag, cut clicks, delete-segment picks, Ctrl+click
   marking, and the lasso — so every consumer receives original-frame
   coordinates automatically.
5. **Hit-test probe.** `fetchHandleId` reads control-canvas pixels via
   `getImageData`, which ignores context transforms — so it probes at
   `rotatePoint(mousePos)` (the rotated position) instead.
6. **Viewport culling** (the `viewScale > 2` bounds optimization in
   `redraw`) is bypassed while rotation ≠ 0: its bounds are computed in the
   unrotated frame. Correctness over the optimization; rotation is expected
   to be used at moderate zoom.

### Why the tools need no changes

- Cut / delete-segment / lasso scans read stored redux points (original
  frame) and compare against the original-frame click from `getMousePos`.
- Their pixel tolerances (`CUT_CLICK_RADIUS_PX / displayToImageRatio`) are
  scalars — rotation-invariant.
- Endpoint snap-merge and the snap indicator live inside drawable
  mouse/draw paths — covered by the funnel and the context transform.
- The staleness guards (segment delete) compare original-frame vertices —
  unaffected.

### Keyboard shortcut

In `Label2dCanvas.onKeyDown`: `r` → rotate CW, `R` (shift) → rotate CCW.
Skipped while focus is in an input/textarea (same guard as the Space
image-toggle) and while a drawing is in progress or a delete-segment preview
is pending. R is currently unbound elsewhere; the 3 s key-arm window may arm
it, which is harmless (nothing consumes an armed R).

## Components / files

| File | Change |
|------|--------|
| `app/src/types/state.ts` | `rotation?: number` on `ImageViewerConfigType`. |
| `app/src/view_config/image.ts` | `rotatePoint` / `unrotatePoint`; dim-swap in `updateCanvasScale`; context rotation in `drawImageOnCanvas`. |
| `app/src/components/image_canvas.tsx` | Pass the config's rotation to `drawImageOnCanvas`. |
| `app/src/components/label2d_canvas.tsx` | `applyRotation` around ALL label/control painting (labels + overlays); `unrotatePoint` in `getMousePos`; rotated probe in `fetchHandleId`; culling bypass; R / Shift+R keys. |
| `app/src/components/viewer2d.tsx` | Rotate-left / rotate-right toolbar buttons with mid-gesture guard. |
| `docs/polyline-feature-map.md` | Re-add rotation entries (currently stale — they describe the stashed code); add the new modules. |

No changes to: drawables, `commit2DLabels`, `draw_history.ts`, the cut /
delete-segment / select tool logic, export/import, or the backend.

## Edge cases

- **180°** — no dim swap, both axes flip.
- **Non-square images** — swapped dims feed letterboxing; correct aspect.
- **Pan position after rotating** — `displayLeft`/`displayTop` are clamped to
  the new bounds by the existing scroll logic; the view may need a nudge but
  nothing breaks.
- **Rotating while a label is selected** — selection is by id in redux;
  drawables rebuild and repaint rotated. No special handling.
- **Predictions from the host** — original-frame coords; rotate with the view
  automatically.
- **Item navigation while rotated** — rotation is per-viewer config and
  survives item switches within a session (like zoom); resets on reload.

## Verification

- **Unit (node-env recipe):** round-trip identity for all four rotations;
  corner mappings (`(0,0)` at 90° → `(H, 0)` etc.); dim-swap sizing.
- **Runtime (repo `/verify` CDP skill):**
  - Draw a polyline → rotate through all four steps → line stays glued to the
    image features.
  - Draw while rotated → rotate back to 0° → line is where it should be.
  - Drag a vertex, C-curve a segment, cut a line, delete a segment, and
    lasso-mark lines — all while rotated.
  - Zoom + pan while rotated.
  - Export and diff: coordinates identical to the same annotation drawn
    unrotated.
- `npx tsc --noEmit` + lint (filtering pre-existing CRLF prettier noise).

## Out of scope

- Arbitrary-angle rotation (needs resampling + sub-pixel handling).
- Persisting rotation across reloads or into exports.
- 3D / point-cloud viewers.
