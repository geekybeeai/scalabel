# Polyline / Polygon Feature Map (2D annotator)

A "read these first" guide for any agent working on 2D **polyline/polygon** features
in this fork (the embedded lane-annotation tool). Grouped by *what you're touching* →
*which files to open*. Anchors are **symbol names** (functions/classes), not line
numbers, since lines drift. Open the file, search the symbol.

Last checked against the local source on **2026-11-13** (baseline commit
`8fee5000`, plus the viewport-rendering changes in this working tree).
Paths below are relative to the `scalabel/` repository root.

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
  `initTempLabel`, `mergeWith` (endpoint snap/merge), `isValid`, `bounds`.
  `_closed` controls ring geometry independently of label type: both a
  `POLYGON_2D` and a `POLYLINE_2D` with `label.closed === true` render closed.
  Self-joining a polyline preserves its type and sets `closed: true`.
  Validity requires FINISHED and non-degenerate geometry; closed rings may
  self-intersect (lane edges that double back are allowed).
  `getPath` caches original-frame `Path2D` geometry for rendering, body picking,
  and grab previews; `invalidateGeometry` clears path/group/bounds caches only
  after geometry changes. `updateShapes` reuses unchanged shape references.
- **Endpoint join / connect** (drag an open line's START or END vertex onto
  another endpoint within 15 screen px): `Label2DList.findNearestEndpoint`
  returns an `EndpointSnapCandidate {polyline, isStart, targetPointIndex,
  mergeable}`;
  `Polygon2D.onMouseMove` (RESHAPE) snaps the dragged coord onto the target
  point and shows `drawSnapIndicator`; `onMouseUp` calls `mergeWith` **only
  if `mergeable`** (same `category[0]`, or self-close start↔end → closed polyline).
  Different-category endpoints still SNAP (bit-identical coords, no gap) but
  the two lines stay distinct labels with their own categories — it commits
  as a plain reshape edit (`recordEdit`, one-step undo). Indicator: solid
  green halo + white centre = merge; dashed white halo + dark centre =
  connect-only. Merge deletes the target via `_mergedOut` → `isValid()=false`
  → `deleteInvalidLabel` (history: `edited(A)` + `deleted(B)`, i.e. two undo
  steps). An open endpoint may also snap to any handle of a closed shape
  (LINE, MID, or CURVE) as connect-only; the target stays closed and distinct.
  `app/src/common/snap_state.ts` (`isSnapEnabled`/`setSnapEnabled`) supplies the
  sticky, session-local toggle, initially ON; `viewer2d.tsx getSnapButton`
  controls it. Turning it off suppresses snapping, merging, and the indicator,
  including a snap acquired earlier in the same drag.
- `app/src/drawable/2d/endpoint_snap_geometry.ts` — `snapTargetIndices` and
  `nearestSnapPointIndex`: pure handle-picking helpers and regression coverage.
  The interactive snap scan in `Label2DList` directly reads `snapPoints`
  (a read-only view) to avoid cloning entire candidate polylines.
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
- `app/src/drawable/2d/polyline_curve_cut.ts` +
  `app/src/drawable/2d/polyline_curve_cut_geometry.ts` — **divide curve**, not
  split-label cut: `performCurveCut`, `findCurveCutSite`, `buildDividedCurve`.
  Exact De Casteljau subdivision adds an anchor between two Bézier groups;
  a straight-span click inserts a vertex. Open and closed shapes keep one label
  and the same outline; history is one `edited` command.
- `app/src/drawable/2d/polyline_straighten.ts` +
  `app/src/drawable/2d/polyline_straighten_geometry.ts` — `performStraighten`,
  `findStraightenSite`, `buildStraightened`: remove the control-point pair from
  the single curve nearest the click/cursor, preserving the other curves and
  the label id. Works on open and closed shapes; one `recordEdit` undo step.
- `app/src/drawable/2d/polyline_simplify.ts` +
  `app/src/drawable/2d/polyline_simplify_geometry.ts` — `performSimplify`,
  `simplifyPolyline`, `countRemovable`: remove redundant straight-span anchors
  while protecting curve groups; uses `recordEdit`.
