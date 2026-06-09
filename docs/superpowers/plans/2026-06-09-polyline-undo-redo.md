# Polyline-Level Undo / Redo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Undo/Redo (toolbar buttons + `Ctrl/⌘+Z` / `Ctrl/⌘+Y` / `Ctrl/⌘+Shift+Z`) that remove/restore whole polylines while annotating in the 2D viewer.

**Architecture:** Command pattern over the normal Redux action path so the backend stays in sync. Undo cancels an in-progress drawing first, otherwise dispatches `DELETE_LABELS` for the highest-`order` polyline; Redo dispatches `ADD_LABELS` to recreate the last deleted one. A single `DrawHistory` singleton holds only a small redo stack (no undo stack — the undo target is derived from state). Finishing a brand-new polyline clears the redo stack.

**Tech Stack:** TypeScript, React, Redux, Material-UI, Jest (ts-jest, jsdom).

Reference spec: [docs/superpowers/specs/2026-06-09-polyline-undo-redo-design.md](../specs/2026-06-09-polyline-undo-redo-design.md)

---

## File Structure

| File | Responsibility |
|------|----------------|
| `app/src/drawable/2d/polygon2d.ts` *(modify)* | Add `isDrawing` getter exposing the `DRAW` state. |
| `app/src/drawable/2d/label2d_list.ts` *(modify)* | Add `isDrawingInProgress()` + `cancelDrawing()` (discard the temporary in-progress drawable). |
| `app/src/common/draw_history.ts` *(new)* | `DrawHistory` singleton: `undo()`, `redo()`, `clearRedo()`, `handleKeyboard()`. All orchestration logic. |
| `app/src/components/viewer2d.tsx` *(modify)* | Two toolbar buttons wired to `drawHistory.undo()/redo()`. |
| `app/src/components/label2d_canvas.tsx` *(modify)* | Route keyboard shortcuts to `drawHistory.handleKeyboard()`. |
| `app/src/drawable/states.ts` *(modify)* | In `commit2DLabels`, call `drawHistory.clearRedo()` when a new polyline/polygon is committed. |
| `app/test/drawable/draw_history.test.ts` *(new)* | Unit/integration tests using the existing drawable test harness. |

**Common commands** (run from repo root `d:\Nikhil\Projects\GitHub\scalabel`):
- Run one test file: `npx jest app/test/drawable/draw_history.test.ts`
- Typecheck: `npx tsc --noEmit`
- Lint: `npm run lint`

---

### Task 1: Drawing-state helpers on the drawable + label list

**Files:**
- Modify: `app/src/drawable/2d/polygon2d.ts` (add `isDrawing` getter near the other getters, ~line 82)
- Modify: `app/src/drawable/2d/label2d_list.ts` (add two methods after `getLabelById`, ~line 142)
- Test: `app/test/drawable/draw_history.test.ts` (new)

- [ ] **Step 1: Write the failing test**

Create `app/test/drawable/draw_history.test.ts`:

```typescript
import * as action from "../../src/action/common"
import Session, { getState } from "../../src/common/session"
import { getNumLabels } from "../../src/functional/state_util"
import { Size2D } from "../../src/math/size2d"
import { initializeTestingObjects, mouseMoveClick } from "./util"

describe("Drawing-state helpers", () => {
  test("isDrawingInProgress and cancelDrawing", () => {
    const itemIndex = 0
    const [label2dHandler] = initializeTestingObjects()
    const canvasSize = new Size2D(1000, 1000)
    dispatchPolygonMode()

    // Place two vertices but do NOT finish the polygon
    mouseMoveClick(label2dHandler, 10, 10, canvasSize, -1, 0)
    mouseMoveClick(label2dHandler, 100, 100, canvasSize, -1, 0)

    expect(Session.label2dList.isDrawingInProgress()).toBe(true)
    expect(getNumLabels(getState(), itemIndex)).toEqual(0)

    Session.label2dList.cancelDrawing()

    expect(Session.label2dList.isDrawingInProgress()).toBe(false)
    expect(Session.label2dList.labelList.length).toEqual(0)
    expect(getNumLabels(getState(), itemIndex)).toEqual(0)
  })
})

/** Select the polygon label type (index 1 in the test config) */
function dispatchPolygonMode(): void {
  Session.dispatch(action.changeSelect({ labelType: 1 }))
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx jest app/test/drawable/draw_history.test.ts`
Expected: FAIL — `Session.label2dList.isDrawingInProgress is not a function`.

