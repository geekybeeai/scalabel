# 2D Viewer: Display-Only 90° Image Rotation — Design

Date: 2026-06-15
Status: Approved
Scope: 2D image annotator (embedded lane tool). Polyline/polygon annotation.
No 3D/point-cloud changes. No backend changes.

## Overview

Add two toolbar buttons that rotate the **view** of the image by 90° (left / right)
so the annotator can turn the image to whatever orientation makes drawing easier.
The rotation is **display-only**:

- Annotation coordinates are always stored and exported in the **original
  (unrotated) image frame**. The exported JSON and the BaseMap backend round-trip
  are completely unaffected.
- Rotation is **view state** (lives in the image viewer config). It resets on
  page reload / reopening the editor and is never written to the annotation data.

Result: the user rotates freely while annotating; the data behaves exactly as if
no rotation happened.

## Design decisions (locked)

- **Controls:** two buttons — Rotate left (90° CCW) and Rotate right (90° CW).
- **Persistence:** temporary view aid; resets on reload; never serialized to JSON.
- **Approach:** rotation is part of the **view transform** (Approach A). The canvas
  DOM stays axis-aligned so the existing cursor-focal zoom and pan are untouched;
  only the canvas pixel dimensions swap for 90°/270°.
- **Mechanism:** the image and label canvases are drawn through a canvas-context
  rotation (`translate` + `rotate`); pointer input is run through the **inverse**
  rotation once before the existing scale-based `toImageCoords`. The existing
  `toCanvasCoords`/`toImageCoords` stay scale-only.
- **Granularity:** only 0/90/180/270 (each click steps 90°). Exact and lossless —
  no pixel resampling, no sub-pixel error.

---

## Architecture

### Rotation state

Add `rotation?: number` (degrees, one of `0 | 90 | 180 | 270`, default `0`) to
`ImageViewerConfigType` in `app/src/types/state.ts`. It is view state, alongside
`viewScale` / `displayLeft` / `displayTop`, and is **not** part of the exported
annotation JSON (which serializes label shapes, not viewer configs).

The two buttons dispatch `changeViewerConfig(viewerId, { ...config, rotation })`
with `rotation = (current ± 90 + 360) % 360`.

### The coordinate transform

Let `k = rotation / 90` and the original image be `W × H`. The **displayed**
image dimensions are:

- `k = 0` or `k = 2` → `W × H`
- `k = 1` or `k = 3` → `H × W` (width/height swapped)

Vertex mapping between original-frame `(x, y)` and rotated/display-frame
`(x', y')` (continuous coordinates; exact):

| k (CW) | image → display | display → image (inverse) |
|---|---|---|
| 0 | `(x, y)` | `(x', y')` |
| 90 | `(H − y, x)` | `(y', H − x')` |
| 180 | `(W − x, H − y)` | `(W − x', H − y')` |
| 270 | `(y, W − x)` | `(W − y', x')` |

These are implemented as two pure helpers in `app/src/view_config/image.ts`:

- `rotatePoint(p, rotation, W, H): Vector2D` — original → display frame.
- `unrotatePoint(p, rotation, W, H): Vector2D` — display → original frame.

`unrotatePoint(rotatePoint(p)) === p` for every `k` (round-trip identity).

### How rotation is applied

1. **Canvas sizing (the dimension swap).** `updateCanvasScale`
   (`app/src/view_config/image.ts`) currently derives `displayToImageRatio` and the
   canvas element size from `image.width` / `image.height`. When `rotation` is
   90/270 it uses the **swapped** dimensions (`image.height`, `image.width`) so
   letterboxing/centering and the ratio are computed for the rotated aspect. The
   canvas DOM element stays axis-aligned (it is just sized to the rotated image).

2. **Image draw.** `drawImageOnCanvas` applies `ctx.save() → translate → rotate(k·90°)
   → drawImage → restore` so the bitmap fills the rotated-dimension canvas. (90°
   rotation needs no smoothing/resampling — it is a rigid transform.)