- `app/src/drawable/2d/polyline_disjoint.ts` +
  `app/src/drawable/2d/polyline_disjoint_geometry.ts` — `performDisjoint`,
  `disjointableAnchors`, `findDisjointAnchor`, `buildDisjointHalves`: split an
  open line at an interior LINE anchor, or a self-closed polyline at an eligible
  ring anchor. Produces two open lines without moving coordinates; first keeps
  the original id, second gets a new id, recorded as one `cut` command.
  True `POLYGON_2D` labels are excluded.
- `app/src/drawable/2d/polyline_arc.ts` +
  `app/src/drawable/2d/polyline_arc_geometry.ts` — `performArc`,
  `curveThroughPoints`: click points then Enter; two picks produce a straight
  span, three define a circular arc, four or more a smooth spline. Stored as
  an ordinary open `POLYLINE_2D` with LINE/CURVE points, so normal rendering,
  export, and endpoint joining apply. Creation uses one `stamped` command.
- `app/src/drawable/2d/polyline_grab_move.ts` — `commitGrab`:
  W carries a line, E carries a copy, click drops, Escape cancels. State is in
  `app/src/common/grab_move_state.ts`; `drawGrabPreview` paints the transient
  offset. MOVE records `edited`; COPY records `created`.
  The canvas now picks W/E targets with `Label2DList.pickPolyline` in body-only
  mode. The older exported `findGrabbableLabel` proximity helper remains in the
  module but is not used by the canvas's W/E shortcut.

## 2. Drawable list + interaction controller
Open when: selection, the drawing lifecycle, mouse/keyboard routing, copy/paste.

- `app/src/drawable/2d/label2d_list.ts` — **`Label2DList`**: `_labels`/`_labelList`/
  `_selectedLabels`, `redraw()`, `updateState()`, `isDrawingInProgress()`,
  `cancelDrawing()`, `onDrawableUpdate()` (rAF-batched repaint via `subscribe`),
  `makeDrawableLabel2D` (factory defaults: `POLYGON_2D`→closed,
  `POLYLINE_2D`→open; `Polygon2D.updateState` also reads `label.closed`).
  `app/src/common/keyboard_state.ts` holds rebuild-safe held/armed keys;
  `app/src/common/pointer_pan_state.ts` coordinates deferred clicks, the
  5 px pan threshold, and the 400 ms double-click pan window.
- `app/src/drawable/2d/label2d_handler.ts` — **`Label2DHandler`** (view-controller):
  `onMouseDown` (starts a temp `Polygon2D` on empty space), `onKeyDown`,
  `pasteLabel` (copy/paste; records to draw history), `_highlightedLabel`,
  `updateState` (clears history on item change), commits via `commit2DLabels`.

## 3. Canvas rendering + pointer input + view transform
Open when: how lines paint, hit-testing, zoom/pan/rotation, screen↔image mapping.

- `app/src/components/label2d_canvas.tsx` — **label + control canvases**. `redraw()`
  (→ `label2dList.redraw`), `updateViewport`, `getMousePos` (client→viewport→image
  via `viewportToImage`), `fetchHandleId` (native polyline geometry picking;
  color-canvas fallback for other drawable types),
  `onMouseDown/Move/Up`, `onKeyDown` (→ handler + stamp-aware history routing),
  `displayToImageRatio` (CSS pixels per image pixel), `_upResRatio` (fixed logical
  resolution 2), `_pixelRatio` (device backing pixels per CSS pixel).
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
- `app/src/view_config/viewport.ts` — annotation-specific display projection:
  `makeViewportTransform`, `imageToViewport`, `viewportToImage`,
  `viewportImageBounds`, `applyViewportTransform`, `ANNOTATION_RESOLUTION`.
  Fitted image size, centering, pan, and rotation match the image layer while
  annotation buffers remain viewport-sized.

### How a polyline reaches the canvas

1. `Label2DHandler.onMouseDown` creates a temporary `Polygon2D` when drawing
   begins on empty space (the canvas may defer the click until mouse-up to
   distinguish drawing from panning). `initTempLabel` enters `DRAW` and chooses
   the selected category color. Clicks add LINE anchors with intervening MID
   handles; pointer movement updates `_mouseCoord` for the pending segment.
