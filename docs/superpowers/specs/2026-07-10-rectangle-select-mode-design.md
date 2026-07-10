# Rectangle Selection Mode for the Select Tool — Design

- **Date:** 2026-07-10
- **Status:** Approved (design); ready for implementation planning
- **Area:** 2D annotator — polyline/polygon selection tools
- **Related:** `docs/superpowers/specs/2026-07-10-freeform-select-polylines-design.md`

## 1. Objective

Add a **Rectangle** selection mode alongside the existing **Freeform** (lasso)
select tool. The toolbar's select button becomes a **split button**: the icon
arms/disarms the currently-selected mode, and a caret opens a dropdown to switch
between Freeform and Rectangle. In Rectangle mode the user draws an
axis-aligned selection rectangle with **two clicks** (click one corner, move to
size, click the opposite corner); every polyline/polygon inside or crossing the
rectangle is marked magenta for the existing batch-delete flow.

## 2. Background (why this is small)

The freeform lasso feature already built everything the rectangle needs
downstream of "which lines did we hit":

- `drawable/2d/freeform_select_geometry.ts` — `findLassoHits(lines, polygon)`
  takes **any** polygon (a rectangle is just a 4-point polygon) and returns the
  crossing hits.
- `drawable/2d/freeform_select.ts` — `runFreeformSelect(polygon, visibility)`
  reads visible polylines/polygons → `findLassoHits` → `markLabels` (union).
- `common/multi_delete_state.ts` — magenta marching-ants highlight, `Delete`
  batch delete, undo, Escape-clear, nav-clear all key off the `marked` set.
- `common/freeform_select_state.ts` — sticky-arm tool state, mutual exclusion
  with cut/delete-segment, the dashed magenta overlay hook.

**The rectangle mode only adds:** a mode flag, a two-click state machine, a
4-corner builder, an overlay branch, and the split-button UI. No new selection,
highlight, delete, or geometry math.

## 3. User flow

1. Click the caret on the select button → dropdown shows **Freeform** /
   **Rectangle** (current mode checked). Pick **Rectangle**.
2. Click the button icon → arms the tool (sticky, green). Cursor is a crosshair
   over the canvas.
3. **Click** the first corner. **Move** the cursor → a dashed magenta rectangle
   rubber-bands from the first corner to the cursor. **Click** the opposite
   corner → the rectangle completes; lines inside or crossing it turn magenta
   (added to the `marked` set). The tool stays armed for another rectangle.
4. **Delete** removes all marked lines (existing, undoable). **Escape** cancels
   an in-progress rectangle and disarms; item navigation clears both.

## 4. Decisions (locked)

| Decision | Choice | Rationale |
|---|---|---|
| Mode switch UI | **Split button** (icon arms current mode; caret opens menu) | One toolbar slot; icon shows the active mode |
| Rectangle interaction | **Click-move-click** (two clicks, mouse-up driven) | Matches the requested flow; distinct from the lasso drag |
| Press-drag-release in rect mode | Sets the first corner on release ("treated as the first click") | Predictable; avoids a second gesture model |
| Selection semantics | **Same as lasso** — crossing (inside or touching) + union | Reuses `findLassoHits`; consistent with freeform |
| `Shift`+drag shortcut | Always a **freeform lasso**, regardless of selected mode | It is a drag gesture; rectangle is click-based |
| Mode persistence | Sticky preference; survives disarm and Escape | Draw several rectangles in a row |
| Target types | `POLYLINE_2D` / `POLYGON_2D` only | Same as freeform |

## 5. Architecture

### 5.1 Modified: `app/src/common/freeform_select_state.ts`

Extend the existing select-tool state with a mode and a rectangle two-click
state machine. The `armed` / `isFreeformActive` / mutual-exclusion logic is
shared across both modes.

