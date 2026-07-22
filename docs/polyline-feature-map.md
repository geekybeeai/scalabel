# Polyline / Polygon Feature Map (2D annotator)

A "read these first" guide for any agent working on 2D **polyline/polygon** features
in this fork (the embedded lane-annotation tool). Grouped by *what you're touching* →
*which files to open*. Anchors are **symbol names** (functions/classes), not line
numbers, since lines drift. Open the file, search the symbol.

> Golden rule: **annotation coordinates are ALWAYS stored/exported in the ORIGINAL
> image pixel frame.** The BaseMap backend overlays them on the original image and
> builds the DWG from them. Any view feature (zoom/pan/rotation) must keep the stored
> coords in that frame.

---

## 1. The drawable — geometry, drawing, editing
Open when: changing how a line is drawn, edited, its vertices/curves, merge, validity.

- `app/src/drawable/2d/polygon2d.ts` — **`Polygon2D`** (the line/polygon). Key symbols:
  `_points` (PathPoint2D[]), `Polygon2DState` (FREE/DRAW/FINISHED/RESHAPE/MOVE),
  `isDrawing`, `onMouseDown/Move/Up` (**C** held/armed + click a MID handle =
  convert segment to curve; one-way — clicking a CURVE point always drags),
  `onKeyDown` (**D**=delete vertex, **Enter**=finish),
  `addVertex`, `deleteVertex`, `shapes()` (**skips MID points**),
  `updateShapes` (rebuilds `_points` from state, **reconstructs MID midpoints**),
  `initTempLabel`, `mergeWith` (endpoint snap/merge), `isValid`. `_closed` = polygon vs
  polyline.
- `app/src/drawable/2d/path_point2d.ts` — `PathPoint2D`, `PathPointType` (LINE/MID/CURVE),
  point styles.
- `app/src/drawable/2d/label2d.ts` — base **`Label2D`**: `editing`, `temporary`, `type`,
  `labelId`, `item`, `label`, `shapes()`, `setManual`, `draw`, `updateState`.
- `app/src/drawable/2d/polygon2d_boundary_cloner.ts` — segment-clone modifier (advanced;
  rarely needed for lanes).
- `app/src/drawable/2d/polyline_cut_geometry.ts` — pure cut-site math:
  `findCutSite` (nearest-span projection, curve/endpoint guards, vertex snap),
  `buildCutHalves`. Also the delete-segment geometry: `DeleteSitePick`,
  `normalizeDeletePicks`, `buildSegmentDeletePieces` (survivors + doomed path).
  No Session/DOM imports — testable with the node-env recipe.
- `app/src/drawable/2d/polyline_cut.ts` — `performCut` (scan open polylines →
  split → delete+add original id, add new label → `drawHistory.recordCut`).
- `app/src/drawable/2d/polyline_segment_delete.ts` — delete-segment tool:
  `handleSegmentDeletePick` (two picks; end picks = trims),
  `commitPendingSegmentDelete` (atomic; records cut/edited/deleted per
  outcome — no new history kinds).

## 2. Drawable list + interaction controller
Open when: selection, the drawing lifecycle, mouse/keyboard routing, copy/paste.

- `app/src/drawable/2d/label2d_list.ts` — **`Label2DList`**: `_labels`/`_labelList`/
  `_selectedLabels`, `redraw()`, `updateState()`, `isDrawingInProgress()`,
  `cancelDrawing()`, `onDrawableUpdate()` (rAF-batched repaint via `subscribe`),
  `makeDrawableLabel2D` (factory: `POLYGON_2D`→closed, `POLYLINE_2D`→open).
- `app/src/drawable/2d/label2d_handler.ts` — **`Label2DHandler`** (view-controller):
  `onMouseDown` (starts a temp `Polygon2D` on empty space), `onKeyDown`,
  `pasteLabel` (copy/paste; records to draw history), `_highlightedLabel`,
  `updateState` (clears history on item change), commits via `commit2DLabels`.

## 3. Canvas rendering + pointer input + view transform
Open when: how lines paint, hit-testing, zoom/pan/rotation, screen↔image mapping.

- `app/src/components/label2d_canvas.tsx` — **label + control canvases**. `redraw()`
  (→ `label2dList.redraw`), `getMousePos` (client→image via `normalizeMouseCoordinates`,
  then `unrotatePoint`), `fetchHandleId` (**hit-test**: reads the control/color canvas),
  `onMouseDown/Move/Up`, `onKeyDown` (→ handler + `drawHistory.handleKeyboard`),
  `viewRotation`/`applyRotation`, `displayToImageRatio`, `_upResRatio`.