- [ ] **Step 3: Add the `isDrawing` getter to `Polygon2D`**

In `app/src/drawable/2d/polygon2d.ts`, add this getter right after the `highlightCursor` getter (after the block ending ~line 94):

```typescript
  /** Whether this polygon is currently being drawn (vertices being placed) */
  public get isDrawing(): boolean {
    return this._state === Polygon2DState.DRAW
  }
```

- [ ] **Step 4: Add the helpers to `Label2DList`**

In `app/src/drawable/2d/label2d_list.ts`, add these two methods immediately after the `getLabelById` method (after the block ending ~line 142). `Polygon2D` is already imported at the top of this file.

```typescript
  /** Whether a polyline/polygon is currently being drawn (unfinished) */
  public isDrawingInProgress(): boolean {
    const label = this._selectedLabels[0]
    return label instanceof Polygon2D && label.isDrawing
  }

  /**
   * Discard the in-progress (temporary) drawing without committing it.
   * Mirrors how commit2DLabels drops invalid temporary drawables, but on demand.
   */
  public cancelDrawing(): void {
    const label = this._selectedLabels[0]
    if (!(label instanceof Polygon2D && label.isDrawing)) {
      return
    }
    label.editing = false
    const selectedIndex = this._selectedLabels.indexOf(label)
    if (selectedIndex >= 0) {
      this._selectedLabels.splice(selectedIndex, 1)
    }
    const listIndex = this._labelList.indexOf(label)
    if (listIndex >= 0) {
      this._labelList.splice(listIndex, 1)
    }
    this.onDrawableUpdate()
  }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npx jest app/test/drawable/draw_history.test.ts`
Expected: PASS.

- [ ] **Step 6: Typecheck**

Run: `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add app/src/drawable/2d/polygon2d.ts app/src/drawable/2d/label2d_list.ts app/test/drawable/draw_history.test.ts
git commit -m "feat: add isDrawing / isDrawingInProgress / cancelDrawing helpers"
```

---

### Task 2: `DrawHistory` singleton (undo / redo / clearRedo / handleKeyboard)

**Files:**
- Create: `app/src/common/draw_history.ts`
- Test: `app/test/drawable/draw_history.test.ts` (extend)

- [ ] **Step 1: Write the failing tests**

Append these `describe` blocks to `app/test/drawable/draw_history.test.ts`. Add `drawPolygon` to the import from `./util` and add the `drawHistory` import at the top:

