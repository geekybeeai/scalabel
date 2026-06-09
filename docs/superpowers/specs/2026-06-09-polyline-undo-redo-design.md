# 2D Viewer: Polyline-Level Undo / Redo — Design

Date: 2026-06-09
Status: Approved
Scope: 2D image annotator (embedded lane tool). Polyline/polygon labels only. No
3D/point-cloud changes.

## Overview

Add **Undo** and **Redo** controls to the 2D viewer that operate at **whole-polyline
granularity** while annotating:

- **Undo** removes the most recent polyline. If a polyline is currently being drawn
  (unfinished), Undo discards that in-progress shape first; otherwise it deletes the
  last *completed* polyline.
- **Redo** restores the last completed polyline that Undo removed.

Triggers: two toolbar buttons **and** the keyboard shortcuts `Ctrl/⌘+Z` (undo) /
`Ctrl/⌘+Y` and `Ctrl/⌘+Shift+Z` (redo).

This is display/interaction only in spirit, but unlike the zoom/line-width features it
**does** change annotation data (it adds/deletes labels) — so it must go through the
normal action path to stay in sync with the backend.

---

## Why not the existing redux-undo

`configure_store.ts` already wraps the reducer in `redux-undo` (filtering `ADD_LABELS` /
`DELETE_LABELS`, limit 20), but it is **never triggered** anywhere — no code dispatches
`UndoActionCreators.undo()/redo()`. More importantly, redux-undo performs *time travel*:
it rolls `present` back to a past snapshot **without dispatching an action**. The
[`Synchronizer`](../../../app/src/common/synchronizer.ts) forwards dispatched actions to
the server; a silent time-travel rollback would never reach it, so the server's saved
annotations would **desync** from the client. Therefore redux-undo is not reused.

Instead we use a **command pattern**: undo dispatches a real `DELETE_LABELS`, redo
dispatches a real `ADD_LABELS`. Both flow through the normal middleware/sync path, so the
server stays consistent.

## Design decisions (locked)

- **Granularity:** whole polyline, not per-vertex. (The existing `D` key still deletes the
  last vertex while drawing; this feature is independent of it.)
- **Undo target:** in-progress unfinished shape first, else the last completed polyline.
- **Label scope:** polyline/polygon labels only (`LabelTypeName.POLYLINE_2D` /
  `POLYGON_2D`). Other label types are ignored.
- **Redo semantics:** standard — finishing a brand-new polyline clears the redo stack.
- **Unfinished-shape cancel is NOT redoable** — only completed-polyline deletions can be
  redone (you can simply redraw an unfinished shape).
- **Home for the logic:** a new dedicated singleton module `draw_history.ts` (not bolted
  onto `Session`).

---

## Architecture

### History model

We keep **no undo stack**. The "thing to undo" is always derived fresh from state: among
the current item's polyline/polygon labels, the one with the **highest `order`** is the
most recently drawn (order is monotonically increasing and is never decremented on
delete, so the max-order polyline is always the latest existing one). Deriving it from
state means it can never drift out of sync with reality.

We keep a small **redo stack** only: an array of
`{ itemIndex: number, label: LabelType, shapes: ShapeType[] }` entries — the data needed
to recreate a removed polyline. This is the entire memory footprint ("history of lines").

### Flow

**Undo** (`drawHistory.undo()`):
1. If `Session.label2dList.isDrawingInProgress()` → `Session.label2dList.cancelDrawing()`
   (discard the temporary in-progress drawable and repaint). Return. *(Not pushed to the
   redo stack.)*
2. Else, read `getState()`, take the current item (`state.user.select.item`). Find
   polyline/polygon labels; pick the one with the highest `order`. If none → no-op (return
   `false`).
3. Capture `{ itemIndex, label, shapes }` (shapes via
   [`getShapes(state, itemIndex, labelId)`](../../../app/src/functional/state_util.ts)).
4. `Session.dispatch(deleteLabel(itemIndex, labelId))`.
5. Push the captured entry onto `_redoStack`. Return `true`.

**Redo** (`drawHistory.redo()`):
1. If `_redoStack` is empty → no-op (return `false`).
2. Pop `{ itemIndex, label, shapes }`; `Session.dispatch(addLabel(itemIndex, label,
   shapes))`. Return `true`. *(Goes straight through `addLabel`, not the drawing-commit
   path, so it does not trip `clearRedo()`.)*

**Redo invalidation** (`drawHistory.clearRedo()`):
- Called when the user **finishes drawing a brand-new polyline** (the handler's draw-commit
  paths). Clears `_redoStack` so a newly drawn shape invalidates pending redos (standard
  editor behavior).

### Identifying / cancelling the in-progress drawing

