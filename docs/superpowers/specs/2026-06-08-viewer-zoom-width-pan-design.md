# 2D Viewer: Cursor-Zoom, Line-Width Buttons, Map-Like Pan — Design

Date: 2026-06-08
Status: Approved
Scope: 2D image annotator (embedded lane tool). No 3D/point-cloud changes.

## Overview

Three independent improvements to the 2D viewer:
1. **Zoom at the cursor** — pinch/Ctrl+scroll zoom should keep the point under the
   cursor fixed (currently it drifts / re-centers).
2. **Line-width buttons** — toolbar buttons to make polylines render thicker/thinner
   (display-only aid, not saved in annotation data).
3. **Map-like panning** — drag empty space to pan (mouse), double-click-drag to pan
   (trackpad), keeping Ctrl/Cmd+drag as a fallback — without breaking drawing/editing.

Each feature is independently shippable. Feature 3 is the highest-risk (it touches the
drawing/editing input path) and requires careful runtime verification.

---

## Feature 1 — Zoom at the cursor

### Problem (root causes, from code exploration)
- `viewer2d.tsx onWheel` sets the zoom focal point from **stale** `this._mX/this._mY`
  (updated only in `onMouseDown`/`onMouseMove`). A trackpad pinch fires `wheel`
  events with **no** mouse-move, so the focal point locks to the last cursor spot →
  zoom drifts.
- `viewer2d.tsx zoom()` has a "blank region" branch that **unconditionally sets
  `displayLeft`/`displayTop = 0`** when the image is smaller than the viewport in a
  dimension, discarding the cursor-focal pan and forcing centering at low zoom.

The focal-point formula itself is correct:
`displayLeft = zoomRatio * (offset.x + config.displayLeft) - offset.x` (and y).

### Design
1. **Live cursor focal point.** In `onWheel`, compute the offset from the wheel
   event: `rect = this._container.getBoundingClientRect();
   this._pendingZoomOffset = new Vector2D(e.clientX - rect.left, e.clientY - rect.top)`.
   (Native `WheelEvent` carries `clientX/clientY`.)
2. **Clamp instead of center.** In `zoom()`, replace the blank-region zeroing with
   clamping that preserves the focal-point pan but keeps the image within bounds.
   Using CSS-pixel displayed image size `iwCss = displayRect.width * newScale`,
   `ihCss = displayRect.height * newScale` (the same basis `updateCanvasScale` uses):
   - If `iwCss >= rect.width`: clamp `displayLeft ∈ [rect.width - iwCss, 0]`.
     Else (image narrower than viewport): clamp `displayLeft ∈ [rect.width - iwCss, 0]`
     too — which collapses to centering only when there is no spare room. Practically:
     `displayLeft = clamp(displayLeft, Math.min(0, rect.width - iwCss), Math.max(0, rect.width - iwCss))`.
   - Same for `displayTop` with heights.
   The exact min/max expression is finalized in the plan; the intent is: cursor-focal
   zoom at every zoom level, never drag the image off into blank space.
3. Zoom **buttons** keep using viewport-center as the focal point (unchanged).

### Files
- `app/src/components/viewer2d.tsx` (`onWheel`, `zoom`).

### Verification
- Trackpad pinch and Ctrl+scroll at several cursor positions keep the point under the
  cursor fixed (runtime: dispatch wheel with ctrlKey + clientX/Y, assert the image
  point under the cursor stays put within a few px).

---

## Feature 2 — Line-width buttons (display-only)

### Design
- Add `lineWidthMultiplier?: number` to `ImageViewerConfigType` in
  `app/src/types/state.ts` (default treated as `1`). It is **view state**, not part of
  the exported annotation JSON.
- Thread it along the existing `viewScale` path:
  `label2d_canvas.redraw` reads `config.lineWidthMultiplier` →
  `label2d_list.redraw(..., lineWidthMultiplier)` →
  `Label2D.draw(..., lineWidthMultiplier?)`. Only `polygon2d.draw` consumes it; the
  abstract `draw` gets an optional trailing param so other shapes need no change
  (JS ignores the extra arg; TS allows overrides to omit trailing optional params).
- In `polygon2d.draw`, the **visible** stroke becomes
  `edgeStyle.lineWidth = Math.max(1, base * (1/√viewScale) * multiplier)`.
  The **CONTROL** (hit-detection) width is unchanged so clicking stays easy.
- **Three toolbar buttons** in `viewer2d.getMenuComponents` mirroring the zoom
  buttons: thicker (`+0.1`), thinner (`−0.1`), reset (`→1`). Clamp to **[0.5, 2.5]**.
  Each dispatches `changeViewerConfig(viewerId, { ...config, lineWidthMultiplier })`,
  which triggers the normal redraw.
- Icons: Material-UI (e.g. `AddIcon`/`RemoveIcon` or line-weight icons) + a reset icon;
  finalized in the plan. Same Tooltip/IconButton/`viewer_button` styling as zoom.

### Files
- `app/src/types/state.ts`, `app/src/drawable/2d/polygon2d.ts`,
  `app/src/drawable/2d/label2d_list.ts`, `app/src/components/label2d_canvas.tsx`,
  `app/src/components/viewer2d.tsx`.