```ts
export type SelectMode = "freeform" | "rectangle"

// new module state
let mode: SelectMode = "freeform"        // sticky preference
let rectFirst: Pt | null = null          // first corner (image frame)
let rectCursor: Pt | null = null         // live opposite corner while sizing

getSelectMode(): SelectMode
setSelectMode(m: SelectMode): void        // notify; does not disarm

// rectangle two-click state machine (mouse-up driven)
isRectSizing(): boolean                   // rectFirst !== null
setRectFirstCorner(pt: Pt): void          // rectFirst=pt; rectCursor=pt; notify
updateRectCursor(pt: Pt): void            // rectCursor=pt; notify (only while sizing)
completeRect(pt: Pt): Pt[] | null         // returns 4 corners or null if too small; clears rect state; notify
getRectPreview(): Pt[] | null             // 4 corners [first, (cx,fy), cursor, (fx,cy)] or null

// overlay: one accessor the canvas uses regardless of mode
getSelectionOverlay(): Pt[]               // freeform: the lasso path; rectangle: getRectPreview() ?? []
```

- `resetFreeform()` also clears `rectFirst` / `rectCursor` (but **not** `mode`).
- `completeRect` returns `null` when `|dx| < MIN_RECT_PX || |dy| < MIN_RECT_PX`
  (a click without sizing, or a zero-area rectangle) so it is a no-op.
- Corner order is CW/CCW-consistent so `findLassoHits`'s implicit-close edge is
  correct: `[ {fx,fy}, {cx,fy}, {cx,cy}, {fx,cy} ]`.

### 5.2 Reused unchanged

`findLassoHits` / `runFreeformSelect` / `markLabels` / the magenta highlight /
`commitMarkedDelete` / the Delete key / Escape-clear / nav-clear.

### 5.3 Modified: `app/src/components/label2d_canvas.tsx`

Gesture wiring branches on `getSelectMode()`:

- **`onMouseDown`** (inside the existing `!ctrl && !meta && freeformAllowed`
  guard):
  - `e.shiftKey || (isFreeformActive() && mode === "freeform")` →
    `beginFreeformPath(mousePos)` (lasso, as today).
  - `isFreeformActive() && mode === "rectangle"` → **consume** the mousedown
    (`setCursor("crosshair"); return`) so empty-space pan never arms. The corner
    logic runs on mouse-up.
- **`onMouseMove`:**
  - `isFreeformDrawing()` → `addFreeformPoint` (lasso, as today).
  - else `isFreeformActive() && mode === "rectangle" && isRectSizing()` →
    `updateRectCursor(mousePos); setCursor("crosshair"); return`.
  - cursor override: `if (isFreeformActive()) setCursor("crosshair")` (as today).
- **`onMouseUp`:**
  - `isFreeformDrawing()` → `endFreeformPath` + `runFreeformSelect` (lasso).
  - else `isFreeformActive() && mode === "rectangle"`:
    - not sizing → `setRectFirstCorner(mousePos)` (first click).
    - sizing → `const corners = completeRect(mousePos); if (corners) runFreeformSelect(corners, visibility)`.
    - `setDefaultCursor(); this._labelList.onDrawableUpdate(); return`.
- **Overlay:** `drawFreeformOverlay` reads `getSelectionOverlay()` instead of
  `getFreeformPath()` so it renders the lasso path **or** the rectangle preview
  (dashed magenta, closed) with no other change.
- **Escape / nav:** `resetFreeform()` already clears rectangle state (§5.1).

### 5.4 Modified: `app/src/components/cut_icon.tsx`

Add `RectangleSelectIcon(props)` — a dashed axis-aligned rectangle glyph
(`fill="none"`, `strokeDasharray`) mirroring `FreeformSelectIcon`.

### 5.5 Modified: `app/src/components/viewer2d.tsx`

`getFreeformSelectButton()` becomes a **split button**:

- Main `IconButton`: arms/disarms via `armFreeform()` / `resetFreeform()` with
  the existing guards; shows `FreeformSelectIcon` or `RectangleSelectIcon` per
  `getSelectMode()`; green when `isFreeformArmed()`.
- A caret `IconButton` (`ArrowDropDownIcon`, or an inline caret if the icon is
  unavailable in `@material-ui/icons` v4) opens a Material-UI `Menu` anchored to
  the caret. Two `MenuItem`s (**Freeform** / **Rectangle**) call
  `setSelectMode(...)`; the current mode shows a check. Selecting a mode may
  also arm the tool (so picking a mode is one action). Menu open/anchor is local
  UI state on the viewer.