2. `Label2DList.onDrawableUpdate` coalesces repaint requests into one animation
   frame. `Label2dCanvas.redraw` clears both annotation canvases, applies the
   same context rotation to each, and calls `Label2DList.redraw`.
3. The list applies type/category visibility, curves-only filtering, and
   optional viewport culling; labels paint in ascending `label.order`.
   Polylines use a VIEW pass and geometry-based picking. Other drawable types
   retain VIEW/CONTROL passes (CONTROL may be skipped during a view gesture).
4. `Polygon2D.draw` starts at the first LINE anchor. LINE points produce
   `lineTo`; a CURVE pair plus the next anchor produces `bezierCurveTo`.
   MID handles do not enter the stroked path. During `DRAW`, an extra segment
   reaches the live cursor. `_closed` adds the closing edge.
5. Finishing with Enter or clicking the first handle ends the transient draw.
   `commit2DLabels` adds a valid new label to Redux and records its creation.
   `updateShapes` later rebuilds its drawable points, including derived MIDs.

### Layers, appearance, and hit testing

- `ImageCanvas` paints the bitmap; `Label2dCanvas` owns the visible label canvas
  and hidden control canvas. `app/src/styles/label.ts` (`label2dViewStyle`)
  positions the annotation canvases, enables pointer input on the visible
  canvas, and hides the control canvas. `Viewer2D.getOverlayComponents` mounts
  annotations outside the panned image wrapper, alongside the stamp panel.
- VIEW strokes use `getColorByCategory` in `app/src/drawable/util.ts`: a
  category-name map takes precedence over the category-index palette. Both
  temporary and committed labels use it. Closed shapes are **outline-only**
  on VIEW. Interactive polyline picking follows the stroke, including closed
  polylines; it does not treat an entire ring interior as a line-body hit.
- Visible style defaults in `polygon2d.ts`: edge width 6, point radius 8,
  highlighted radius 12, in **logical drawing units (2 per CSS pixel)**.
  Thus the default line is 3 CSS px, handles are 4 CSS px in radius, and the
  highlighted handle is 6 CSS px, independent of zoom and device resolution.
  The line-width multiplier still controls the stroke.
  `changeLineWidth` clamps the multiplier to 0.5–4 and defaults/resets to 1.
- Handles appear on VIEW for selected/highlighted labels. CURVE handles are
  cyan, MID handles use a lightened category color, LINE anchors use the
  category color, and visible handles have black outlines.
  `PathPoint2D.draw` scales point positions by `ratio`, but uses the supplied
  radius directly. Dashed Bézier control-polygon edges are CONTROL-only.
- `Label2DList.pickPolyline` first rejects distant cached bounds, then checks
  visible point handles (6/7 CSS px hit radii), then the actual cached stroke
  via `Polygon2D.containsStroke`. Bézier control-polygon edges and empty space
  outside the stroke are not body hits. Handles take priority over bodies;
  bodies follow paint order. Active MOVE/RESHAPE retains its drag target.
- Other drawable types retain encoded CONTROL colors. The fallback reads one
  backing pixel at the projected viewport coordinate and never accepts an old
  widened polyline control stroke. The legacy polygon CONTROL renderer remains
  available to direct callers, but is omitted by viewport rendering.
- Curves-only mode uses `curveGroupIndices` to paint only Bézier groups in
  rendering and geometry picking. The label being actively edited remains fully rendered.
  Category hiding exempts actively edited labels; hide-all/type filtering
  instead exempts selected labels. These visibility rules are distinct.

### Scale, redraw, and viewport rules

- `updateCanvasScale` computes the image canvas's fitted CSS size times
  `viewScale`, plus centering padding. Pan moves the image content using CSS margins
  (`displayLeft`/`displayTop` in `Viewer2D.getDrawableComponents`).
  Annotation positions paint at `ratio = displayToImageRatio * 2` logical
  units per image pixel; `applyViewportTransform` maps logical units through
  `_pixelRatio / 2` and applies pan/rotation. Pointer input reverses the same
  projection. Widths/radii do not inherit image magnification.
- Current zoom limits are 1–60. `getUpResRatio` requests 2× backing resolution
  through zoom 3, then 1×. Backing dimensions are capped at 8192; the returned
  effective ratio reflects the actual allocation and **can fall below 1**
  when capped, without changing stored coordinates or CSS placement.
  This adaptive/capped backing applies to the image layer. Annotation buffers
  cover only the viewport at device resolution (subject to an 8192-per-axis
  safety cap); zoom does not change their allocation.