```typescript
// add to the existing "./util" import: drawPolygon
// add this import near the top of the file:
import { drawHistory } from "../../src/common/draw_history"

describe("DrawHistory undo/redo", () => {
  const polyA: number[][] = [
    [10, 10],
    [100, 100],
    [200, 100],
    [100, 0]
  ]
  const polyB: number[][] = [
    [500, 500],
    [600, 400],
    [700, 700]
  ]

  test("undo removes last completed polyline, redo restores it", () => {
    const itemIndex = 0
    const [label2dHandler] = initializeTestingObjects()
    const canvasSize = new Size2D(1000, 1000)
    drawHistory.clearRedo()
    Session.dispatch(action.changeSelect({ labelType: 1 }))

    drawPolygon(label2dHandler, canvasSize, polyA)
    drawPolygon(label2dHandler, canvasSize, polyB)
    expect(getNumLabels(getState(), itemIndex)).toEqual(2)

    expect(drawHistory.undo()).toBe(true)
    expect(getNumLabels(getState(), itemIndex)).toEqual(1)

    expect(drawHistory.redo()).toBe(true)
    expect(getNumLabels(getState(), itemIndex)).toEqual(2)
  })

  test("undo with nothing to undo returns false", () => {
    initializeTestingObjects()
    drawHistory.clearRedo()
    Session.dispatch(action.changeSelect({ labelType: 1 }))
    expect(drawHistory.undo()).toBe(false)
  })

  test("redo with empty stack returns false", () => {
    initializeTestingObjects()
    drawHistory.clearRedo()
    expect(drawHistory.redo()).toBe(false)
  })

  test("undo cancels an in-progress drawing (not redoable)", () => {
    const itemIndex = 0
    const [label2dHandler] = initializeTestingObjects()
    const canvasSize = new Size2D(1000, 1000)
    drawHistory.clearRedo()
    Session.dispatch(action.changeSelect({ labelType: 1 }))

    // Start drawing but do not finish
    mouseMoveClick(label2dHandler, 10, 10, canvasSize, -1, 0)
    mouseMoveClick(label2dHandler, 100, 100, canvasSize, -1, 0)
    expect(Session.label2dList.isDrawingInProgress()).toBe(true)

    expect(drawHistory.undo()).toBe(true)
    expect(Session.label2dList.isDrawingInProgress()).toBe(false)
    expect(getNumLabels(getState(), itemIndex)).toEqual(0)
    // Cancelling an unfinished shape is not redoable
    expect(drawHistory.redo()).toBe(false)
  })

  test("clearRedo invalidates a pending redo", () => {
    const itemIndex = 0
    const [label2dHandler] = initializeTestingObjects()
    const canvasSize = new Size2D(1000, 1000)
    drawHistory.clearRedo()
    Session.dispatch(action.changeSelect({ labelType: 1 }))

    drawPolygon(label2dHandler, canvasSize, polyA)
    expect(drawHistory.undo()).toBe(true)
    expect(getNumLabels(getState(), itemIndex)).toEqual(0)

    drawHistory.clearRedo()
    expect(drawHistory.redo()).toBe(false)
    expect(getNumLabels(getState(), itemIndex)).toEqual(0)
  })
})

describe("DrawHistory.handleKeyboard", () => {
  test("Ctrl+Z undoes, Ctrl+Y redoes, plain keys ignored", () => {
    const itemIndex = 0
    const [label2dHandler] = initializeTestingObjects()
    const canvasSize = new Size2D(1000, 1000)
    drawHistory.clearRedo()
    Session.dispatch(action.changeSelect({ labelType: 1 }))
    drawPolygon(label2dHandler, canvasSize, [
      [10, 10],
      [100, 100],
      [200, 100],
      [100, 0]
    ])
    expect(getNumLabels(getState(), itemIndex)).toEqual(1)

    // Plain "z" without modifier is ignored
    expect(
      drawHistory.handleKeyboard(new KeyboardEvent("keydown", { key: "z" }))
    ).toBe(false)

    // Ctrl+Z undoes
    expect(
      drawHistory.handleKeyboard(
        new KeyboardEvent("keydown", { key: "z", ctrlKey: true })
      )
    ).toBe(true)
    expect(getNumLabels(getState(), itemIndex)).toEqual(0)

    // Ctrl+Y redoes
    expect(
      drawHistory.handleKeyboard(
        new KeyboardEvent("keydown", { key: "y", ctrlKey: true })
      )
    ).toBe(true)
    expect(getNumLabels(getState(), itemIndex)).toEqual(1)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx jest app/test/drawable/draw_history.test.ts`
Expected: FAIL — `Cannot find module '../../src/common/draw_history'`.

- [ ] **Step 3: Create the `DrawHistory` module**

Create `app/src/common/draw_history.ts`:

```typescript
import _ from "lodash"

import { addLabel, deleteLabel } from "../action/common"
import { LabelTypeName } from "../const/common"
import { getShapes } from "../functional/state_util"
import { LabelType, ShapeType } from "../types/state"
import Session, { dispatch, getState } from "./session"

/** A removed polyline, holding everything needed to recreate it. */
interface RemovedPolyline {
  /** Item the polyline belongs to */
  itemIndex: number
  /** The label definition */
  label: LabelType
  /** The label's shapes */
  shapes: ShapeType[]
}

/**
 * Polyline-level undo/redo for the 2D annotator.
 *
 * Undo/redo dispatch normal ADD_LABELS / DELETE_LABELS actions so the
 * synchronizer keeps the backend in sync. No undo stack is kept: the label to
 * undo is derived from state (highest-order polyline). Only a redo stack of
 * removed polylines is held.
 */
export class DrawHistory {
  /** Removed completed polylines available for redo */
  private _redoStack: RemovedPolyline[]

  /** Constructor */
  constructor() {
    this._redoStack = []
  }

  /**
   * Undo the most recent polyline. An in-progress drawing is cancelled first
   * (not redoable); otherwise the last completed polyline is deleted.
   *
   * @returns whether anything was undone
   */
  public undo(): boolean {
    if (Session.label2dList.isDrawingInProgress()) {
      Session.label2dList.cancelDrawing()
      return true
    }
    const state = getState()
    const itemIndex = state.user.select.item
    const item = state.task.items[itemIndex]
    let target: LabelType | null = null
    for (const labelId of Object.keys(item.labels)) {
      const label = item.labels[labelId]
      if (
        label.type === LabelTypeName.POLYGON_2D ||
        label.type === LabelTypeName.POLYLINE_2D
      ) {
        if (target === null || label.order > target.order) {
          target = label
        }
      }
    }
    if (target === null) {
      return false
    }
    const shapes = getShapes(state, itemIndex, target.id)
    this._redoStack.push({
      itemIndex,
      label: _.cloneDeep(target),
      shapes: _.cloneDeep(shapes)
    })
    dispatch(deleteLabel(itemIndex, target.id))
    return true
  }

  /**
   * Redo the last completed polyline removed by undo.
   *
   * @returns whether anything was redone
   */
  public redo(): boolean {
    const entry = this._redoStack.pop()
    if (entry === undefined) {
      return false
    }
    dispatch(addLabel(entry.itemIndex, entry.label, entry.shapes))
    return true
  }

  /** Clear the redo stack (called when a new polyline is drawn). */
  public clearRedo(): void {
    this._redoStack = []
  }

  /**
   * Handle a keyboard event. Ctrl/Cmd+Z = undo, Ctrl/Cmd+Y or
   * Ctrl/Cmd+Shift+Z = redo.
   *
   * @param e the keyboard event
   * @returns whether the event was acted on
   */
  public handleKeyboard(e: KeyboardEvent): boolean {
    if (!(e.ctrlKey || e.metaKey)) {
      return false
    }
    const key = e.key.toLowerCase()
    if (key === "z" && !e.shiftKey) {
      return this.undo()
    }
    if (key === "y" || (key === "z" && e.shiftKey)) {
      return this.redo()
    }
    return false
  }
}

/** Global singleton instance */
export const drawHistory = new DrawHistory()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx jest app/test/drawable/draw_history.test.ts`
Expected: PASS (all describe blocks).

- [ ] **Step 5: Typecheck + lint**