During drawing, the temporary `Polygon2D` (state `DRAW`, `temporary === true`,
`editing === true`) lives in `Session.label2dList.selectedLabels[0]` and in the label-list
array (pushed by `Label2DHandler.onMouseDown`). It is **not** in redux state until
committed.

- `Label2DList.isDrawingInProgress(): boolean` — true when `selectedLabels[0]` is a
  `Polygon2D` whose state is `DRAW`.
- `Label2DList.cancelDrawing(): void` — remove that temporary drawable from
  `selectedLabels` and the label-list array, clear its `editing` flag, and call
  `onDrawableUpdate()` to repaint. This mirrors how `commit2DLabels` already drops
  *invalid temporary* drawables ("New invalid drawable should be dropped"), but is invoked
  on demand by undo.

---

## Components / Files

| File | Change |
|------|--------|
| `app/src/common/draw_history.ts` *(new)* | Singleton manager holding `_redoStack` and `undo()` / `redo()` / `clearRedo()`. Pure orchestration over `getState()`, `Session.dispatch`, and `Session.label2dList`. Independently unit-testable. |
| `app/src/drawable/2d/label2d_list.ts` | Add `isDrawingInProgress()` and `cancelDrawing()`. |
| `app/src/components/viewer2d.tsx` | Two toolbar buttons in `getMenuComponents`: **Undo** (`@material-ui/icons/Undo`) → `drawHistory.undo()`, **Redo** (`@material-ui/icons/Redo`) → `drawHistory.redo()`. Same `Tooltip` + `IconButton` + `viewer_button` styling as the zoom / line-width buttons. |
| `app/src/components/label2d_canvas.tsx` | In `onKeyDown`: `Ctrl/⌘+Z` → `drawHistory.undo()`; `Ctrl/⌘+Y` or `Ctrl/⌘+Shift+Z` → `drawHistory.redo()`. Call `e.preventDefault()` only when the action actually did something, so the shortcut is not hijacked when there is nothing to undo/redo. |
| `app/src/drawable/2d/label2d_handler.ts` | In the draw-finish commit paths (Enter / close-on-mouse-up that commit a **new** temporary polyline), call `drawHistory.clearRedo()`. |

### Action signatures used (already exist)

- `addLabel(itemIndex, label, shapes)` → `ADD_LABELS`
  ([common.ts:168](../../../app/src/action/common.ts)).
- `deleteLabel(itemIndex, labelId)` → `DELETE_LABELS`
  ([common.ts:407](../../../app/src/action/common.ts)).
- `getShapes(state, itemIndex, labelId)`
  ([state_util.ts:166](../../../app/src/functional/state_util.ts)).

---

## Edge cases

- **Nothing to undo** (no in-progress shape, no completed polyline): no-op; do not
  `preventDefault` the keyboard shortcut.
- **Empty redo stack:** redo is a no-op.
- **Re-added polyline gets a fresh id/order.** That is fine — it is an equivalent shape and
  the round-trip stays sync-clean.
- **Non-polyline labels are ignored** by both undo selection and the redo stack.
- **Buttons always visible** in the 2D toolbar; they simply no-op when there is nothing to
  act on (consistent with always-enabled zoom/line-width buttons).
- **Undo of an unfinished shape** does not populate redo (decision (a)).

---

## Verification (runtime, by driving the editor)

1. Draw 3 polylines (finish each with Enter). Click **Undo** three times → each click
   removes the most recently completed polyline, last-drawn first.
2. Click **Redo** three times → the polylines reappear in reverse order.
3. Start drawing a 4th polyline (place a couple of vertices, do **not** finish) and click
   **Undo** → the unfinished shape disappears; completed polylines are untouched.
4. After an Undo, draw a new polyline and finish it → **Redo** no longer resurrects the
   previously undone polyline (redo stack cleared).
5. Confirm both the **buttons** and `Ctrl/⌘+Z` / `Ctrl/⌘+Y` (and `Ctrl/⌘+Shift+Z`) drive
   the same behavior. Confirm the existing `D` (delete vertex) and `Enter` (finish) still
   work.
6. **Sync check:** after a sequence of undo/redo, reload the page and confirm the
   server-persisted annotations match what is on screen (no desync). Confirm exported JSON
   (`/getExport`) reflects exactly the visible polylines.

Builds verified with `tsc --noEmit` + `npm run build`. Nothing is committed by the
implementer beyond this design doc; the user commits code changes manually.

---

## Out of scope / future

- Per-vertex undo/redo (superseded by polyline granularity).
- Undo/redo for reshape/move edits of existing polylines (only draw + whole-polyline
  delete here).
- Undo across multiple items / a global multi-item history (current item only).
- Persisting the redo stack across page reloads.