- `interaction_state.ts` marks zoom/pan gestures and signals idle after 150 ms.
  Only the image canvas opts into 0.7× motion resolution. The annotation
  canvas skips the optional non-polyline CONTROL pass during interaction and
  repaints after idle. Stable canvas refs and dimension checks prevent repeated
  allocation. `ResizeObserver` schedules viewport-size updates; `updateViewport`
  refreshes image dimensions and projection on navigation and view changes.
  `Label2DList.updateState` skips drawable rebuilds for view-only state changes.
- Views above zoom 2 cull labels using `bounds()` plus a margin.
  `Polygon2D.bounds` includes `_mouseCoord` **only while DRAW**, so a live
  preview crossing the viewport remains visible even with off-screen anchors.
  MOVE/RESHAPE do not bypass culling merely because `editing` is true.
  Rotated views use the inverse-projected viewport bounds too. Off-screen
  handles are skipped before painting.
- Tool overlays paint after the label list on VIEW: segment deletion,
  freeform/rectangle selection, grab, stamp, arc, and disjoint hints. They use
  the same image-to-backing ratio and rotation as the labels.

## 4. Commit to state · undo/redo · actions/reducers
Open when: how a finished/edited/deleted line reaches redux; history behavior.

- `app/src/drawable/states.ts` — **`commit2DLabels`** = the normal funnel from drawables →
  redux. New draw → `addNewLabel`; edit → `updateLabel` + `drawHistory.recordEdit`;
  invalid → `deleteInvalidLabel` (+ record). Helpers `polylineShapesChanged`, `lineSnapshot`.
  Ordinary click-to-draw creation/reshape/move goes through this function.
  Dedicated tools (cut, divide, straighten, simplify, disjoint, arc, grab,
  stamp) also dispatch their own label actions and record history explicitly.
- `app/src/common/draw_history.ts` — **`DrawHistory`** (polyline-level undo/redo). Command
  kinds `created`/`edited`/`deleted`/`cut`/`stamped`; `undo`/`redo`/`recordUserLine`/`recordEdit`/
  `recordDeletion`/`recordDeletedLine`/`recordCut`/`recordStamp`/`canUndo`/`canRedo`/
  `handleKeyboard`/`reset`. `recordCut` records the split as one atomic command
  (single undo/redo step); `recordStamp` does the same for every mark created by
  one stamp run.
  Only tracks **user-touched** lines (never untouched predictions).
- `app/src/common/stamp_history.ts` — `getStampHistoryTarget` and
  `runStampHistoryAction`: pending manual placements use their position stack
  only before Apply; applied runs use draw history. A successful document
  undo/redo closes an applied stamp preview to prevent ghost marks.
  `stamp_state.ts` exposes `handleStampHistoryKeyboard`, `undoStampOrHistory`,
  and `redoStampOrHistory`; the canvas and toolbar use these wrappers.
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
- `app/src/types/state.ts` — `LabelType` (id/type/shapes/order/manual/category/item/closed),
  `ImageViewerConfigType` (viewScale/displayLeft/displayTop/lineWidthMultiplier/rotation),
  `PathPoint2DType`, `IdType`, `ShapeType`.
- `app/src/types/export.ts` — export JSON shapes (`poly2d`, `manualShape`).
- `app/src/const/common.ts` — `LabelTypeName.POLYGON_2D`/`POLYLINE_2D`, `Key.*`, `Cursor`.

## 6. Export / import (backend round-trip)
- `app/src/server/export.ts` — `DatasetExport`, poly2d serialization (what `/getExport` emits).
- `app/src/server/import.ts` — parse poly2d, `manualShape` → `manual`.
  `convertPolygonToExport` in `export.ts` includes both polygon type and
  `label.closed` when computing `poly2d.closed`. Import retains a closed ring
  as `POLYLINE_2D` + `closed: true` when polygons are not a project label type;
  when polygon labels are supported, a closed import becomes `POLYGON_2D`.

