# Freeform (Lasso) Selection for Polylines — Design

- **Date:** 2026-07-10
- **Status:** Approved (design); ready for implementation planning
- **Area:** 2D annotator — polyline/polygon editing tools
- **Related map:** `docs/polyline-feature-map.md`

## 1. Objective

Add a **Freeform Select** (lasso) tool to the 2D annotator. The user drags a
freeform region; every polyline/polygon that lies inside **or** crosses the
region is highlighted in the existing magenta style, and a single **Delete**
press removes them all — reusing the multi-delete infrastructure the recent
Ctrl+click work introduced.

## 2. Background (why this is small)

Two existing mechanisms make this feature mostly wiring rather than new logic:

### 2.1 Pan is gated — plain left-drag does not always pan

In `viewer2d.tsx onMouseMove`, a drag pans only when `allowPan` is true:

- `Ctrl/Meta + drag` → pan anywhere, or
- inside the post-double-click pan window (`inPanWindow`, 400 ms), or
- `isArmed() && exceededThreshold` — and `armed` is set only by
  `pointer_pan_state.armEmptyDrag`, which `label2d_canvas.onMouseDown` calls
  **only when the press lands on empty canvas** (`labelIndex < 0`).

So left-drag pans **only when it starts over empty space** and moves > 5 px.
`Shift` is absent from the pan gate (it is used only for Ctrl+Shift+Z redo).
Therefore **`Shift + Left-drag` is free**, and an armed lasso simply intercepts
the gesture before the empty-space pan-arming runs — the viewer never pans.

### 2.2 Magenta highlight + batch delete already exist

The recent "Ctrl+click marks lines for deletion" work built:

- `common/multi_delete_state.ts` — a `marked` `Set<IdType>` with
  `toggleMarked` / `isMarked` / `getMarked` / `markedCount` / `clearMarked` /
  `onMarkedChange`.
- `drawable/2d/polygon2d.ts draw()` — any `isMarked(labelId)` line is stroked in
  `DELETE_HIGHLIGHT_COLOR` as animated marching-ants on the VIEW canvas.
- `drawable/2d/multi_delete.ts commitMarkedDelete()` — atomic batch delete with
  per-line undo snapshots.
- `components/toolbar.tsx deletePressed()` — calls `commitMarkedDelete()` first,
  then falls back to normal selected-label deletion.
- `label2d_canvas.tsx` — Escape and item-navigation already `clearMarked()`;
  `syncMarchingAnts` animates while `markedCount() > 0`.

**The lasso only has to compute which lines it hit and union them into the
`marked` set.** Highlight, Delete, undo, Escape-clear, and nav-clear come for
free.

## 3. User flow

**Activation (two paths, both required):**

1. **Toolbar button** — a new **Freeform Select** button beside the existing
   Cut and Delete-Segment buttons. Clicking arms a sticky mode.
2. **Shortcut** — `Shift + Left-mouse-down` starts a lasso immediately, without
   arming the tool (one-off; leaves tool mode unchanged).

**Drawing:** drag with the left button held; the in-progress path renders as a
dashed magenta outline that closes to its start point.

**Selection:** on mouse-up, every visible polyline/polygon that is inside or
crosses the region is **added** (union) to the `marked` set and turns magenta.

**Delete:** press **Delete** → existing `commitMarkedDelete()` removes all
marked lines atomically (undoable).

**Cancel:** **Escape** clears marks and disarms the tool; item navigation clears
both as well.

## 4. Decisions (locked)

| Decision | Choice | Rationale |
|---|---|---|
| Selection semantics | **Crossing** — enclosed **or** touching | Matches spec's "inside or intersecting"; single magenta color; most forgiving for delete |
| Combine with existing marks | **Add (union)** | Composes with Ctrl+click and repeated lassos; a lasso never un-marks |
| After lasso | **Mark only** (no auto-delete) | Delete key deletes, per spec |
| Toolbar mode | **Sticky** until Escape / toggle-off / item-nav | Lasso several regions in a row |
| `Shift+drag` | **One-off**, no mode change | Ad-hoc quick path |
| Target types | `POLYLINE_2D` / `POLYGON_2D` only | `commitMarkedDelete` already filters |
| Curves | Approximated by stored vertices in the hit-test (v1) | Exact bezier intersection is out of scope |

## 5. Architecture

### 5.1 New files

**`app/src/common/freeform_select_state.ts`** — transient, non-Redux tool state,
mirroring `cut_state.ts` / `segment_delete_state.ts`.

