# Multi-select Lines for Batch Delete — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the annotator Ctrl/Cmd+click polylines and polygons to mark them for deletion (highlighted green), then delete the whole marked set with the Delete key.

**Architecture:** A transient (non-Redux) module `multi_delete_state.ts` holds a `Set` of marked label ids and notifies listeners on change — mirroring the existing `cut_state.ts` / `segment_delete_state.ts` tool-state modules. `Polygon2D.draw()` reads that set live and paints marked lines green. The Delete key routes through a standalone `commitMarkedDelete()` that snapshots each marked line for undo, dispatches one atomic `deleteLabels`, and clears the set. Ctrl+click marking, the redraw subscription, Escape, and item-navigation clearing are wired in `label2d_canvas.tsx`; the Delete-key entry point is `toolbar.deletePressed()`.

**Tech Stack:** TypeScript, React, Redux, HTML canvas, Jest (`npx jest`), ESLint, ts (`npx tsc --noEmit`). Package manager: **npm**.

**Spec:** [docs/superpowers/specs/2026-07-09-multi-select-line-delete-design.md](../specs/2026-07-09-multi-select-line-delete-design.md)

## Global Constraints

- Highlight color is exactly `rgba(0, 230, 0, 0.95)` (the segment-delete overlay color, [label2d_canvas.tsx:805](../../../app/src/components/label2d_canvas.tsx#L805)).
- Markable types: `LabelTypeName.POLYLINE_2D` and `LabelTypeName.POLYGON_2D` only. Boxes / custom labels are never marked.
- The green override applies in `DrawMode.VIEW` **only** — never on the CONTROL canvas (it would corrupt hit-test color encoding).
- Marking is disabled in tracking tasks (`state.task.config.tracking === true`): Ctrl+click falls through to panning.
- Ctrl/Cmd+drag on empty canvas (or on a box) must still pan — do not regress [label2d_canvas.tsx:487](../../../app/src/components/label2d_canvas.tsx#L487).
- The marked set is one-item-scoped; it is cleared on Escape, on Delete, and on item navigation.
- Undo restores deleted lines one at a time (reuses the existing `"deleted"` command kind), matching the annotator's existing multi-label delete.
- Follow existing code style: 2-space indent, no semicolons, JSDoc on exported functions (match `segment_delete_state.ts`).

**Run a single test file:** `npx jest <path>`
**Typecheck:** `npx tsc --noEmit -p tsconfig.json`
**Lint (touched files):** `npx eslint -c .eslintrc.json --ext .ts,.tsx app/src app/test`

---

### Task 1: Transient marked-set module

**Files:**
- Create: `app/src/common/multi_delete_state.ts`
- Test: `app/test/common/multi_delete_state.test.ts`

**Interfaces:**
- Consumes: `IdType` from `app/src/types/state`.
- Produces (used by Tasks 2–5):
  - `isMarked(labelId: IdType): boolean`
  - `toggleMarked(labelId: IdType): void`
  - `getMarked(): IdType[]`
  - `markedCount(): number`
  - `clearMarked(): void`
  - `onMarkedChange(listener: () => void): () => void`

- [ ] **Step 1: Write the failing test**

Create `app/test/common/multi_delete_state.test.ts`:

```ts
import {
  clearMarked,
  getMarked,
  isMarked,
  markedCount,
  onMarkedChange,
  toggleMarked
} from "../../src/common/multi_delete_state"

describe("multi_delete_state", () => {
  beforeEach(() => {
    clearMarked()
  })

  test("toggles ids in and out of the marked set", () => {
    expect(isMarked("lineA")).toBe(false)
    expect(markedCount()).toBe(0)

    toggleMarked("lineA")
    expect(isMarked("lineA")).toBe(true)
    expect(markedCount()).toBe(1)

    toggleMarked("lineB")
    expect(getMarked().sort()).toEqual(["lineA", "lineB"])

    toggleMarked("lineA") // toggle off
    expect(isMarked("lineA")).toBe(false)
    expect(getMarked()).toEqual(["lineB"])
  })

  test("clearMarked empties the set", () => {
    toggleMarked("lineA")
    toggleMarked("lineB")
    clearMarked()
    expect(markedCount()).toBe(0)
    expect(getMarked()).toEqual([])
  })

  test("notifies listeners on change, not on no-op clears, and after unsubscribe", () => {
    let calls = 0
    const off = onMarkedChange(() => {
      calls += 1
    })
    toggleMarked("lineA") // add
    expect(calls).toBe(1)
    toggleMarked("lineA") // remove
    expect(calls).toBe(2)
    clearMarked() // set had one? no — it's empty now, so no-op
    expect(calls).toBe(2)
    toggleMarked("lineB")
    clearMarked() // had one: notifies
    expect(calls).toBe(4)
    off()
    toggleMarked("lineC")
    expect(calls).toBe(4)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx jest app/test/common/multi_delete_state.test.ts`
Expected: FAIL — `Cannot find module '../../src/common/multi_delete_state'`.

- [ ] **Step 3: Write minimal implementation**

Create `app/src/common/multi_delete_state.ts`:

```ts
import { IdType } from "../types/state"

/**
 * Transient, non-Redux set of line labels (polylines/polygons) the user has
 * Ctrl+clicked to mark for batch deletion. Mirrors the cut_state.ts /
 * segment_delete_state.ts tool-state pattern: plain module state plus a
 * listener list so the canvas can repaint when the set changes. The set is
 * scoped to the current item and is cleared on Escape, on Delete, and on item
 * navigation.
 */
const marked = new Set<IdType>()
const listeners = new Set<() => void>()

/** Notify all listeners of a change. */
function notify(): void {
  listeners.forEach((listener) => listener())
}

/**
 * Whether a label id is currently marked for deletion.
 *
 * @param labelId the label id
 */
export function isMarked(labelId: IdType): boolean {
  return marked.has(labelId)
}

/**
 * Toggle a label id in or out of the marked set. Always notifies.
 *
 * @param labelId the label id
 */
export function toggleMarked(labelId: IdType): void {
  if (marked.has(labelId)) {
    marked.delete(labelId)
  } else {
    marked.add(labelId)
  }
  notify()
}

/** A snapshot array of the currently marked ids. */
export function getMarked(): IdType[] {
  return [...marked]
}

/** How many ids are marked. */
export function markedCount(): number {
  return marked.size
}

/** Clear all marks. Notifies only when something was actually cleared. */
export function clearMarked(): void {
  if (marked.size === 0) {
    return
  }
  marked.clear()
  notify()
}

/**
 * Subscribe to marked-set changes.
 *
 * @param listener called after every change
 * @returns an unsubscribe function
 */
export function onMarkedChange(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx jest app/test/common/multi_delete_state.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/src/common/multi_delete_state.ts app/test/common/multi_delete_state.test.ts
git commit -m "feat: transient marked-set state for multi-line delete"
```

---

### Task 2: Batch-delete command

**Files:**
- Create: `app/src/drawable/2d/multi_delete.ts`
- Test: `app/test/drawable/multi_delete.test.ts`

**Interfaces:**
- Consumes: `getMarked`, `markedCount`, `clearMarked` from `app/src/common/multi_delete_state` (Task 1); `deleteLabels` from `app/src/action/common` (`deleteLabels(itemIndices: number[], labelIds: IdType[][])`, [common.ts:421](../../../app/src/action/common.ts#L421)); `drawHistory` + `LineSnapshot` from `app/src/common/draw_history` (`recordDeletedLine(itemIndex: number, labelId: IdType, snapshot: LineSnapshot)`, [draw_history.ts:145](../../../app/src/common/draw_history.ts#L145)); `getShapes` from `app/src/functional/state_util`; `Session`, `dispatch`, `getState` from `app/src/common/session`.
- Produces (used by Task 5): `commitMarkedDelete(): "deleted" | "ignored"`.

- [ ] **Step 1: Write the failing test**

Create `app/test/drawable/multi_delete.test.ts` (harness mirrors `polyline_segment_delete.test.ts`):

```ts
import * as action from "../../src/action/common"
import { drawHistory } from "../../src/common/draw_history"
import {
  clearMarked,
  getMarked,
  toggleMarked
} from "../../src/common/multi_delete_state"
import Session, { getState } from "../../src/common/session"
import { LabelTypeName } from "../../src/const/common"
import { commitMarkedDelete } from "../../src/drawable/2d/multi_delete"
import { makeLabel, makePathPoint2D } from "../../src/functional/states"
import { PathPointType } from "../../src/types/state"
import { initializeTestingObjects } from "./util"

/**
 * Seed a polyline with a fixed label id into item 0.
 *
 * @param labelId the label id
 * @param vertices [x, y] pairs (all plain LINE vertices)
 */
function seedLine(labelId: string, vertices: number[][]): void {
  const shapes = vertices.map(([x, y]) =>
    makePathPoint2D({ x, y, pointType: PathPointType.LINE, label: [labelId] })
  )
  const label = makeLabel(
    {
      id: labelId,
      item: 0,
      type: LabelTypeName.POLYLINE_2D,
      shapes: shapes.map((s) => s.id)
    },
    false
  )
  Session.dispatch(action.addLabel(0, label, shapes))
}

describe("multi delete", () => {
  beforeEach(() => {
    clearMarked()
  })

  test("deletes every marked line and clears the set", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [100, 0]
    ])
    seedLine("lineB", [
      [0, 50],
      [100, 50]
    ])

    toggleMarked("lineA")
    toggleMarked("lineB")
    expect(getMarked().sort()).toEqual(["lineA", "lineB"])

    expect(commitMarkedDelete()).toBe("deleted")
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(0)
    expect(getMarked()).toHaveLength(0)
  })

  test("undo restores the deleted lines one at a time", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [100, 0]
    ])
    seedLine("lineB", [
      [0, 50],
      [100, 50]
    ])
    toggleMarked("lineA")
    toggleMarked("lineB")

    expect(commitMarkedDelete()).toBe("deleted")
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(0)

    expect(drawHistory.undo()).toBe(true)
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(1)
    expect(drawHistory.undo()).toBe(true)
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(2)
  })

  test("ignores an empty set and leaves labels untouched", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [100, 0]
    ])
    expect(commitMarkedDelete()).toBe("ignored")
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(1)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx jest app/test/drawable/multi_delete.test.ts`
Expected: FAIL — `Cannot find module '../../src/drawable/2d/multi_delete'`.

- [ ] **Step 3: Write minimal implementation**

Create `app/src/drawable/2d/multi_delete.ts`:

```ts
import _ from "lodash"

import { deleteLabels } from "../../action/common"
import { drawHistory, LineSnapshot } from "../../common/draw_history"
import {
  clearMarked,
  getMarked,
  markedCount
} from "../../common/multi_delete_state"
import Session, { dispatch, getState } from "../../common/session"
import { LabelTypeName } from "../../const/common"
import { getShapes } from "../../functional/state_util"
import { PathPoint2DType } from "../../types/state"

/** Outcome of a batch-delete attempt. */
export type MarkedDeleteOutcome = "deleted" | "ignored"

/**
 * Delete every line currently marked for deletion in one atomic action.
 *
 * Only polylines and polygons in the current item are removed. Each is
 * snapshotted first via the existing per-line undo command, so undo restores
 * them one at a time (matching the annotator's existing multi-label delete).
 * The marked set is always cleared afterward. Returns "ignored" (and clears)
 * when nothing valid is marked, so callers can fall back to normal deletion.
 */
export function commitMarkedDelete(): MarkedDeleteOutcome {
  if (markedCount() === 0) {
    return "ignored"
  }
  const state = getState()
  const itemIndex = state.user.select.item
  const item = state.task.items[itemIndex]
  if (item === undefined) {
    clearMarked()
    return "ignored"
  }
  const deletable = getMarked().filter((id) => {
    const label = item.labels[id]
    return (
      label !== undefined &&
      (label.type === LabelTypeName.POLYLINE_2D ||
        label.type === LabelTypeName.POLYGON_2D)
    )
  })
  if (deletable.length === 0) {
    clearMarked()
    return "ignored"
  }
  // Snapshot each line before deletion so undo can restore it.
  for (const id of deletable) {
    const before: LineSnapshot = {
      label: _.cloneDeep(item.labels[id]),
      shapes: _.cloneDeep(getShapes(state, itemIndex, id) as PathPoint2DType[])
    }
    drawHistory.recordDeletedLine(itemIndex, id, before)
  }
  // Drop any stale selected drawable before the rebuild (mirrors segment delete).
  Session.label2dList.selectedLabels.length = 0
  dispatch(deleteLabels([itemIndex], [deletable]))
  clearMarked()
  return "deleted"
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx jest app/test/drawable/multi_delete.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/src/drawable/2d/multi_delete.ts app/test/drawable/multi_delete.test.ts
git commit -m "feat: commitMarkedDelete batch-deletes marked lines with undo"
```

---

### Task 3: Green highlight for marked lines

**Files:**
- Modify: `app/src/drawable/2d/polygon2d.ts` (imports near top; draw() at lines 407-410)

**Interfaces:**
- Consumes: `isMarked` from `app/src/common/multi_delete_state` (Task 1).
- Produces: none (visual only; verified end-to-end in Task 6).

> **Note on testing:** the 2D canvas stroke path has no unit-test harness in this
> codebase (rendering is validated at runtime — see Task 6). This task's gate is
> typecheck + lint; behavior is confirmed in Task 6.

- [ ] **Step 1: Add the import and color constant**

In `app/src/drawable/2d/polygon2d.ts`, add to the import group that already
imports from `../../common/...` (near the top of the file):

```ts
import { isMarked } from "../../common/multi_delete_state"
```

Then, next to the other module-level style constants (just below
`DEFAULT_CONTROL_HIGH_POINT_STYLE` at [polygon2d.ts:33](../../../app/src/drawable/2d/polygon2d.ts#L33)), add:

```ts
/** Stroke color for a line marked for batch deletion (segment-delete green). */
const MULTI_DELETE_HIGHLIGHT_COLOR = "rgba(0, 230, 0, 0.95)"
/** Extra stroke width multiplier for a marked line, for emphasis. */
const MULTI_DELETE_HIGHLIGHT_WIDTH_FACTOR = 1.5
```

- [ ] **Step 2: Override the stroke for marked lines (VIEW mode only)**

In `Polygon2D.draw()`, locate the line-stroke setup at
[polygon2d.ts:407-411](../../../app/src/drawable/2d/polygon2d.ts#L407-L411):

```ts
    // Draw line first
    edgeStyle.color = assignColor(0)
    context.save()
    context.strokeStyle = toCssColor(edgeStyle.color)
    context.lineWidth = edgeStyle.lineWidth
    context.beginPath()
```

Insert the override between the `context.lineWidth = ...` line and
`context.beginPath()`:

```ts
    // Draw line first
    edgeStyle.color = assignColor(0)
    context.save()
    context.strokeStyle = toCssColor(edgeStyle.color)
    context.lineWidth = edgeStyle.lineWidth
    // Lines Ctrl+clicked for batch deletion are stroked green (view canvas
    // only — never override the CONTROL canvas, whose color encodes hit ids).
    if (mode === DrawMode.VIEW && isMarked(this._labelId)) {
      context.strokeStyle = MULTI_DELETE_HIGHLIGHT_COLOR
      context.lineWidth = edgeStyle.lineWidth * MULTI_DELETE_HIGHLIGHT_WIDTH_FACTOR
    }
    context.beginPath()
```

- [ ] **Step 3: Typecheck and lint**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: no new errors.
Run: `npx eslint -c .eslintrc.json --ext .ts,.tsx app/src/drawable/2d/polygon2d.ts`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add app/src/drawable/2d/polygon2d.ts
git commit -m "feat: stroke marked lines green on the view canvas"
```

---

### Task 4: Ctrl+click marking, redraw subscription, Escape, and item-nav clear

**Files:**
- Modify: `app/src/components/label2d_canvas.tsx` (import line 39; field ~132; componentDidMount 179-193; componentWillUnmount 198-210; onMouseDown 487-489; onKeyDown ~855; updateState 901-908)

**Interfaces:**
- Consumes: `toggleMarked`, `clearMarked`, `markedCount`, `onMarkedChange` from `app/src/common/multi_delete_state` (Task 1); `LabelTypeName` from `app/src/const/common`; existing `this.redraw()` ([label2d_canvas.tsx:358](../../../app/src/components/label2d_canvas.tsx#L358)), `this._labelList.labelList` ([label2d_list.ts:179](../../../app/src/drawable/2d/label2d_list.ts#L179)), drawable `.type` / `.labelId` accessors ([label2d.ts:128](../../../app/src/drawable/2d/label2d.ts#L128), [label2d.ts:144](../../../app/src/drawable/2d/label2d.ts#L144)).
- Produces: none (UI wiring; verified in Task 6).

> **Note on testing:** canvas mouse/key wiring is not unit-tested in this
> codebase; gate is typecheck + lint, behavior confirmed end-to-end in Task 6.

- [ ] **Step 1: Add imports**

In `app/src/components/label2d_canvas.tsx`, change the const import at
[line 39](../../../app/src/components/label2d_canvas.tsx#L39):

```ts
import { Key } from "../const/common"
```

to:

```ts
import { Key, LabelTypeName } from "../const/common"
```

Add a new import (place it near the other `../common/...` imports, e.g. beside
the `onSegmentDeleteChange` import group around [line 24](../../../app/src/components/label2d_canvas.tsx#L24)):

```ts
import {
  clearMarked,
  markedCount,
  onMarkedChange,
  toggleMarked
} from "../common/multi_delete_state"
```

- [ ] **Step 2: Add the unsubscribe field**

Next to `private _offSegmentDelete: (() => void) | null = null` at
[line 132](../../../app/src/components/label2d_canvas.tsx#L132), add:

```ts
  private _offMarkedChange: (() => void) | null = null
```

- [ ] **Step 3: Subscribe on mount, unsubscribe on unmount**

In `componentDidMount`, right after the `_offSegmentDelete` assignment
([lines 190-192](../../../app/src/components/label2d_canvas.tsx#L190-L192)), add:

```ts
    this._offMarkedChange = onMarkedChange(() => this.redraw())
```

In `componentWillUnmount`, right after the existing `_offSegmentDelete` teardown
block ([lines 207-210](../../../app/src/components/label2d_canvas.tsx#L207-L210)), add:

```ts
    if (this._offMarkedChange !== null) {
      this._offMarkedChange()
      this._offMarkedChange = null
    }
```

- [ ] **Step 4: Mark on Ctrl+click, else pan**

Replace the pan guard at [lines 487-489](../../../app/src/components/label2d_canvas.tsx#L487-L489):

```ts
    if (e.ctrlKey || e.metaKey) {
      return
    }
```

with:

```ts
    if (e.ctrlKey || e.metaKey) {
      // Ctrl/Cmd+click directly on a polyline/polygon toggles it into the
      // batch-delete set; anywhere else (empty canvas, a box) falls through to
      // the pan behavior. Disabled for tracking tasks.
      if (labelIndex >= 0 && !this.state.task.config.tracking) {
        const drawable = this._labelList.labelList[labelIndex]
        if (
          drawable !== undefined &&
          (drawable.type === LabelTypeName.POLYLINE_2D ||
            drawable.type === LabelTypeName.POLYGON_2D)
        ) {
          toggleMarked(drawable.labelId)
          return
        }
      }
      return
    }
```

- [ ] **Step 5: Clear the set on Escape**

In `onKeyDown`, right after the cut-tool Escape branch that ends at
[line 855](../../../app/src/components/label2d_canvas.tsx#L855):

```ts
    if (e.key === Key.ESCAPE && isCutMode()) {
      // Escape disarms the one-shot cut tool.
      setCutMode(false)
      this.setDefaultCursor()
      return
    }
```

add:

```ts
    if (e.key === Key.ESCAPE && markedCount() > 0) {
      // Escape clears the batch-delete selection (redraw via the subscription).
      clearMarked()
      return
    }
```

- [ ] **Step 6: Clear the set on item navigation**

In `updateState`, inside the item-change block at
[lines 901-908](../../../app/src/components/label2d_canvas.tsx#L901-L908), add
`clearMarked()` beside the existing disarm calls:

```ts
    if (this._cutItemIndex !== state.user.select.item) {
      // Navigating to another image disarms the cut tools.
      if (this._cutItemIndex !== -1) {
        setCutMode(false)
        resetSegmentDelete()
        clearMarked()
      }
      this._cutItemIndex = state.user.select.item
    }
```

- [ ] **Step 7: Typecheck and lint**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: no new errors.
Run: `npx eslint -c .eslintrc.json --ext .ts,.tsx app/src/components/label2d_canvas.tsx`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add app/src/components/label2d_canvas.tsx
git commit -m "feat: Ctrl+click marks lines for deletion; clear on Escape/nav"
```

---

### Task 5: Route the Delete key through the batch delete

**Files:**
- Modify: `app/src/components/toolbar.tsx` (import group ~22-28; deletePressed 585-606)

**Interfaces:**
- Consumes: `commitMarkedDelete` from `app/src/drawable/2d/multi_delete` (Task 2).
- Produces: none (entry point).

> **Note on testing:** the delete logic is already unit-tested in Task 2; this
> task only wires the tested function into the Delete-key handler. Gate is
> typecheck + lint; end-to-end confirmation is Task 6.

- [ ] **Step 1: Add the import**

In `app/src/components/toolbar.tsx`, add near the existing drawable/common
imports (e.g. after the `drawHistory` import at [line 28](../../../app/src/components/toolbar.tsx#L28)):

```ts
import { commitMarkedDelete } from "../drawable/2d/multi_delete"
```

- [ ] **Step 2: Call commitMarkedDelete first in deletePressed**

Change the start of `deletePressed()` at [line 585](../../../app/src/components/toolbar.tsx#L585):

```ts
  private deletePressed(): void {
    const select = this.state.user.select
```

to:

```ts
  private deletePressed(): void {
    // Batch-delete any Ctrl+click-marked lines first; if none, fall through to
    // the normal selected-label deletion.
    if (commitMarkedDelete() === "deleted") {
      return
    }
    const select = this.state.user.select
```

- [ ] **Step 3: Typecheck and lint**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: no new errors.
Run: `npx eslint -c .eslintrc.json --ext .ts,.tsx app/src/components/toolbar.tsx`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add app/src/components/toolbar.tsx
git commit -m "feat: Delete key removes Ctrl+click-marked lines"
```

---

### Task 6: Full verification (unit + typecheck + lint + runtime)

**Files:** none (verification only).

- [ ] **Step 1: Run the full unit suite for the new/related modules**

Run: `npx jest app/test/common/multi_delete_state.test.ts app/test/drawable/multi_delete.test.ts app/test/drawable/polyline_segment_delete.test.ts app/test/drawable/draw_history.test.ts`
Expected: all PASS (segment-delete and draw_history included as a regression check on the shared undo plumbing).

- [ ] **Step 2: Typecheck and lint the whole project**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: no errors.
Run: `npm run lint`
Expected: clean.

- [ ] **Step 3: Runtime end-to-end verification**

Use the `verify` skill (drives the label page in headless Chrome over CDP) to confirm the full flow. Verify each:
  1. Draw two polylines. Ctrl+click each → **both render green** (`rgba(0,230,0,0.95)`), noticeably thicker.
  2. Press **Delete** → both lines are gone.
  3. Press **Ctrl+Z** twice → both lines are restored (one per undo).
  4. Ctrl+click a line to mark it, then Ctrl+click it again → it returns to its normal color (toggle off).
  5. Ctrl+click a line to mark it, press **Escape** → green clears, line remains.
  6. **Ctrl+drag on empty canvas still pans** the image (no regression).
  7. A **plain left-click** on a line still single-selects it in its normal color (no green, edit handles appear).
  8. Draw a **polygon**, Ctrl+click it → its outline turns green; Delete removes it.

Expected: all eight behaviors pass. If any fails, use `superpowers:systematic-debugging` before patching.

- [ ] **Step 4: Final commit (if any verification fixups were needed)**

```bash
git add -A
git commit -m "test: verify multi-select line delete end-to-end"
```

---

## Self-Review

**1. Spec coverage:**
- Ctrl+click marks polylines/polygons, empty/box pans → Task 4 Step 4. ✓
- Separate delete-set, green only for marked → Task 1 (set) + Task 3 (render). ✓
- Green color `rgba(0,230,0,0.95)` → Task 3 Step 1 constant. ✓
- Delete removes all marked, atomic, undo one-at-a-time → Task 2 + Task 5. ✓
- Escape clears → Task 4 Step 5. ✓ Item-nav clears → Task 4 Step 6. ✓
- VIEW-only override (hit-test safety) → Task 3 Step 2 guard. ✓
- Tracking tasks excluded → Task 4 Step 4 `!tracking` guard; Task 2 no-op when set empty. ✓
- Redraw on toggle → Task 4 Step 3 subscription. ✓
- Undo reuses `"deleted"` command → Task 2 uses `recordDeletedLine`. ✓
- Tests: unit for the set (Task 1), integration for delete+undo (Task 2), runtime for UI (Task 6). ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows full code; every command shows expected output. ✓

**3. Type consistency:** `isMarked`/`toggleMarked`/`getMarked`/`markedCount`/`clearMarked`/`onMarkedChange` are defined in Task 1 and consumed with the same names/signatures in Tasks 2–4. `commitMarkedDelete(): "deleted" | "ignored"` defined in Task 2, consumed in Task 5. `deleteLabels(number[], IdType[][])` and `recordDeletedLine(number, IdType, LineSnapshot)` match the verified source signatures. `drawable.type` / `drawable.labelId` match `Label2D` accessors. ✓

## Deferred (not in scope)

- Tracking-task support (marking is disabled there).
- Rubber-band / drag-rectangle selection.
- Single-undo-restores-all (currently one line per undo, matching existing multi-delete). A new `draw_history` command kind would be a small follow-up if desired.