## 7. Toolbar / sidebar / alerts / image load
- `app/src/components/toolbar.tsx` — category list, delete, keyboard-shortcut legend;
  `deletePressed` first calls `commitMarkedDelete`; if no batch is marked,
  normal selection deletion uses `drawHistory.recordDeletion` +
  `deleteSelectedLabels`.
- `app/src/common/cut_state.ts` — cut-tool armed flag (+ change listeners);
  `app/src/components/cut_icon.tsx` — scissors icon + CSS cursor. Toolbar
  button in `viewer2d.tsx getCutButton`; click/cursor/Escape/context-menu
  wiring in `label2d_canvas.tsx`.
- `app/src/common/segment_delete_state.ts` — delete-segment phase machine
  (awaitFirst/awaitSecond/preview, mutually exclusive with cut mode);
  toolbar button in `viewer2d.tsx getDeleteSegmentButton`; picks/overlay/
  timer wiring in `label2d_canvas.tsx` (marching-ants preview, 3 s commit).
- `app/src/common/multi_delete_state.ts` +
  `app/src/drawable/2d/multi_delete.ts` — Ctrl/Cmd+click toggles a polyline or
  polygon into the transient marked set; Delete removes marked lines.
  `commitMarkedDelete` dispatches deletion together but records a `deleted`
  command **per line**, so undo restores them one at a time.
  `app/src/drawable/2d/marching_ants.ts` supplies the shared animation offset
  for marked lines and the delete-segment preview. Escape clears marks.
- `app/src/common/freeform_select_state.ts` +
  `app/src/drawable/2d/freeform_select.ts` +
  `app/src/drawable/2d/freeform_select_geometry.ts` — freeform/rectangle selection
  feeds that same marked set via `runFreeformSelect`/`findLassoHits`.
  `getFreeformSelectButton` has a sub-mode menu: lasso drag or two-click
  rectangle; Shift+drag always starts an ad-hoc lasso. Only fully enclosed
  lines are marked. Disabled while drawing, for tracking, or in curves-only
  display; overlays use `drawFreeformOverlay`.
- Other toolbar/state entry points in `viewer2d.tsx`:
  `getCurveCutButton` → `app/src/common/curve_cut_state.ts`,
  `getStraightenButton` → `app/src/common/straighten_state.ts`,
  `getSimplifyButton` → `app/src/common/simplify_state.ts`,
  `getDisjointButton` → `app/src/common/disjoint_state.ts`, and
  `getArcButton` → `app/src/common/arc_state.ts`.
  Executors/geometry are listed in section 1; click precedence is centralized
  in `Label2dCanvas.handleArmedTool` (cut and divide also have earlier branches).
- Bare shortcuts in `Label2dCanvas.onKeyDown`: A straightens the curve nearest
  the cursor; W/E carry a line/copy; 1–9 choose category slots 1–9 and 0 chooses
  slot 10, applying to current selection and the next drawing category.
  `canRunShortcut` blocks these while typing in INPUT/TEXTAREA, drawing, or
  previewing segment deletion. Arc clicks accumulate until Enter; Escape
  first clears pending arc picks, then disarms on a subsequent press.