### Verification
- Click thicker/thinner/reset; confirm polyline stroke visibly changes and resets;
  confirm exported JSON (`/getExport`) is unaffected; confirm hit-testing/selection
  still works.

---

## Feature 3 — Context-sensitive (map-like) panning

### Current behavior (from exploration)
- Pan today = **Ctrl/Cmd + drag** (gated in `viewer2d.onMouseMove`).
- Drawing = **click-click** (mouse-down then mouse-up adds a vertex); a new polyline
  starts on mouse-down over empty space.
- Editing = left-**drag** on a vertex (RESHAPE) or edge (MOVE) of a selected label.
- Empty-space drag currently begins drawing — this is what we repurpose.
- Events propagate canvas → container, so both `label2d_canvas` (hit-test + draw) and
  `viewer2d` (pan) see each pointer event.

### Behavior (target)
- **Empty space + click** (release within ~5px) → place/continue a polyline point
  (unchanged drawing).
- **Empty space + drag** (>~5px) → **pan** the image; no point is drawn.
- **On a label/vertex/edge + drag** → edit (unchanged).
- **Double-click then drag** → pan anywhere (even over labels), via a short
  post-double-click "pan window" (trackpad-friendly).
- **Ctrl/Cmd + drag** → pan anywhere (kept).
- Cursor: `grab`/`grabbing` while panning, normal otherwise.

### Architecture
A small coordination module `app/src/common/pointer_pan_state.ts` (analogous to the
existing `interaction_state.ts`) holds transient per-gesture flags so the two sibling
components agree on intent:
- `armEmptyDrag(downX, downY)` — set by `label2d_canvas.onMouseDown` when the hit-test
  finds **no** label/handle (FREE or DRAW state). It records the down position and that
  a drag here is allowed to pan, and the canvas **defers** the draw action.
- `panWindowUntil(ts)` / `isInPanWindow()` — opened by `onDoubleClick`.
- `markPanned()` / `didPan()` — set by the viewer pan handler once movement exceeds the
  threshold; read by `label2d_canvas.onMouseUp` to decide draw-vs-discard.
- `reset()` — on mouse-up.

Flow (left button over the canvas):
1. `label2d_canvas.onMouseDown`: hit-test. If on a label/handle → existing behavior. If
   empty (FREE/DRAW) → `armEmptyDrag(pos)` and **do not** create the label/vertex yet.
2. `viewer2d.onMouseMove` (bubbled): if `_mouseDown` and
   (`e.ctrlKey||e.metaKey` **or** `armed-empty-drag` **or** `isInPanWindow()`) and the
   pointer moved beyond the threshold → run the existing RAF-batched pan (with the
   Feature-1 clamp) and `markPanned()`.
3. `label2d_canvas.onMouseUp`: if `didPan()` → discard the deferred draw; else → perform
   the deferred draw (start polyline / add the vertex at the click). Then `reset()`.
4. `viewer2d.onMouseUp`: ensure flags cleared.

The deferral means the draw-start moves from "mouse-down on empty" to "mouse-up without
drag." All other drawing/editing (click-click vertices, vertex drag, edge move, Enter to
finish, C/D keys) is unchanged.

### Files
- `app/src/common/pointer_pan_state.ts` (new),
- `app/src/components/label2d_canvas.tsx` (hit-test, arm/defer, draw-on-click-up),
- `app/src/drawable/2d/label2d_handler.ts` / `polygon2d.ts` (cooperate with deferral),
- `app/src/components/viewer2d.tsx` (pan gating incl. empty-drag + double-click window;
  `onDoubleClick` opens the window; Ctrl+drag retained; cursor feedback).

### Edge cases
- Movement threshold (~5px in CSS px) distinguishes click from drag; jitter while
  clicking still places a point.
- Double-click without drag keeps existing behavior (does not pan; does not break
  any double-click-to-finish behavior if present).
- Pan still clamps to image bounds (shared with Feature 1).
- Ctrl+drag over a label still pans (does not edit), as today.

### Verification (must be runtime, by driving the editor)
Confirm **no regression** in drawing/editing AND the new pan works:
- Draw a multi-point polyline (clicks), finish with Enter.
- Select it; drag a vertex; drag an edge; delete a vertex (D); curve a segment (C).
- Pan by: empty-space drag, double-click-drag (incl. over a label), Ctrl+drag.
- Confirm empty-space *click* still adds a point (not a pan) and empty-space *drag*
  pans (no stray 1-point label created).

---

## Cross-cutting

- All three are display/interaction changes; none alter the exported annotation data.
- Builds verified with `tsc --noEmit` + `npm run build`; behavior verified at runtime
  via headless Chrome (puppeteer-core) against the running `test2` project.
- Nothing is committed by the implementer; the user commits manually.

## Out of scope / future
- Two-finger trackpad drag → pan (the user requested double-tap-drag instead).
- Applying `lineWidthMultiplier` to boxes/custom shapes (polylines only for now).
- Persisting width/zoom preferences across sessions.