- The toolbar still contributes a single slot (the split button) beside the cut
  and delete-segment buttons.

## 6. Data flow

```
caret -> Menu -> setSelectMode('rectangle')   (sticky preference)
button -> armFreeform()                        (green; crosshair)

mode = 'rectangle':
  click 1 (mouseUp, idle)   -> setRectFirstCorner(pt)          state: sizing
  mouse-move (sizing)       -> updateRectCursor(pt); overlay redraws (dashed rect)
  click 2 (mouseUp, sizing) -> corners = completeRect(pt)
                               if corners: runFreeformSelect(corners, vis)
                                 -> findLassoHits -> markLabels(union)
                               hit lines turn magenta; state -> idle (armed)
Delete   -> commitMarkedDelete()   (existing)
Escape   -> resetFreeform() + clearMarked()
item-nav -> resetFreeform() + clearMarked()
```

## 7. Coordinate frame

Corners are captured in image coordinates (`getMousePos`), matching stored
vertices, so `findLassoHits` compares like with like. The overlay multiplies by
`displayToImageRatio * _upResRatio` when painting, exactly like the lasso.

## 8. Edge cases & guards

- **Click without sizing / zero-area rectangle:** `completeRect` returns null →
  no-op, first-corner state is cleared so the next click starts fresh.
- **Ctrl/Meta held:** falls through to pan / Ctrl+click-mark; rectangle requires
  the armed mode and excludes Ctrl/Meta.
- **Visibility filters:** honored inside `runFreeformSelect` (unchanged).
- **Tracking / in-progress draw / curves-only:** the button's existing guards
  block arming in all three cases.
- **Mode switch mid-rectangle:** switching modes calls `resetFreeform`-style
  clearing of the in-progress rectangle (no dangling corner).
- **Mutual exclusion:** arming cut/delete-segment disarms the select tool
  (unchanged); the mode preference persists.

## 9. Non-goals (YAGNI)

- No rotated / non-axis-aligned rectangles.
- No drag-to-draw rectangle (two-click only; drag sets the first corner).
- No auto-delete on completion (mark, then Delete).
- No selection of boxes or other label types.
- No per-rectangle undo separate from the existing batch-delete undo.

## 10. Testing

- **Pure state machine** (`freeform_select_state` tests, node-env jest recipe):
  mode get/set persistence; `setRectFirstCorner` → `isRectSizing` true;
  `updateRectCursor` updates preview; `completeRect` returns 4 ordered corners
  for a valid rectangle and `null` for a too-small one; `resetFreeform` clears
  rectangle state but keeps mode.
- **Geometry:** an existing `findLassoHits` test extended with a rectangle
  polygon (inside / crossing / outside) — proves reuse.
- **Runtime** (headless Chrome / CDP): switch to Rectangle via the dropdown,
  two-click a rectangle, confirm crossing lines go magenta, Delete removes them,
  undo restores, Escape clears, and the icon reflects the active mode. (Note: on
  this harness, drive gestures with DOM-dispatched `MouseEvent`s — CDP
  `Input.dispatchMouseEvent` does not register as canvas gestures.)
- **Static:** `npx tsc --noEmit`, `npm run lint` (filter pre-existing CRLF
  `prettier/prettier` noise).

## 11. File change summary

| File | Change |
|---|---|
| `app/src/common/freeform_select_state.ts` | add mode + rectangle two-click state machine + `getSelectionOverlay` |
| `app/src/components/label2d_canvas.tsx` | rectangle gesture wiring; overlay reads `getSelectionOverlay` |
| `app/src/components/cut_icon.tsx` | add `RectangleSelectIcon` |
| `app/src/components/viewer2d.tsx` | split button + mode-switch `Menu` |
| `app/test/common/freeform_select_state.test.ts` | rectangle state-machine tests |
| `app/test/drawable/freeform_select_geometry.test.ts` | rectangle-polygon hit test |