3. **Label draw.** Before the drawable list renders, the label context **and** the
   control (hit-detection) context get the same `translate + rotate`. The drawables
   continue to draw at their normal `toCanvasCoords` positions; the context rotation
   turns the whole layer. Because the control canvas is rotated identically,
   color-coded hit-testing keeps working in the rotated view with no extra code.

4. **Pointer input.** When converting a mouse event to an image coordinate, the
   pointer (already mapped into the rotated/display frame by the existing pan/zoom
   math) is passed through `unrotatePoint(...)` before being stored. Every click is
   therefore recorded in the **original** image frame.

### Composition with zoom/pan

`displayLeft` / `displayTop` / `viewScale` and the cursor-focal wheel zoom operate
on the **axis-aligned** canvas DOM element via `getBoundingClientRect` — none of
that changes. The only adjustment is that the focal-point clamp and any image-size
reads use the rotated (swapped) dimensions. Rotate → zoom → pan compose naturally.

---

## Components / Files

| File | Change |
|------|--------|
| `app/src/types/state.ts` | Add `rotation?: number` to `ImageViewerConfigType`. |
| `app/src/view_config/image.ts` | `rotatePoint` / `unrotatePoint` helpers; rotation-aware `updateCanvasScale` (dimension swap); `drawImageOnCanvas` context rotation. |
| `app/src/components/image_canvas.tsx` | Read `rotation` from the viewer config; pass it to canvas sizing + image draw. |
| `app/src/components/label2d_canvas.tsx` | Apply the context rotation to the label + control draw; run pointer→image through `unrotatePoint`. |
| `app/src/components/viewer2d.tsx` | Two rotate buttons (RotateLeft / RotateRight `@material-ui/icons`), dispatching the new `rotation`. |

No changes to: drawables (`polygon2d.ts` etc.), `commit2DLabels`, undo/redo
(`draw_history.ts`), export (`server/export.ts`), or the BaseMap backend — they all
operate purely in the original image frame.

---

## Edge cases

- **Editing existing lines while rotated** — works automatically: hit-testing uses
  the control canvas, which is rotated by the same transform.
- **180°** — no dimension swap (just both axes flipped). 90°/270° swap W↔H.
- **Non-square images** — the swapped dimensions feed `updateCanvasScale`, so
  letterboxing is correct for the rotated aspect.
- **Pan after rotate** — `displayLeft`/`displayTop` are re-clamped to the rotated
  bounds; nothing breaks (the view may need a pan nudge).
- **Reset on reload** — viewer config rebuilds, `rotation` defaults to 0.
- **Predictions loaded from the host** — they are stored in original-frame image
  coords, so they rotate with the view automatically (no special handling).

## Verification

- **Unit:** round-trip identity `unrotatePoint(rotatePoint(p, k), k) ≈ p` for
  k ∈ {0,90,180,270}; plus known mappings (e.g. `(0,0)` at 90° → `(H, 0)`,
  image corner sanity).
- **Runtime (drive the editor):**
  - Draw a polyline → rotate left/right → the line turns with the image, stays
    aligned to it.
  - Draw a new line **while rotated** → rotate back to 0° → the line sits where it
    should in the upright image.
  - Select and drag a vertex while rotated → edits land correctly.
  - Zoom (cursor wheel) and pan while rotated → both still work.
  - Export (`/getExport`) and confirm vertex coordinates are in the **original,
    unrotated** frame (identical to drawing the same line without rotating).
- Builds verified with `tsc --noEmit` + lint. Nothing committed by the implementer
  beyond this design doc; the user commits code manually.

## Out of scope / future

- Arbitrary-angle rotation (would require pixel resampling + sub-pixel coordinate
  handling). Only 90° steps here.
- Persisting rotation across reloads or into the exported JSON.
- Rotation for 3D / point-cloud viewers.