- `app/src/components/viewer2d.tsx` — `getStampButton` ("Repeat marks along a
  line"), and
  `getOverlayComponents` (right-pinned settings panel). The stamp button is green
  while it is armed or its panel is open; it is unavailable while drawing or on
  tracking tasks.
- Capture is initiated from the template picker, so there is no separate capture
  button in the toolbar.
- `app/src/components/stamp_settings.tsx` — **Stamp marks** panel: template
  picker (Dash/Chevron/saved custom plus `+ Save a mark as a shape`), mark class,
  angle, dash length, chevron arm/apex controls, custom size, perpendicular
  offset, even-spacing controls with editable numeric values, Reset, and
  Apply/Update/Done/Cancel. The panel stops pointer events from reaching the
  canvas underneath it.
- `app/src/common/stamp_state.ts` — transient ARMED/PREVIEW state, persisted
  stamp options, captured templates in `localStorage`, and a separate placement
  undo/redo stack for manual mode. Settings survive from one stamp to the next;
  placed marks do not reach redux until Apply/Update.
- `app/src/common/stamp_interaction.ts` — `getStampCanvasClickAction` resolves
  guide pick vs manual placement vs ignoring even-spacing preview clicks.
  `app/src/common/stamp_settings_utils.ts` — `parseStampNumber`/
  `clampStampNumber` support editable numeric settings. Stamp history routing
  is described in section 4.
- `app/src/components/label2d_canvas.tsx` — `handleStamp` picks the guide,
  `handleCapture` saves a reusable shape, `drawStampPreview` paints the live
  preview, `addManualMark` records click positions along the guide, and
  `commitStampPreview` writes the run. Image navigation disarms the tool and
  ends an active preview.
- "Curves only" sidebar checkbox — `showCurvesOnly` viewer-config flag
  (`toolbar.tsx` toggle → `label2d_canvas.tsx redraw` →
  `label2d_list.ts redraw` filter → `polygon2d.ts draw` curves-only branch,
  rendering plus geometric picking). Pure group finder: `app/src/drawable/2d/curve_groups.ts`
  (`curveGroupIndices`). Cut/delete-segment arming is guarded while on.
- `app/src/components/toolbar_category.tsx` — category rows, Show all / Show Tags.
- "Show image" checkbox + "Image opacity" slider — `hideImage` / `imageOpacity`
  viewer-config flags (`toolbar.tsx` → `image_canvas.tsx`: `redraw` blanks the
  layer on `hideImage`; CSS opacity on the image canvas dims it. Labels are on
  separate canvases, unaffected). Slider disabled while image hidden. Space
  also toggles `hideImage` (`label2d_canvas.tsx onKeyDown`, skipped while
  typing in inputs) — this REPLACES upstream's Space = toggle-checked-label
  binding. Both toggle rows render below the category list.
- `app/src/styles/label.ts` — `categoryStyle`, `label2dViewStyle`, `alerts` toast style.
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
- `app/test/drawable/polygon_viewport_bounds.test.ts` — node-environment
  coverage of live-cursor bounds, off-screen-anchor previews, multi-vertex
  polygon closing previews, and retained culling for committed labels.
- `app/test/server/closed_polyline_roundtrip.test.ts` — closed polyline export
  and import behavior for polyline-only and polygon-enabled projects.
- `app/test/drawable/endpoint_snap_geometry.test.ts`,
  `app/test/drawable/curve_groups.test.ts`,
  `app/test/drawable/polyline_disjoint_geometry.test.ts`, and
  `app/test/drawable/polyline_disjoint.test.ts` — handle picking, Bézier groups,
  open/closed disjoint geometry, and history behavior.
- Cut/delete coverage: `app/test/drawable/polyline_cut_geometry.test.ts`,
  `app/test/drawable/polyline_cut.test.ts`,
  `app/test/drawable/polyline_segment_delete.test.ts`,
  `app/test/drawable/draw_history_cut.test.ts`, and
  `app/test/drawable/multi_delete.test.ts`.
- Selection/state coverage: `app/test/drawable/freeform_select_geometry.test.ts`,
  `app/test/drawable/freeform_select.test.ts`,
  `app/test/common/freeform_select_state.test.ts`,
  `app/test/common/multi_delete_state.test.ts`,
  `app/test/common/segment_delete_state.test.ts`,
  `app/test/common/pointer_pan_state.test.ts`, and
  `app/test/common/interaction_state.test.ts`.
- Stamp coverage: `app/test/drawable/polyline_stamp_history.test.ts`,
  `app/test/common/stamp_interaction.test.ts`,
  `app/test/common/stamp_history.test.ts`, and
  `app/test/common/stamp_settings_utils.test.ts`.

**Running tests in this environment (important):**
- Default Jest setup starts `redis-server`; DOM/canvas-based suites may also
  need the native `canvas` module. Check the actual failure before treating
  a missing dependency as an application regression. Several focused suites,
  including viewport-bounds coverage, use Node and avoid a real canvas.
- For pure-logic and compatible node-environment suites, run from the repo root:
  `npx jest <file> --runInBand --env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js --coverage=false`
  This bypasses redis and jsdom's canvas. `app/test/setup/noop.js` supplies
  globalSetup/globalTeardown for this recipe — it exports a **synchronous** noop
  (`module.exports = () => {}`), not an `async` one: babel-jest transpiles async
  arrows in `.js` files to regenerator-runtime code that fails inside jest's setup
  context, so keep it sync.
- `npx tsc --noEmit` and `npm run lint` are additional checks when dependencies
  are installed; success must be established from current output. Windows
  checkouts can have existing CRLF `prettier/prettier` noise — compare a changed
  file's rule violations to HEAD before attributing them to a change.

## Gotchas that will save the next agent hours
- **Coords are original-frame, always.** View transforms (zoom/pan/rotation) never change
  stored/exported coordinates.
- **Mid-draw is not in redux.** A line only becomes an `ADD_LABELS` action at Enter/close
  via `commit2DLabels`; before that it's a transient `Polygon2D` in
  `Session.label2dList.selectedLabels[0]`.
- **`shapes()` drops MID points**; midpoints are reconstructed in `updateShapes`. Compare
  geometry by non-MID vertices.
- **Image zoom and annotation rendering use different buffers.** The image
  keeps its scaled CSS box; annotations stay viewport-sized. Use the shared
  viewport projection for annotation input/output, not image-canvas backing
  ratios. Never add pan offsets to stored points. Snap distance is
  `15 / displayToImageRatio` image pixels, using the actual CSS display scale.
- **Bounds must include the live preview.** During DRAW, `_mouseCoord` belongs
  in `Polygon2D.bounds`; otherwise high-zoom culling can hide the segment when
  its committed anchors are off-screen. Do not broadly exempt all `editing`
  labels from culling. See `polygon_viewport_bounds.test.ts`.
- **`getBoundingClientRect` powers cursor-focal zoom/pan** → keep the canvas DOM
  axis-aligned (that's why rotation is done via context transform + dim-swap, not CSS
  rotate).
- **The dormant `redux-undo`** (in `configure_store.ts`) is wired but never triggered;
  the real undo/redo is `draw_history.ts`. Don't confuse them.
- **Separate image loading from annotation repaint problems.** For expired URLs
  or download failures, inspect `session_setup.tsx image.onerror`; for labels
  misplaced after navigation, inspect `Label2dCanvas.updateViewport`.
- **Per-instance key maps are unsafe across selection updates.** Drawable
  rebuilds can create fresh `_keyDownMap`s, and selection changes may clear
  handler key state. Read held keys from `common/keyboard_state.ts` (module-level, fed by
  Label2dCanvas's document listeners, rebuild-proof) — see the C/D checks in
  `polygon2d.ts onMouseDown`. Do NOT "fix" this by moving the action to bare keydown:
  C's gesture is convert-on-click **then drag to shape**; keydown-only conversion was
  tried and rejected (hair-trigger, no drag). And keep exactly ONE trigger per action —
  a keydown path plus a click path double-fires and undoes/corrupts the edit.
- **Some trackpads suppress clicks while typing.** Laptop palm rejection can
  suppress taps while a key is held, so the click may arrive after keyup.
  Hold-key+click gestures therefore also accept a
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
  press-and-slide can continue a point gesture without an intervening mousemove.
- **C never straightens a curve.** `lineToCurve` is one-way (MID→curve only);
  clicking a cyan CURVE control point drags it regardless of C's held/armed
  state. Current behavior is in `Polygon2D.onMouseDown` and `lineToCurve`.
  Conversion replaces the MID slot with the first control point (at the 1/3
  mark) and inserts the second at the 2/3 mark. Mouse-up retains the highlighted
  handle, and `shouldDeferPointerDown` protects point gestures from the pan
  window. The handler also resolves the current hit on mouse-down, so do not
  assume mousedown skips hit testing or always retains a previous target.
  Unwanted curves are removed via undo, deleting an adjacent LINE vertex (its
  control points are swept up), deleting the line, or the dedicated
  **straighten tool / A shortcut** (`performStraighten`). The unified
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
- **View rotation is display-only.** The image uses swapped CSS dimensions
  (`updateCanvasScale`); annotations and previews use `applyViewportTransform`.
  `getMousePos` reverses the viewport projection once. Native line picking is
  in original-image coordinates; other drawable color probes use
  `imageToViewport` and `_pixelRatio`. Never store or export rotated coordinates.

---

## 9. Repetitive stamp marks

Open when: adding repeated dashes, chevrons, or a saved custom marking along an
existing polyline/polygon.

### UI and controls

- The toolbar button titled **Repeat marks along a line** opens the **Stamp marks**
  panel at the top-right of the canvas and arms a one-shot guide-line pick. The
  next click on a visible line chooses the guide; the guide itself is never
  modified.
- The panel's template picker supports **Dash**, **Chevron**, captured custom
  shapes, and `+ Save a mark as a shape`. It also exposes the output **Class**,
  relative **Angle**, **Offset**,
  and shape-specific sizing:
  - Dash: **Length**.
  - Chevron: **Arm A**, **Arm B**, and **Apex**.
  - Custom: **Size** plus the saved template's **Rename** and **Forget** actions.
- **Even spacing** is enabled by default. It shows **Spacing** and **Start**;
  the internal end margin keeps marks away from both guide endpoints. Turning it
  off switches to manual placement and shows the count of placed marks plus the
  Clear button.
- The template picker's **+ Save a mark as a shape** item captures the visible
  polyline/polygon nearest the cursor, names it `Mark N`, stores it in browser
  `localStorage`, and selects it as the current custom template. Captured shapes
  remain available after reload; the panel can rename or forget them.

### How the tool works

1. Guide picking searches visible `POLYLINE_2D`/`POLYGON_2D` labels within
   `CUT_CLICK_RADIUS_PX` (20 display pixels, converted to image pixels for the
   current zoom). Hidden label types and categories are excluded.
2. The chosen guide is flattened for curved spans and walked by **arc length**.
   Each sample gets the local direction of travel. The mark is rotated by the
   configured angle relative to that direction and shifted by the configured
   perpendicular offset, so marks follow bends instead of using one global
   orientation.
3. Preview geometry is transient. Slider changes repaint the preview without
   writing labels, and canvas clicks do not commit an even-spacing preview.
   **Apply** creates one separate manual `POLYLINE_2D` label per
   mark, using the panel class or the sidebar's selected class when no panel
   class is set; the guide remains untouched.
4. After the first Apply, the button becomes **Update**. Applying again removes
   the previous run and writes the adjusted run, rather than stacking duplicates.
   **Done** closes the preview while keeping the last applied run. **Cancel**
   cancels an unapplied preview; the panel's X also closes the active preview.
5. One stamp run is recorded as one `stamped` draw-history command, so undo/redo
   removes or restores the whole batch in one step. In manual placement mode,
   Ctrl/Cmd+Z and Ctrl/Cmd+Y operate on pending placement positions before the
   first Apply. Applied runs use document history; a successful undo/redo closes
   their transient preview. Toolbar undo/redo uses the same routing.

### How to use it

1. Draw or select an existing line that can serve as the guide.
2. Click **Repeat marks along a line**, tune the panel settings, then click the
   guide. The panel stays open while the green preview is adjusted.
3. Keep **Even spacing** on for a regular run; changing the panel only updates
   the preview, and canvas clicks leave it pending. Or turn even spacing off and
   click along the guide to add individual marks. Clicking near an existing
   pending mark removes it; use Ctrl/Cmd+Z/Y for placement undo/redo.
4. Choose **Apply**. Use **Update** after changing settings, then **Done** when
   the run is correct. To stamp another guide, toggle the toolbar button off and
   on again if the panel is still open, then select the next guide.
5. For an irregular marking, open the template picker, choose **+ Save a mark as
   a shape**, click an existing mark, then choose the saved shape from the same
   stamp panel and stamp it along another guide. Enter numeric values directly
   beside any slider when a precise size, angle, spacing, or offset is needed.

Implementation anchors: `app/src/components/stamp_settings.tsx`,
`app/src/common/stamp_state.ts`, `app/src/components/viewer2d.tsx`
(`getStampButton`), `app/src/components/label2d_canvas.tsx`
(`handleStamp`/`handleCapture`/`drawStampPreview`/`commitStampPreview`),
`app/src/drawable/2d/polyline_stamp.ts` (`findGuideLine`/`previewMarks`/
`commitStamp`/`captureMarkAt`), and
`app/src/drawable/2d/polyline_stamp_geometry.ts`
(`stampAlongPath`/`samplePath`/`projectToPath`/`captureTemplate`).