- `app/src/components/image_canvas.tsx` — the **image** canvas (`redraw` → `drawImageOnCanvas`,
  `updateScale`).
- `app/src/components/viewer2d.tsx` — the 2D viewer + **toolbar buttons**:
  (rendered into the NAVBAR via a portal — `drawable_viewer.tsx render` →
  `title_bar.tsx` `NAVBAR_TOOLS_SLOT_ID` slot; the buttons' logic stays here)
  `getMenuComponents` (zoom/width buttons), `getHistoryButtons` (undo/redo),
  `getRotationButtons`, `onWheel`/`zoom` (cursor-focal), pan, `changeLineWidth`,
  `rotateView`/`resetRotation` (±90° + reset-to-0 buttons, display-only;
  inert while drawing or during a delete-segment preview; **R** = CW /
  **Shift+R** = CCW in `label2d_canvas.tsx onKeyDown`, skipped while typing
  in inputs). Rotation is per-image: the `changeSelect` reducer
  (`functional/common.ts`) swaps `rotation` through the `itemRotations`
  memory map on item change, so each image keeps its own orientation.
- `app/src/view_config/image.ts` — **coordinate + canvas math**: `toImageCoords`/
  `toCanvasCoords` (scale-only), `normalizeMouseCoordinates`, `updateCanvasScale`
  (canvas sizing, `displayToImageRatio`, rotation dim-swap), `drawImageOnCanvas`,
  `getCurrentImageSize`, `rotatePoint`/`unrotatePoint`, constants `MIN_SCALE`/`MAX_SCALE`,
  `SCROLL_ZOOM_RATIO`(=1.03)/`ZOOM_RATIO`(=1.3).

## 4. Commit to state · undo/redo · actions/reducers
Open when: how a finished/edited/deleted line reaches redux; history behavior.

- `app/src/drawable/states.ts` — **`commit2DLabels`** = the single funnel from drawables →
  redux. New draw → `addNewLabel`; edit → `updateLabel` + `drawHistory.recordEdit`;
  invalid → `deleteInvalidLabel` (+ record). Helpers `polylineShapesChanged`, `lineSnapshot`.
  **Only path that turns drawing into `ADD_LABELS`.**
- `app/src/common/draw_history.ts` — **`DrawHistory`** (polyline-level undo/redo). Command
  kinds `created`/`edited`/`deleted`/`cut`; `undo`/`redo`/`recordUserLine`/`recordEdit`/
  `recordDeletion`/`recordDeletedLine`/`recordCut`/`canUndo`/`canRedo`/`handleKeyboard`/
  `reset`. `recordCut` records the split as one atomic command (single undo/redo step).
  Only tracks **user-touched** lines (never untouched predictions).
- `app/src/action/common.ts` — `addLabel`/`addLabelsToItem`/`deleteLabel`/`deleteLabels`/
  `changeShapes`/`changeViewerConfig`.
- `app/src/action/select.ts` — `deleteSelectedLabels`, `selectLabel`,
  `changeSelectedLabelsCategories`.
- `app/src/functional/common.ts` — reducers: `addLabels`/`addLabelsToItem` (**assigns fresh
  `order = maxOrder+1`, reuses label id**), `deleteLabels`/`deleteLabelsFromItem`,
  `changeShapes`.
- `app/src/functional/states.ts` — `makeLabel` (**default `manual: true`**).
- `app/src/functional/state_util.ts` — `getShapes`, `getNumLabels`, `getCategory`.

## 5. Types & constants
- `app/src/types/state.ts` — `LabelType` (id/type/shapes/order/manual/category/item),
  `ImageViewerConfigType` (viewScale/displayLeft/displayTop/lineWidthMultiplier/rotation),
  `PathPoint2DType`, `IdType`, `ShapeType`.
- `app/src/types/export.ts` — export JSON shapes (`poly2d`, `manualShape`).
- `app/src/const/common.ts` — `LabelTypeName.POLYGON_2D`/`POLYLINE_2D`, `Key.*`, `Cursor`.

## 6. Export / import (backend round-trip)
- `app/src/server/export.ts` — `DatasetExport`, poly2d serialization (what `/getExport` emits).
- `app/src/server/import.ts` — parse poly2d, `manualShape` → `manual`.

## 7. Toolbar / sidebar / alerts / image load
- `app/src/components/toolbar.tsx` — category list, delete, keyboard-shortcut legend;
  `deletePressed` (Delete key → `drawHistory.recordDeletion` + `deleteSelectedLabels`).
- `app/src/common/cut_state.ts` — cut-tool armed flag (+ change listeners);
  `app/src/components/cut_icon.tsx` — scissors icon + CSS cursor. Toolbar
  button in `viewer2d.tsx getCutButton`; click/cursor/Escape/context-menu
  wiring in `label2d_canvas.tsx`.
- `app/src/common/segment_delete_state.ts` — delete-segment phase machine
  (awaitFirst/awaitSecond/preview, mutually exclusive with cut mode);
  toolbar button in `viewer2d.tsx getDeleteSegmentButton`; picks/overlay/
  timer wiring in `label2d_canvas.tsx` (marching-ants preview, 3 s commit).
- "Curves only" sidebar checkbox — `showCurvesOnly` viewer-config flag
  (`toolbar.tsx` toggle → `label2d_canvas.tsx redraw` →
  `label2d_list.ts redraw` filter → `polygon2d.ts draw` curves-only branch,
  both canvases). Pure group finder: `app/src/drawable/2d/curve_groups.ts`
  (`curveGroupIndices`). Cut/delete-segment arming is guarded while on.
- `app/src/components/toolbar_category.tsx` — category rows, Show all / Show Tags.
- "Show image" checkbox + "Image opacity" slider — `hideImage` / `imageOpacity`
  viewer-config flags (`toolbar.tsx` → `image_canvas.tsx`: `redraw` blanks the
  layer on `hideImage`; CSS opacity on the image canvas dims it. Labels are on
  separate canvases, unaffected). Slider disabled while image hidden. Space
  also toggles `hideImage` (`label2d_canvas.tsx onKeyDown`, skipped while
  typing in inputs) — this REPLACES upstream's Space = toggle-checked-label
  binding. Both toggle rows render below the category list.
- `app/src/styles/label.ts` — `categoryStyle`, `alerts` toast style.
- `app/src/components/alert.tsx` + `app/src/components/label_layout.tsx` — alert toasts
  (`getAlerts`). Duplicate messages never stack: `common/alert.ts alert()`
  finds a visible toast with the same severity+message and fires
  `onAlertRepeat` instead, which shakes the existing toast and restarts its
  dismiss timer.
- `app/src/common/session_setup.tsx` — image loading (`loadImages`, `image.onerror`,
  retry + friendly error).

## 8. Tests
- `app/test/drawable/` — `polygon.test.ts`, `draw_history.test.ts`, and `util.ts`
  (harness: `initializeTestingObjects`, `drawPolygon`, `mouseMoveClick`, `keyDown`).
- `app/test/view_config/image_rotation.test.ts` — pure-math example.

**Running tests in this environment (important):**
- Drawable/component tests need the native `canvas` module + `redis-server`, which are
  usually **missing here** → those suites fail to load. Don't fight it; verify via
  `tsc`/`lint`/runtime.
- **Pure-logic** tests DO run with:
  `npx jest <file> --env=node --globalSetup=<noop> --globalTeardown=<noop>`
  (a `<noop>` is a JS file exporting a no-op function), which bypasses redis and
  jsdom's canvas. `app/test/setup/noop.js` is the ready-made noop globalSetup/
  globalTeardown for this recipe — it exports a **synchronous** noop
  (`module.exports = () => {}`), not an `async` one: babel-jest transpiles async
  arrows in `.js` files to regenerator-runtime code that fails inside jest's setup
  context, so keep it sync.
- `npx tsc --noEmit` and `npm run lint` always work. Lint has pervasive **pre-existing
  CRLF `prettier/prettier`** noise on Windows checkouts — filter it and compare a changed
  file's *non-prettier* rule violations to HEAD before assuming you introduced them.

## Gotchas that will save the next agent hours
- **Coords are original-frame, always.** View transforms (zoom/pan/rotation) never change
  stored/exported coordinates.
- **Mid-draw is not in redux.** A line only becomes an `ADD_LABELS` action at Enter/close
  via `commit2DLabels`; before that it's a transient `Polygon2D` in
  `Session.label2dList.selectedLabels[0]`.
- **`shapes()` drops MID points**; midpoints are reconstructed in `updateShapes`. Compare
  geometry by non-MID vertices.
- **Zoom/pan are CSS on the canvas DOM element** (`viewScale`/`displayLeft`/`displayTop`);
  the image↔canvas coord map (`toImageCoords`/`toCanvasCoords`) is **scale-only**.
- **`getBoundingClientRect` powers cursor-focal zoom/pan** → keep the canvas DOM
  axis-aligned (that's why rotation is done via context transform + dim-swap, not CSS
  rotate).
- **The dormant `redux-undo`** (in `configure_store.ts`) is wired but never triggered;
  the real undo/redo is `draw_history.ts`. Don't confuse them.
- **The artifact server, not the client, is the load bottleneck** (see the expired-URL /
  slow-download investigations): `session_setup.tsx image.onerror` + signed URLs.
- **Per-instance key maps die mid-click.** The select-on-click dispatch rebuilds
  drawables with fresh, empty `_keyDownMap`s (and the handler's pressed-key set can be
  cleared by the same dispatch), so gating a mousedown action on `this.isKeyDown(...)`
  silently fails. Read held keys from `common/keyboard_state.ts` (module-level, fed by
  Label2dCanvas's document listeners, rebuild-proof) — see the C/D checks in
  `polygon2d.ts onMouseDown`. Do NOT "fix" this by moving the action to bare keydown:
  C's gesture is convert-on-click **then drag to shape**; keydown-only conversion was
  tried and rejected (hair-trigger, no drag). And keep exactly ONE trigger per action —
  a keydown path plus a click path double-fires and undoes/corrupts the edit.
- **Trackpads physically cannot hold-key + click.** Laptop palm rejection ("disable
  touchpad while typing") suppresses taps while any key is held; auto-repeat keeps
  resetting the suppression timer, so C-held clicks never land and the click that
  finally does arrives after keyup. Hold-key+click gestures therefore also accept a
  recent plain press via the 3 s **arm window** (`armKey`/`isKeyArmed`/`consumeArmedKey`
  in `keyboard_state.ts`, one-shot, Ctrl/Meta chords excluded so Ctrl+C never arms):
  press C, release, click-drag within 3 s.
- **Trackpad drags are split gestures and collide with the double-click pan window.**
  A dblclick opens a 400 ms window during which any drag pans
  (`openPanWindow`/`inPanWindow` in `pointer_pan_state.ts`); trackpad users start
  drags with a double-tap, which used to feed the C+drag curve gesture into the pan
  path. `shouldDeferPointerDown` (same file) is the single gate: clicks on a label
  POINT — or on the body while that label still has a point handle highlighted —
  bypass the pan window. Related: C+tap's `lineToCurve` puts the control points at
  the 1/3 / 2/3 marks (out from under the cursor), and `Polygon2D.onMouseUp`
  deliberately KEEPS `_highlightedHandle` after a reshape so the follow-up
  press-and-slide (no mousemove between trackpad taps) still targets the point.
- **C never straightens a curve.** `lineToCurve` is one-way (MID→curve only);
  clicking a cyan CURVE control point ALWAYS drags it, whatever C's held/armed
  state. The old CURVE→straight toggle fired on stale C signals (held C past
  the 600 ms burst window, or the 3 s arm re-armed by auto-repeat until keyup)
  and destroyed curves during normal adjust clicks — see
  `docs/superpowers/specs/2026-07-16-c-curve-adjust-reset-fix-design.md`.
  Held-C double-click still "continues the gesture" structurally: press 1
  converts (control point 1 takes the former MID's array slot; spatially it
  sits at the 1/3 mark), and the kept `_highlightedHandle` + no hit-test on
  mousedown means press 2 targets that CURVE point and drags it. Unwanted
  curves are removed via undo, deleting an adjacent LINE vertex (its control
  points are swept up), or deleting the line — on a 2-vertex line after
  history is gone, delete-and-redraw is the only recovery. The unified
  gesture on BOTH devices: hover the MID, hold C (or press+release within
  the 3 s arm window), click or double-click, drag.
- **Closed rings must START with a LINE anchor.** `draw()`'s path builder
  (`moveTo(points[0])`), `updateShapes`' closing-MID insertion, and
  `curveGroupIndices` all assume `_points[0]` is a vertex. `getVertices()`
  returns anchors AND curve control points (it only filters MIDs), so any
  code that removes an anchor from a ring can strand control points at the
  head. Both `deleteVertex` and `mergeWith`'s self-close branch restore the
  invariant by rotating (`while points[0] !== LINE: shift→push`) — do the
  same in any new ring-editing code. Symptom if violated: the outline passes
  through cyan control points and a stray pale MID handle appears inside the
  curve group.
- **View rotation is display-only and lives in three places.** The canvas is
  SIZED to swapped dims (`updateCanvasScale`), CONTENT is turned by a context
  transform (`applyRotation` wraps labels AND the delete/lasso overlays), and
  POINTER input is un-rotated once in `getMousePos`. `fetchHandleId` must
  probe at `rotatePoint(mousePos)` because `getImageData` ignores context
  transforms. Viewport culling is bypassed while rotated. Never store or
  export rotated coordinates.
