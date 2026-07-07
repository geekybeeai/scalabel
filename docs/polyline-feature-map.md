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
  `isDrawing`, `onMouseDown/Move/Up`, `onKeyDown` (**D**=delete vertex, **Enter**=finish,
  **C**=curve), `addVertex`, `deleteVertex`, `shapes()` (**skips MID points**),
  `updateShapes` (rebuilds `_points` from state, **reconstructs MID midpoints**),
  `initTempLabel`, `mergeWith` (endpoint snap/merge), `isValid`. `_closed` = polygon vs
  polyline.
- `app/src/drawable/2d/path_point2d.ts` — `PathPoint2D`, `PathPointType` (LINE/MID/CURVE),
  point styles.
- `app/src/drawable/2d/label2d.ts` — base **`Label2D`**: `editing`, `temporary`, `type`,
  `labelId`, `item`, `label`, `shapes()`, `setManual`, `draw`, `updateState`.
- `app/src/drawable/2d/polygon2d_boundary_cloner.ts` — segment-clone modifier (advanced;
  rarely needed for lanes).

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
  `getMenuComponents` (zoom/width buttons), `getHistoryButtons` (undo/redo),
  `getRotationButtons`, `onWheel`/`zoom` (cursor-focal), pan, `changeLineWidth`,
  `rotateView`.
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
  kinds `created`/`edited`/`deleted`; `undo`/`redo`/`recordUserLine`/`recordEdit`/
  `recordDeletion`/`recordDeletedLine`/`canUndo`/`canRedo`/`handleKeyboard`/`reset`.
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
- `app/src/components/toolbar_category.tsx` — category rows, Show all / Show Tags.
- `app/src/styles/label.ts` — `categoryStyle`, `alerts` toast style.
- `app/src/components/alert.tsx` + `app/src/components/label_layout.tsx` — alert toasts
  (`getAlerts`).
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
  (a `<noop>` is a JS file exporting `module.exports = async () => {}`), which bypasses
  redis and jsdom's canvas.
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