```ts
// module state
let armed = false            // sticky toolbar mode
let drawing = false          // mid-lasso (mouse down..up)
let path: Pt[] = []          // in-progress lasso, image-frame points

isFreeformArmed(): boolean            // toolbar mode on (button color)
isFreeformActive(): boolean           // armed || drawing (owns the gesture/cursor)
armFreeform(): void                   // setCutMode(false); resetSegmentDelete(); armed=true; notify
resetFreeform(): void                 // armed=false; drawing=false; path=[]; notify
beginFreeformPath(pt: Pt): void       // drawing=true; path=[pt]; notify
addFreeformPoint(pt: Pt): void        // append if far enough from last (merge-nearby); notify
getFreeformPath(): Pt[]
endFreeformPath(): Pt[]               // returns path; drawing=false; path=[]; notify
onFreeformChange(listener): () => void
```

Mutual exclusion (same style as segment-delete's `onCutModeChange` listener):
this module subscribes to `onCutModeChange` and `onSegmentDeleteChange` and
calls `resetFreeform()` when either of those tools becomes active. Because
`armFreeform()` disarms them *before* arming (and they are then inactive), those
listeners are no-ops at arm time and only fire when the user later arms cut or
delete-segment. No import cycle: freeform depends on cut/segment state, not the
reverse.

**`app/src/drawable/2d/freeform_select_geometry.ts`** — pure math, **no Session
or DOM imports** (testable via the node-env jest recipe, like
`polyline_cut_geometry.ts`).

```ts
export interface Pt { x: number; y: number }
export function pointInPolygon(pt: Pt, polygon: Pt[]): boolean   // ray casting
export function segmentsIntersect(a: Pt, b: Pt, c: Pt, d: Pt): boolean
export interface LassoLine { id: IdType; pts: Pt[]; closed: boolean }
export function lineHitsLasso(line: LassoLine, lasso: Pt[]): boolean
export function findLassoHits(lines: LassoLine[], lasso: Pt[]): IdType[]
```

`lineHitsLasso` (crossing test): true if **any** line vertex is inside the lasso
(`pointInPolygon`), **or** any line segment intersects any lasso edge (all
consecutive lasso pairs plus the closing edge `lasso[n-1]→lasso[0]`). For a
`closed` line, its own closing edge is included among the line segments. This one
predicate covers both "fully enclosed" (all vertices inside) and "crossing" (an
edge crosses the boundary).

**`app/src/drawable/2d/freeform_select.ts`** — impure orchestrator (like
`multi_delete.ts`).

```ts
export interface FreeformVisibility {
  hideLabels: boolean
  hiddenLabelTypes: string[]
  hiddenCategories: string[]
}
// Reads current item's labels, keeps visible POLYLINE_2D/POLYGON_2D, extracts
// image-frame vertices via getShapes(state, item, id), calls findLassoHits, then
// markLabels(hits). Returns the number of newly-considered hits.
export function runFreeformSelect(lasso: Pt[], vis: FreeformVisibility): number
```

Guard: returns 0 without touching state if `lasso.length < 3`.

**`app/test/drawable/freeform_select_geometry.test.ts`** — pure-logic unit tests
(node-env recipe from the feature map §8).

### 5.2 Reused with a tiny addition

**`app/src/common/multi_delete_state.ts`** — add an **add-only union** helper
(so a lasso never un-marks a line already in the set — unlike `toggleMarked`):

```ts
export function markLabels(ids: IdType[]): void {
  let changed = false
  for (const id of ids) {
    if (!marked.has(id)) { marked.add(id); changed = true }
  }
  if (changed) notify()
}
```

### 5.3 Reused unchanged

- Magenta `isMarked` marching-ants stroke in `polygon2d.ts draw()`.
- `commitMarkedDelete()` + the Delete key in `toolbar.tsx`.
- `syncMarchingAnts` (already animates while `markedCount() > 0`).
- Escape / item-nav `clearMarked()`.

### 5.4 Modified files

**`app/src/components/viewer2d.tsx`**
- `getFreeformSelectButton()` mirroring `getCutButton()`: `HighlightAlt` icon,
  green when `isFreeformArmed()`, toggles `armFreeform()` / `resetFreeform()`,
  with the same guards as the cut button (`!isDrawingInProgress()`, not a
  tracking task, and `showCurvesOnly !== true`).
- Add the button to the toolbar assembly beside the cut / delete-segment
  buttons.

**`app/src/components/label2d_canvas.tsx`**
- Subscribe to `onFreeformChange` in the component lifecycle (store an
  `_offFreeformChange`, unsubscribe on unmount) → `redraw()`.
- `onMouseDown`: **before** the Ctrl/Meta and empty-space branches, add
  ```ts
  if ((isFreeformActive() || e.shiftKey) && !e.ctrlKey && !e.metaKey) {
    beginFreeformPath(mousePos)   // NOT armEmptyDrag → viewer never pans
    return
  }
  ```
- `onMouseMove`: while `isFreeformActive() && drawing`, `addFreeformPoint(mousePos)`,
  `redraw()`, and return early (skip hover/edit).
- `onMouseUp`: while drawing, `const path = endFreeformPath()`; if
  `path.length >= 3`, call `runFreeformSelect(path, visibilityConfig)`;
  `redraw()`; return early (before the `isArmed()` block).
- Cursor: in the `onMouseMove` cursor-override branch, `if (isFreeformActive())
  setCursor("crosshair")`.
- `onKeyDown`: `if (key === Key.ESCAPE && isFreeformActive()) { resetFreeform() }`.
- `updateState` (item change): add `resetFreeform()` beside the existing
  `clearMarked()` / `resetSegmentDelete()`.
- Overlay: add `drawFreeformOverlay(context, ratio)` — strokes the in-progress
  path dashed in `DELETE_HIGHLIGHT_COLOR`, closing to the start point — invoked
  from the same overlay hook as the delete-segment overlay.
- **Cleanup:** remove the leftover `console.log("[DEBUG] …")` lines in
  `onMouseDown` (they sit in the function being edited).

**`app/src/components/toolbar.tsx`** (optional nicety)
- Add a "Shift+drag: freeform select" entry to the keyboard-shortcut legend.

## 6. Data flow

```
Arm (toolbar)         ─┐
Shift + Left-down      ─┴─► beginFreeformPath(pt); cursor = crosshair
   mouse-move (drag)    ──► addFreeformPoint(pt) [merge-nearby]; draw dashed overlay
   mouse-up             ──► path = endFreeformPath()
                            if path.length >= 3:
                              runFreeformSelect(path, vis)
                                → findLassoHits → markLabels(union)
                            hit lines turn magenta (existing highlight)
Delete key            ──► commitMarkedDelete()          (existing, atomic, undoable)
Escape                ──► resetFreeform() + clearMarked() (new + existing)
item navigation       ──► resetFreeform() + clearMarked() (new + existing)
```

## 7. Coordinate frame

Lasso points are captured in **image coordinates** (`getMousePos`), matching the
stored/exported polyline vertices, so the hit-test compares like with like (per
the map's golden rule). The overlay multiplies by the up-res `ratio` when
painting, exactly like the delete-segment overlay.

## 8. Edge cases & guards

- **Click, not drag:** `< 3` points / ~zero area → no-op; marks untouched.
- **Ctrl/Meta held:** falls through to existing pan / Ctrl+click-mark behavior;
  the lasso requires Shift or the armed mode and excludes Ctrl/Meta.
- **Visibility:** hidden lines (`hideLabels` / `hiddenLabelTypes` /
  `hiddenCategories`) are excluded from hit-testing, so you cannot mark what you
  cannot see.
- **Tracking tasks / in-progress draw / curves-only:** button disabled — same
  guards as the cut button.
- **Mutual exclusion:** arming freeform disarms cut + delete-segment, and arming
  either of those disarms freeform.
- **Double-click pan window:** a rare overlap (lasso started within 400 ms of a
  double-click) could let the viewer pan mid-lasso; acceptable for v1, noted.

## 9. Non-goals (YAGNI)

- No AutoCAD window-vs-crossing directional modes (crossing only).
- No auto-delete on release (mark, then Delete).
- No selection of boxes or other label types.
- No exact curve/bezier intersection (vertex approximation).
- No change to the annotator's real selection system.

## 10. Testing

- **Pure geometry** (`freeform_select_geometry.test.ts`, node-env recipe):
  `pointInPolygon` (inside / outside / on-edge), `segmentsIntersect`,
  `lineHitsLasso` (fully enclosed, crossing one edge, disjoint, single-point
  line, closed polygon), `findLassoHits` (mixed set → correct id subset).
- **Runtime** (the `verify` skill, headless Chrome over CDP): arm via toolbar and
  via Shift+drag; confirm hit lines go magenta, non-hit lines do not, Delete
  removes them, undo restores, Escape clears.
- **Static:** `npx tsc --noEmit` and `npm run lint` (filter pre-existing CRLF
  `prettier/prettier` noise on this Windows checkout, per the map).

## 11. File change summary

| File | Change |
|---|---|
| `app/src/common/freeform_select_state.ts` | **new** — tool state + path buffer + mutual exclusion |
| `app/src/drawable/2d/freeform_select_geometry.ts` | **new** — pure hit-test math |
| `app/src/drawable/2d/freeform_select.ts` | **new** — orchestrator (state → geometry → `markLabels`) |
| `app/test/drawable/freeform_select_geometry.test.ts` | **new** — pure-logic tests |
| `app/src/common/multi_delete_state.ts` | add `markLabels(ids)` union helper |
| `app/src/components/viewer2d.tsx` | add `getFreeformSelectButton` + toolbar entry |
| `app/src/components/label2d_canvas.tsx` | gesture wiring, overlay, cursor, Escape, nav-clear, DEBUG cleanup |
| `app/src/components/toolbar.tsx` | (optional) shortcut-legend entry |