Run: `npx tsc --noEmit`
Run: `npm run lint`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add app/src/common/draw_history.ts app/test/drawable/draw_history.test.ts
git commit -m "feat: add DrawHistory for polyline-level undo/redo"
```

---

### Task 3: Toolbar Undo/Redo buttons

**Files:**
- Modify: `app/src/components/viewer2d.tsx` (imports near the top; buttons in `getMenuComponents`, ~line 204-265)

> No Jest test: the existing zoom / line-width toolbar buttons are not unit-tested (they wrap Material-UI `IconButton` in a class component that needs heavy context). The behavior they call (`drawHistory`) is fully covered by Task 2. This task is verified by typecheck, lint, and runtime.

- [ ] **Step 1: Add icon + module imports**

In `app/src/components/viewer2d.tsx`, add these imports alongside the existing `@material-ui/icons/*` imports (after the `ZoomOutIcon` import, ~line 9):

```typescript
import RedoIcon from "@material-ui/icons/Redo"
import UndoIcon from "@material-ui/icons/Undo"
```

And add the singleton import alongside the other `../common/*` imports (place near the existing `Session` import):

```typescript
import { drawHistory } from "../common/draw_history"
```

- [ ] **Step 2: Add the two buttons**

In `getMenuComponents`, after the `widthResetButton` definition (immediately before `return [` at ~line 257), add:

```typescript
      const undoButton = (
        <Tooltip
          key={`undo2dButton${this.props.id}`}
          title="Undo last polyline"
          enterDelay={500}
          TransitionComponent={Fade}
          TransitionProps={{ timeout: 600 }}
          arrow
        >
          <IconButton
            onClick={() => drawHistory.undo()}
            className={this.props.classes.viewer_button}
            edge={"start"}
          >
            <UndoIcon />
          </IconButton>
        </Tooltip>
      )
      const redoButton = (
        <Tooltip
          key={`redo2dButton${this.props.id}`}
          title="Redo polyline"
          enterDelay={500}
          TransitionComponent={Fade}
          TransitionProps={{ timeout: 600 }}
          arrow
        >
          <IconButton
            onClick={() => drawHistory.redo()}
            className={this.props.classes.viewer_button}
            edge={"start"}
          >
            <RedoIcon />
          </IconButton>
        </Tooltip>
      )
```

- [ ] **Step 3: Add the buttons to the returned array**

Change the `return [ ... ]` in `getMenuComponents` (~line 258-265) to include the two new buttons at the end:

```typescript
      return [
        zoomInButton,
        zoomOutButton,
        resetZoomButton,
        widthUpButton,
        widthDownButton,
        widthResetButton,
        undoButton,
        redoButton
      ]
```

- [ ] **Step 4: Typecheck + lint**

Run: `npx tsc --noEmit`
Run: `npm run lint`
Expected: no errors. (If lint enforces import ordering, ensure the new imports are alphabetized within their group.)

- [ ] **Step 5: Runtime verify**

Start the app, open a 2D polyline/polygon task. Confirm two new buttons (undo + redo icons) appear in the viewer toolbar next to the line-width buttons. Draw two polylines, click Undo twice (both disappear, last-first), click Redo twice (they return). Confirm no console errors.

- [ ] **Step 6: Commit**

```bash
git add app/src/components/viewer2d.tsx
git commit -m "feat: add undo/redo toolbar buttons to 2D viewer"
```

---

### Task 4: Keyboard shortcuts in the label canvas

**Files:**
- Modify: `app/src/components/label2d_canvas.tsx` (`onKeyDown`, ~line 467-476; import near the top)

> No Jest test: `handleKeyboard` itself is unit-tested in Task 2. This is thin wiring inside a React component with document-level listeners; verified by typecheck, lint, and runtime.

- [ ] **Step 1: Import the singleton**

In `app/src/components/label2d_canvas.tsx`, add alongside the other `../common/*` imports (near the existing `Session` import):

```typescript
import { drawHistory } from "../common/draw_history"
```

- [ ] **Step 2: Route the shortcut in `onKeyDown`**

Replace the body of `onKeyDown` (currently ~line 467-476):

```typescript
  public onKeyDown(e: KeyboardEvent): void {
    if (this.checkFreeze()) {
      return
    }

    const key = e.key
    this._keyDownMap[key] = true
    this._labelHandler.onKeyDown(e)
    this._labelList.onDrawableUpdate()
  }
```

with:

```typescript
  public onKeyDown(e: KeyboardEvent): void {
    if (this.checkFreeze()) {
      return
    }

    // Polyline-level undo/redo (Ctrl/Cmd+Z / Ctrl/Cmd+Y / Ctrl/Cmd+Shift+Z).
    // Only swallow the shortcut when it actually did something.
    if (drawHistory.handleKeyboard(e)) {
      e.preventDefault()
      return
    }

    const key = e.key
    this._keyDownMap[key] = true
    this._labelHandler.onKeyDown(e)
    this._labelList.onDrawableUpdate()
  }
```

- [ ] **Step 3: Typecheck + lint**

Run: `npx tsc --noEmit`
Run: `npm run lint`
Expected: no errors.

- [ ] **Step 4: Runtime verify**

In a 2D polyline task: draw two polylines, press `Ctrl/⌘+Z` twice (both removed), `Ctrl/⌘+Y` twice (both restored), and `Ctrl/⌘+Shift+Z` (also redoes). Confirm the existing `D` (delete vertex while drawing) and `Enter` (finish) still work, and that `Ctrl+Z` with nothing to undo does not throw.

- [ ] **Step 5: Commit**

```bash
git add app/src/components/label2d_canvas.tsx
git commit -m "feat: wire undo/redo keyboard shortcuts in label canvas"
```

---

### Task 5: Clear the redo stack when a new polyline is committed

**Files:**
- Modify: `app/src/drawable/states.ts` (`commit2DLabels`, ~line 228-274; import near the top)
- Test: `app/test/drawable/draw_history.test.ts` (extend)

- [ ] **Step 1: Write the failing test**

Append to `app/test/drawable/draw_history.test.ts`:

```typescript
describe("Redo stack clears when a new polyline is drawn", () => {
  test("drawing after undo invalidates redo", () => {
    const itemIndex = 0
    const [label2dHandler] = initializeTestingObjects()
    const canvasSize = new Size2D(1000, 1000)
    drawHistory.clearRedo()
    Session.dispatch(action.changeSelect({ labelType: 1 }))

    drawPolygon(label2dHandler, canvasSize, [
      [10, 10],
      [100, 100],
      [200, 100],
      [100, 0]
    ])
    drawPolygon(label2dHandler, canvasSize, [
      [500, 500],
      [600, 400],
      [700, 700]
    ])
    expect(getNumLabels(getState(), itemIndex)).toEqual(2)

    // Undo the second polyline (redo stack now has 1 entry)
    expect(drawHistory.undo()).toBe(true)
    expect(getNumLabels(getState(), itemIndex)).toEqual(1)

    // Draw a NEW polyline — this must clear the redo stack via commit2DLabels
    drawPolygon(label2dHandler, canvasSize, [
      [300, 300],
      [350, 350],
      [400, 300]
    ])
    expect(getNumLabels(getState(), itemIndex)).toEqual(2)

    // Redo must now be a no-op
    expect(drawHistory.redo()).toBe(false)
    expect(getNumLabels(getState(), itemIndex)).toEqual(2)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx jest app/test/drawable/draw_history.test.ts -t "drawing after undo invalidates redo"`
Expected: FAIL — `drawHistory.redo()` returns `true` and label count becomes 3 (redo stack was not cleared).

- [ ] **Step 3: Add the imports to `states.ts`**

In `app/src/drawable/states.ts`, add alongside the existing imports:

```typescript
import { LabelTypeName } from "../const/common"
import { drawHistory } from "../common/draw_history"
```

- [ ] **Step 4: Detect a new polyline and clear the redo stack**

In `commit2DLabels` (`app/src/drawable/states.ts`), track whether a new polyline/polygon drawable was added, and clear the redo stack after dispatching. Modify the function so it reads:

```typescript
export function commit2DLabels(
  updatedLabelDrawables: Array<Readonly<Label2D>>
): void {
  const state = getState()
  const numItems = state.task.items.length
  const updatedShapes: ItemShapeIdMap = {}
  const updatedLabels: ItemLabelIdMap = {}
  const tracking = state.task.config.tracking
  const actions: BaseAction[] = []
  let newPolylineCommitted = false
  updatedLabelDrawables.forEach((drawable) => {
    drawable.setManual()
    if (drawable.isValid()) {
      // Valid drawable
      if (!drawable.temporary) {
        // Existing drawable
        if (tracking) {
          updateTrack(drawable, updatedLabels, updatedShapes)
        } else {
          updateLabel(drawable, updatedLabels, updatedShapes)
        }
      } else {
        // New drawable
        if (
          drawable.type === LabelTypeName.POLYGON_2D ||
          drawable.type === LabelTypeName.POLYLINE_2D
        ) {
          newPolylineCommitted = true
        }
        if (tracking) {
          // Add track
          actions.push(addNewTrack(drawable, numItems))
        } else {
          // Add labels
          actions.push(addNewLabel(drawable))
        }
      }
    } else {
      // Invalid drawable
      if (!drawable.temporary) {
        // Existing drawable
        if (tracking) {
          actions.push(terminateTrackFromDrawable(drawable, numItems))
        } else {
          actions.push(deleteInvalidLabel(drawable))
        }
      }
      // New invalid drawable should be dropped. nothing happens.
    }
  })
  actions.push(commitLabelsToState(updatedLabels))
  actions.push(commitShapesToState(updatedShapes))
  dispatch(makeSequential(actions, true))
  if (newPolylineCommitted) {
    drawHistory.clearRedo()
  }
}
```

> Note: `redo()` recreates a polyline via `addLabel` directly (not through `commit2DLabels`), so redo does **not** trip this clear — only genuine user draws do.

- [ ] **Step 5: Run the test to verify it passes**

Run: `npx jest app/test/drawable/draw_history.test.ts -t "drawing after undo invalidates redo"`
Expected: PASS.

- [ ] **Step 6: Run the full test file + typecheck + lint**

Run: `npx jest app/test/drawable/draw_history.test.ts`
Run: `npx tsc --noEmit`
Run: `npm run lint`
Expected: all PASS / no errors.

- [ ] **Step 7: Commit**

```bash
git add app/src/drawable/states.ts app/test/drawable/draw_history.test.ts
git commit -m "feat: clear redo stack when a new polyline is committed"
```

---

## Final Verification (runtime, whole feature)

Run the app on a 2D polyline/polygon task and confirm:

1. Draw 3 polylines (Enter to finish each). Undo ×3 (buttons) → each removes the most recently finished polyline, last-drawn first.
2. Redo ×3 → polylines reappear in reverse order.
3. Start a 4th polyline (place 2 vertices, don't finish) → Undo → the unfinished shape disappears; finished polylines untouched.
4. After an Undo, draw + finish a new polyline → Redo no longer resurrects the previously undone polyline.
5. `Ctrl/⌘+Z` / `Ctrl/⌘+Y` / `Ctrl/⌘+Shift+Z` mirror the buttons. Existing `D` (delete vertex) and `Enter` (finish) still work.
6. **Sync check:** after a sequence of undo/redo, reload the page; the server-persisted annotations match the screen. `/getExport` reflects exactly the visible polylines.

Run the full suite once: `npm test` (or at least `npx jest app/test/drawable`) and confirm no regressions.

---

## Self-Review Notes

- **Spec coverage:** command-pattern undo/redo (Task 2), in-progress-first then completed (Task 1 + 2), polyline/polygon-only scope (Task 2 filter + Task 5 filter), redo-clear on new draw (Task 5), buttons (Task 3), keyboard incl. Shift+Z (Task 2/4), sync-safety via normal actions (Task 2), edge cases no-op (Task 2 tests). All present.
- **Type consistency:** `drawHistory` singleton and methods `undo()/redo()/clearRedo()/handleKeyboard()`; `Label2DList.isDrawingInProgress()/cancelDrawing()`; `Polygon2D.isDrawing`; `drawable.type` compared to `LabelTypeName.POLYGON_2D/POLYLINE_2D` — names consistent across Tasks 1–5.
- **No placeholders:** every code/test step contains complete code and exact commands.
