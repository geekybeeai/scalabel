# Freeform (Lasso) Selection for Polylines — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a freeform (lasso) select tool that marks every polyline/polygon inside or crossing a user-drawn region for the existing batch-delete flow.

**Architecture:** A lasso gesture (toolbar-armed or `Shift`+drag) accumulates a path in the image frame; on release, a pure geometry module finds the lines it hit and unions them into the existing `multi_delete_state.marked` set. The magenta highlight, `Delete`-key batch delete, undo, and Escape/nav clearing already key off that set, so almost no new delete/highlight code is needed.

**Tech Stack:** TypeScript, React 16 + Redux, HTML canvas, `@material-ui/core` v4 (icons v4 — no modern icon set), Jest.

## Global Constraints

- **Coordinates are always the original image frame.** Lasso points are captured via `getMousePos` (image coords) and compared against stored vertices; the overlay multiplies by `displayToImageRatio * _upResRatio` only when painting.
- **Reuse, don't rebuild:** marking a line = adding its id to `multi_delete_state`; deletion goes through the existing `commitMarkedDelete` + `Delete` key. Do not add new delete or highlight paths.
- **Selection semantics:** crossing (enclosed **or** touching). **Combine:** union (a lasso only ever *adds* marks).
- **Targets:** `POLYLINE_2D` / `POLYGON_2D` only; respect the same visibility filters the cut tool uses (`hideLabels` / `hiddenLabelTypes` / `hiddenCategories`).
- **No new `console.log`.** Remove the pre-existing `[DEBUG]` logs in the function being edited.
- Commit messages end with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

## Testing Strategy (read before starting)

Per `docs/polyline-feature-map.md` §8, this checkout runs **pure-logic** suites but **not** drawable/component suites (missing native `canvas` + `redis`).

- **Tasks 1–3** are pure modules → real TDD with a runnable local test using the node-env recipe:
  `npx jest <path> --env=node --globalSetup=app/test/setup/noop.js --globalTeardown=app/test/setup/noop.js`
  (run from the repo root `d:/Nikhil/Projects/GitHub/scalabel`).
- **Task 4** (orchestrator) gets a unit test mirroring the existing `app/test/drawable/multi_delete.test.ts`. That suite needs the store and **may not load locally** — that is expected. Its authoritative local gate is `npx tsc --noEmit`; its logic (the hit-test) is already proven by Task 1, and it is exercised end-to-end in Task 5.
- **Tasks 5–6** (canvas, toolbar) have no local unit harness → gate on `npx tsc --noEmit`, `npm run lint` (filter the pre-existing CRLF `prettier/prettier` noise), and a runtime check via the **`verify`** skill (headless Chrome/CDP).

All commands run from the repo root unless noted.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/src/drawable/2d/freeform_select_geometry.ts` | **New.** Pure lasso math: point-in-polygon, segment intersection, `lineHitsLasso`, `findLassoHits`. No Session/DOM. |
| `app/src/common/freeform_select_state.ts` | **New.** Transient tool state (armed / drawing / path buffer) + mutual exclusion with cut & delete-segment. |
| `app/src/drawable/2d/freeform_select.ts` | **New.** Impure orchestrator: read visible lines from state → `findLassoHits` → `markLabels`. |
| `app/src/common/multi_delete_state.ts` | **Modify.** Add `markLabels(ids)` union helper. |
| `app/src/components/cut_icon.tsx` | **Modify.** Add `FreeformSelectIcon` (inlined SVG). |
| `app/src/components/viewer2d.tsx` | **Modify.** Add `getFreeformSelectButton` + wire into the toolbar. |
| `app/src/components/label2d_canvas.tsx` | **Modify.** Gesture wiring, overlay, cursor, Escape, nav-clear, DEBUG cleanup. |
| `app/test/drawable/freeform_select_geometry.test.ts` | **New.** Pure geometry tests. |
| `app/test/common/multi_delete_state.test.ts` | **New.** `markLabels` tests. |
| `app/test/common/freeform_select_state.test.ts` | **New.** Tool-state + mutual-exclusion tests. |
| `app/test/drawable/freeform_select.test.ts` | **New.** Orchestrator test (CI; mirrors `multi_delete.test.ts`). |

---

## Task 1: Pure lasso geometry

**Files:**
- Create: `app/src/drawable/2d/freeform_select_geometry.ts`
- Test: `app/test/drawable/freeform_select_geometry.test.ts`

**Interfaces:**
- Consumes: `IdType` from `app/src/types/state`.
- Produces:
  - `interface Pt { x: number; y: number }`
  - `interface LassoLine { id: IdType; pts: Pt[]; closed: boolean }`
  - `pointInPolygon(pt: Pt, polygon: Pt[]): boolean`
  - `segmentsIntersect(a: Pt, b: Pt, c: Pt, d: Pt): boolean`
  - `lineHitsLasso(line: LassoLine, lasso: Pt[]): boolean`
  - `findLassoHits(lines: LassoLine[], lasso: Pt[]): IdType[]`

- [ ] **Step 1: Write the failing test**

Create `app/test/drawable/freeform_select_geometry.test.ts`:

```ts
import {
  findLassoHits,
  LassoLine,
  lineHitsLasso,
  pointInPolygon,
  Pt,
  segmentsIntersect
} from "../../src/drawable/2d/freeform_select_geometry"

// A 10x10 square lasso.
const SQUARE: Pt[] = [
  { x: 0, y: 0 },
  { x: 10, y: 0 },
  { x: 10, y: 10 },
  { x: 0, y: 10 }
]

describe("freeform_select_geometry", () => {
  describe("pointInPolygon", () => {
    test("inside", () => {
      expect(pointInPolygon({ x: 5, y: 5 }, SQUARE)).toBe(true)
    })
    test("outside", () => {
      expect(pointInPolygon({ x: 15, y: 5 }, SQUARE)).toBe(false)
    })
  })

  describe("segmentsIntersect", () => {
    test("crossing", () => {
      expect(
        segmentsIntersect(
          { x: 0, y: 0 },
          { x: 10, y: 10 },
          { x: 0, y: 10 },
          { x: 10, y: 0 }
        )
      ).toBe(true)
    })
    test("disjoint", () => {
      expect(
        segmentsIntersect(
          { x: 0, y: 0 },
          { x: 1, y: 0 },
          { x: 0, y: 5 },
          { x: 1, y: 5 }
        )
      ).toBe(false)
    })
  })

  describe("lineHitsLasso", () => {
    test("fully enclosed", () => {
      const line: LassoLine = {
        id: "a",
        pts: [
          { x: 2, y: 2 },
          { x: 8, y: 8 }
        ],
        closed: false
      }
      expect(lineHitsLasso(line, SQUARE)).toBe(true)
    })
    test("crossing with both endpoints outside", () => {
      const line: LassoLine = {
        id: "b",
        pts: [
          { x: -5, y: 5 },
          { x: 15, y: 5 }
        ],
        closed: false
      }
      expect(lineHitsLasso(line, SQUARE)).toBe(true)
    })
    test("fully outside", () => {
      const line: LassoLine = {
        id: "c",
        pts: [
          { x: 20, y: 20 },
          { x: 30, y: 30 }
        ],
        closed: false
      }
      expect(lineHitsLasso(line, SQUARE)).toBe(false)
    })
    test("single-point line inside", () => {
      const line: LassoLine = {
        id: "d",
        pts: [{ x: 5, y: 5 }],
        closed: false
      }
      expect(lineHitsLasso(line, SQUARE)).toBe(true)
    })
    test("degenerate lasso never hits", () => {
      const line: LassoLine = {
        id: "e",
        pts: [{ x: 5, y: 5 }],
        closed: false
      }
      expect(
        lineHitsLasso(line, [
          { x: 0, y: 0 },
          { x: 10, y: 0 }
        ])
      ).toBe(false)
    })
  })

  describe("findLassoHits", () => {
    test("returns only ids that hit", () => {
      const lines: LassoLine[] = [
        { id: "in", pts: [{ x: 5, y: 5 }], closed: false },
        { id: "out", pts: [{ x: 99, y: 99 }], closed: false }
      ]
      expect(findLassoHits(lines, SQUARE)).toEqual(["in"])
    })
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx jest app/test/drawable/freeform_select_geometry.test.ts --env=node --globalSetup=app/test/setup/noop.js --globalTeardown=app/test/setup/noop.js`
Expected: FAIL — `Cannot find module '../../src/drawable/2d/freeform_select_geometry'`.

- [ ] **Step 3: Write the implementation**

Create `app/src/drawable/2d/freeform_select_geometry.ts`:

```ts
import { IdType } from "../../types/state"

/** A 2D point in the image frame. */
export interface Pt {
  x: number
  y: number
}

/** A polyline/polygon reduced to its vertices for lasso hit-testing. */
export interface LassoLine {
  /** the label id */
  id: IdType
  /** the line's vertices in the image frame (order matters) */
  pts: Pt[]
  /** whether the line's own path closes (polygon) */
  closed: boolean
}

/**
 * Point-in-polygon test by ray casting (even-odd rule). A point exactly on an
 * edge is ambiguous, which is harmless here: a boundary-crossing line is also
 * caught by the edge test in lineHitsLasso.
 *
 * @param pt the query point
 * @param polygon the polygon vertices (implicitly closed)
 */
export function pointInPolygon(pt: Pt, polygon: Pt[]): boolean {
  let inside = false
  const n = polygon.length
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const a = polygon[i]
    const b = polygon[j]
    const crosses =
      a.y > pt.y !== b.y > pt.y &&
      pt.x < ((b.x - a.x) * (pt.y - a.y)) / (b.y - a.y) + a.x
    if (crosses) {
      inside = !inside
    }
  }
  return inside
}

/**
 * Orientation of the ordered triplet (p, q, r):
 * 0 collinear, 1 clockwise, 2 counter-clockwise.
 *
 * @param p first point
 * @param q second point
 * @param r third point
 */
function orientation(p: Pt, q: Pt, r: Pt): number {
  const val = (q.y - p.y) * (r.x - q.x) - (q.x - p.x) * (r.y - q.y)
  if (val === 0) {
    return 0
  }
  return val > 0 ? 1 : 2
}

/**
 * Whether q lies on segment pr, assuming p, q, r are collinear.
 *
 * @param p segment start
 * @param q query point
 * @param r segment end
 */
function onSegment(p: Pt, q: Pt, r: Pt): boolean {
  return (
    q.x <= Math.max(p.x, r.x) &&
    q.x >= Math.min(p.x, r.x) &&
    q.y <= Math.max(p.y, r.y) &&
    q.y >= Math.min(p.y, r.y)
  )
}

/**
 * Whether segment ab intersects segment cd (including collinear touching).
 *
 * @param a first endpoint of segment 1
 * @param b second endpoint of segment 1
 * @param c first endpoint of segment 2
 * @param d second endpoint of segment 2
 */
export function segmentsIntersect(a: Pt, b: Pt, c: Pt, d: Pt): boolean {
  const o1 = orientation(a, b, c)
  const o2 = orientation(a, b, d)
  const o3 = orientation(c, d, a)
  const o4 = orientation(c, d, b)
  if (o1 !== o2 && o3 !== o4) {
    return true
  }
  if (o1 === 0 && onSegment(a, c, b)) {
    return true
  }
  if (o2 === 0 && onSegment(a, d, b)) {
    return true
  }
  if (o3 === 0 && onSegment(c, a, d)) {
    return true
  }
  if (o4 === 0 && onSegment(c, b, d)) {
    return true
  }
  return false
}

/**
 * Crossing test: true if ANY line vertex is inside the lasso OR any line
 * segment crosses any lasso edge. Covers both "fully enclosed" and "crossing".
 *
 * @param line the candidate line
 * @param lasso the lasso polygon vertices (implicitly closed)
 */
export function lineHitsLasso(line: LassoLine, lasso: Pt[]): boolean {
  if (lasso.length < 3) {
    return false
  }
  for (const p of line.pts) {
    if (pointInPolygon(p, lasso)) {
      return true
    }
  }
  const segCount = line.closed ? line.pts.length : line.pts.length - 1
  for (let i = 0; i < segCount; i++) {
    const a = line.pts[i]
    const b = line.pts[(i + 1) % line.pts.length]
    for (let j = 0; j < lasso.length; j++) {
      const c = lasso[j]
      const d = lasso[(j + 1) % lasso.length]
      if (segmentsIntersect(a, b, c, d)) {
        return true
      }
    }
  }
  return false
}

/**
 * The ids of every line inside or crossing the lasso.
 *
 * @param lines the candidate lines
 * @param lasso the lasso polygon vertices
 */
export function findLassoHits(lines: LassoLine[], lasso: Pt[]): IdType[] {
  const hits: IdType[] = []
  for (const line of lines) {
    if (lineHitsLasso(line, lasso)) {
      hits.push(line.id)
    }
  }
  return hits
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx jest app/test/drawable/freeform_select_geometry.test.ts --env=node --globalSetup=app/test/setup/noop.js --globalTeardown=app/test/setup/noop.js`
Expected: PASS (all tests green).

- [ ] **Step 5: Commit**

```bash
git add app/src/drawable/2d/freeform_select_geometry.ts app/test/drawable/freeform_select_geometry.test.ts
git commit -m "feat: pure lasso hit-test geometry for freeform select"
```

---

## Task 2: `markLabels` union helper

**Files:**
- Modify: `app/src/common/multi_delete_state.ts`
- Test: `app/test/common/multi_delete_state.test.ts`

**Interfaces:**
- Consumes: existing `marked` set, `notify`, `IdType`.
- Produces: `markLabels(ids: IdType[]): void` — union-add, notifies once if anything was newly added.

- [ ] **Step 1: Write the failing test**

Create `app/test/common/multi_delete_state.test.ts`:

```ts
import {
  clearMarked,
  getMarked,
  isMarked,
  markedCount,
  markLabels,
  onMarkedChange,
  toggleMarked
} from "../../src/common/multi_delete_state"

describe("multi_delete_state markLabels", () => {
  beforeEach(() => {
    clearMarked()
  })

  test("adds ids as a union", () => {
    markLabels(["a", "b"])
    expect(isMarked("a")).toBe(true)
    expect(isMarked("b")).toBe(true)
    expect(markedCount()).toBe(2)
  })

  test("never removes an already-marked id", () => {
    toggleMarked("a")
    markLabels(["a", "b"])
    expect(getMarked().sort()).toEqual(["a", "b"])
  })

  test("notifies once when new ids are added", () => {
    let calls = 0
    const off = onMarkedChange(() => {
      calls++
    })
    markLabels(["a", "b"])
    expect(calls).toBe(1)
    off()
  })

  test("does not notify when all ids are already marked", () => {
    markLabels(["a"])
    let calls = 0
    const off = onMarkedChange(() => {
      calls++
    })
    markLabels(["a"])
    expect(calls).toBe(0)
    off()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx jest app/test/common/multi_delete_state.test.ts --env=node --globalSetup=app/test/setup/noop.js --globalTeardown=app/test/setup/noop.js`
Expected: FAIL — `markLabels` is not exported / not a function.

- [ ] **Step 3: Write the implementation**

In `app/src/common/multi_delete_state.ts`, add after `toggleMarked` (before `getMarked`):

```ts
/**
 * Add every id to the marked set (union — never removes). Notifies once if any
 * id was newly added. Used by the freeform lasso, which only ever adds.
 *
 * @param ids the ids to mark
 */
export function markLabels(ids: IdType[]): void {
  let changed = false
  for (const id of ids) {
    if (!marked.has(id)) {
      marked.add(id)
      changed = true
    }
  }
  if (changed) {
    notify()
  }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx jest app/test/common/multi_delete_state.test.ts --env=node --globalSetup=app/test/setup/noop.js --globalTeardown=app/test/setup/noop.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/common/multi_delete_state.ts app/test/common/multi_delete_state.test.ts
git commit -m "feat: markLabels union helper on the batch-delete set"
```

---

## Task 3: Freeform tool-state module

**Files:**
- Create: `app/src/common/freeform_select_state.ts`
- Test: `app/test/common/freeform_select_state.test.ts`

**Interfaces:**
- Consumes: `Pt` from `app/src/drawable/2d/freeform_select_geometry`; `cut_state` (`isCutMode`, `onCutModeChange`, `setCutMode`); `segment_delete_state` (`isSegmentDeleteActive`, `onSegmentDeleteChange`, `resetSegmentDelete`).
- Produces:
  - `isFreeformArmed(): boolean`
  - `isFreeformActive(): boolean` (armed || drawing)
  - `isFreeformDrawing(): boolean`
  - `armFreeform(): void`
  - `resetFreeform(): void`
  - `beginFreeformPath(pt: Pt): void`
  - `addFreeformPoint(pt: Pt): void`
  - `getFreeformPath(): Pt[]`
  - `endFreeformPath(): Pt[]`
  - `onFreeformChange(listener: () => void): () => void`

- [ ] **Step 1: Write the failing test**

Create `app/test/common/freeform_select_state.test.ts`:

```ts
import { isCutMode, setCutMode } from "../../src/common/cut_state"
import {
  addFreeformPoint,
  armFreeform,
  beginFreeformPath,
  endFreeformPath,
  getFreeformPath,
  isFreeformActive,
  isFreeformArmed,
  isFreeformDrawing,
  resetFreeform
} from "../../src/common/freeform_select_state"
import {
  armSegmentDelete,
  isSegmentDeleteActive,
  resetSegmentDelete
} from "../../src/common/segment_delete_state"

describe("freeform_select_state", () => {
  beforeEach(() => {
    resetFreeform()
    setCutMode(false)
    resetSegmentDelete()
  })

  test("arming sets armed and active", () => {
    expect(isFreeformArmed()).toBe(false)
    armFreeform()
    expect(isFreeformArmed()).toBe(true)
    expect(isFreeformActive()).toBe(true)
  })

  test("path lifecycle with merge-near", () => {
    beginFreeformPath({ x: 0, y: 0 })
    expect(isFreeformDrawing()).toBe(true)
    addFreeformPoint({ x: 10, y: 0 })
    addFreeformPoint({ x: 10.1, y: 0 }) // within merge distance -> dropped
    addFreeformPoint({ x: 20, y: 0 })
    expect(getFreeformPath()).toHaveLength(3)
    const finished = endFreeformPath()
    expect(finished).toHaveLength(3)
    expect(isFreeformDrawing()).toBe(false)
    expect(getFreeformPath()).toHaveLength(0)
  })

  test("end keeps armed (sticky mode)", () => {
    armFreeform()
    beginFreeformPath({ x: 0, y: 0 })
    endFreeformPath()
    expect(isFreeformArmed()).toBe(true)
  })

  test("reset clears everything", () => {
    armFreeform()
    beginFreeformPath({ x: 0, y: 0 })
    resetFreeform()
    expect(isFreeformArmed()).toBe(false)
    expect(isFreeformActive()).toBe(false)
    expect(getFreeformPath()).toHaveLength(0)
  })

  test("arming the cut tool disarms freeform", () => {
    armFreeform()
    setCutMode(true)
    expect(isFreeformArmed()).toBe(false)
  })

  test("arming freeform disarms the cut tool", () => {
    setCutMode(true)
    armFreeform()
    expect(isCutMode()).toBe(false)
  })

  test("arming delete-segment disarms freeform", () => {
    armFreeform()
    armSegmentDelete()
    expect(isFreeformArmed()).toBe(false)
  })

  test("arming freeform disarms delete-segment", () => {
    armSegmentDelete()
    armFreeform()
    expect(isSegmentDeleteActive()).toBe(false)
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx jest app/test/common/freeform_select_state.test.ts --env=node --globalSetup=app/test/setup/noop.js --globalTeardown=app/test/setup/noop.js`
Expected: FAIL — `Cannot find module '../../src/common/freeform_select_state'`.

- [ ] **Step 3: Write the implementation**

Create `app/src/common/freeform_select_state.ts`:

```ts
import { Pt } from "../drawable/2d/freeform_select_geometry"
import { isCutMode, onCutModeChange, setCutMode } from "./cut_state"
import {
  isSegmentDeleteActive,
  onSegmentDeleteChange,
  resetSegmentDelete
} from "./segment_delete_state"

/**
 * Transient, non-Redux state for the freeform (lasso) select tool. Mirrors
 * cut_state.ts / segment_delete_state.ts: plain module state plus a listener
 * list so the canvas repaints on change.
 *
 * `armed` is the sticky toolbar mode; `drawing` is true only between lasso
 * mouse-down and mouse-up; `path` holds the in-progress lasso vertices in the
 * image frame. Mutually exclusive with the cut and delete-segment tools.
 */

/** Squared image-px distance below which a new lasso point is merged away. */
const MERGE_DIST_SQ = 1

let armed = false
let drawing = false
let path: Pt[] = []

const listeners = new Set<() => void>()

/** Notify all listeners of a change. */
function notify(): void {
  listeners.forEach((listener) => listener())
}

/** Whether the sticky toolbar mode is on (drives the button color). */
export function isFreeformArmed(): boolean {
  return armed
}

/** Whether the tool owns the gesture/cursor (armed or mid-lasso). */
export function isFreeformActive(): boolean {
  return armed || drawing
}

/** Whether a lasso drag is currently in progress. */
export function isFreeformDrawing(): boolean {
  return drawing
}

/** Arm the sticky mode. Disarms the cut and delete-segment tools. */
export function armFreeform(): void {
  setCutMode(false)
  resetSegmentDelete()
  if (armed) {
    return
  }
  armed = true
  notify()
}

/** Disarm and clear any in-progress lasso. Safe to call when already off. */
export function resetFreeform(): void {
  if (!armed && !drawing && path.length === 0) {
    return
  }
  armed = false
  drawing = false
  path = []
  notify()
}

/**
 * Begin a lasso at the given image-frame point.
 *
 * @param pt the first lasso point (image frame)
 */
export function beginFreeformPath(pt: Pt): void {
  drawing = true
  path = [{ x: pt.x, y: pt.y }]
  notify()
}

/**
 * Append a point to the in-progress lasso, merging points closer than
 * MERGE_DIST_SQ to the last one to avoid zero-length segments.
 *
 * @param pt the next lasso point (image frame)
 */
export function addFreeformPoint(pt: Pt): void {
  if (!drawing) {
    return
  }
  const last = path[path.length - 1]
  if (last !== undefined) {
    const dx = pt.x - last.x
    const dy = pt.y - last.y
    if (dx * dx + dy * dy < MERGE_DIST_SQ) {
      return
    }
  }
  path.push({ x: pt.x, y: pt.y })
  notify()
}

/** The in-progress lasso vertices (image frame). */
export function getFreeformPath(): Pt[] {
  return path
}

/**
 * Finish the lasso: return its vertices and clear the in-progress path.
 * Leaves `armed` unchanged (sticky mode survives one lasso).
 */
export function endFreeformPath(): Pt[] {
  const finished = path
  drawing = false
  path = []
  notify()
  return finished
}

/**
 * Subscribe to freeform-tool changes.
 *
 * @param listener called after every change
 * @returns an unsubscribe function
 */
export function onFreeformChange(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

// Mutual exclusion: arming the cut or delete-segment tool cancels freeform.
onCutModeChange(() => {
  if (isCutMode()) {
    resetFreeform()
  }
})
onSegmentDeleteChange(() => {
  if (isSegmentDeleteActive()) {
    resetFreeform()
  }
})
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx jest app/test/common/freeform_select_state.test.ts --env=node --globalSetup=app/test/setup/noop.js --globalTeardown=app/test/setup/noop.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/common/freeform_select_state.ts app/test/common/freeform_select_state.test.ts
git commit -m "feat: freeform-select tool state with cut/segment mutual exclusion"
```

---

## Task 4: Freeform orchestrator

**Files:**
- Create: `app/src/drawable/2d/freeform_select.ts`
- Test: `app/test/drawable/freeform_select.test.ts`

**Interfaces:**
- Consumes: `getState` (Session), `getShapes`, `LabelTypeName`, `PathPoint2DType`, `findLassoHits`/`LassoLine`/`Pt`, `markLabels`.
- Produces:
  - `interface FreeformVisibilityFilter { hideLabels: boolean; hiddenLabelTypes: string[]; hiddenCategories: number[] }`
  - `runFreeformSelect(lasso: Pt[], visibility?: FreeformVisibilityFilter): number`

- [ ] **Step 1: Write the test** (CI-oriented; mirrors `multi_delete.test.ts`)

Create `app/test/drawable/freeform_select.test.ts`:

```ts
import * as action from "../../src/action/common"
import { clearMarked, getMarked } from "../../src/common/multi_delete_state"
import Session from "../../src/common/session"
import { LabelTypeName } from "../../src/const/common"
import { runFreeformSelect } from "../../src/drawable/2d/freeform_select"
import { makeLabel, makePathPoint2D } from "../../src/functional/states"
import { PathPointType } from "../../src/types/state"
import { initializeTestingObjects } from "./util"

/**
 * Seed a polyline with a fixed label id into item 0.
 *
 * @param labelId the label id
 * @param vertices [x, y] pairs (plain LINE vertices)
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

describe("freeform select orchestrator", () => {
  beforeEach(() => {
    clearMarked()
  })

  test("marks lines inside or crossing, ignores those outside", () => {
    initializeTestingObjects()
    seedLine("inside", [
      [10, 10],
      [20, 20]
    ])
    seedLine("outside", [
      [500, 500],
      [600, 600]
    ])
    const lasso = [
      { x: 0, y: 0 },
      { x: 50, y: 0 },
      { x: 50, y: 50 },
      { x: 0, y: 50 }
    ]
    const count = runFreeformSelect(lasso, {
      hideLabels: false,
      hiddenLabelTypes: [],
      hiddenCategories: []
    })
    expect(count).toBe(1)
    expect(getMarked()).toEqual(["inside"])
  })
})
```

- [ ] **Step 2: Run the test**

Run: `npx jest app/test/drawable/freeform_select.test.ts --env=node --globalSetup=app/test/setup/noop.js --globalTeardown=app/test/setup/noop.js`
Expected: FAIL — module not found. (If the drawable suite cannot load here due to missing native `canvas`/`redis`, that is the known environment limitation from the Testing Strategy; the `tsc` gate in Step 4 is authoritative locally.)

- [ ] **Step 3: Write the implementation**

Create `app/src/drawable/2d/freeform_select.ts`:

```ts
import { markLabels } from "../../common/multi_delete_state"
import { getState } from "../../common/session"
import { LabelTypeName } from "../../const/common"
import { getShapes } from "../../functional/state_util"
import { PathPoint2DType } from "../../types/state"
import { findLassoHits, LassoLine, Pt } from "./freeform_select_geometry"

/** Visibility filters the lasso scan must respect (mirrors CutVisibilityFilter). */
export interface FreeformVisibilityFilter {
  /** all labels hidden */
  hideLabels: boolean
  /** hidden label type names */
  hiddenLabelTypes: string[]
  /** hidden category indices */
  hiddenCategories: number[]
}

/**
 * Mark every visible polyline/polygon in the current item that is inside or
 * crosses the lasso, unioning them into the batch-delete set. Returns the
 * number of lines marked. A lasso of fewer than 3 points, a tracking task, or
 * an all-hidden view is a no-op.
 *
 * @param lasso the lasso vertices in the image frame
 * @param visibility optional viewer-config visibility filter
 */
export function runFreeformSelect(
  lasso: Pt[],
  visibility?: FreeformVisibilityFilter
): number {
  if (lasso.length < 3) {
    return 0
  }
  const state = getState()
  if (state.task.config.tracking) {
    return 0
  }
  if (visibility?.hideLabels === true) {
    return 0
  }
  const itemIndex = state.user.select.item
  const item = state.task.items[itemIndex]
  if (item === undefined) {
    return 0
  }
  const lines: LassoLine[] = []
  for (const labelId of Object.keys(item.labels)) {
    const label = item.labels[labelId]
    if (
      label.type !== LabelTypeName.POLYLINE_2D &&
      label.type !== LabelTypeName.POLYGON_2D
    ) {
      continue
    }
    if (visibility?.hiddenLabelTypes.includes(label.type) === true) {
      continue
    }
    if (visibility?.hiddenCategories.includes(label.category[0]) === true) {
      continue
    }
    const stored = getShapes(state, itemIndex, labelId) as PathPoint2DType[]
    if (stored.length === 0) {
      continue
    }
    lines.push({
      id: labelId,
      pts: stored.map((p) => ({ x: p.x, y: p.y })),
      closed:
        label.type === LabelTypeName.POLYGON_2D || label.closed === true
    })
  }
  const hits = findLassoHits(lines, lasso)
  if (hits.length > 0) {
    markLabels(hits)
  }
  return hits.length
}
```

- [ ] **Step 4: Type-check (authoritative local gate)**

Run: `npx tsc --noEmit`
Expected: no new errors referencing `freeform_select.ts`.

- [ ] **Step 5: Commit**

```bash
git add app/src/drawable/2d/freeform_select.ts app/test/drawable/freeform_select.test.ts
git commit -m "feat: freeform-select orchestrator marks lasso-hit lines"
```

---

## Task 5: Canvas gesture wiring, overlay, and DEBUG cleanup

Enables the full flow via `Shift`+drag (no toolbar needed yet).

**Files:**
- Modify: `app/src/components/label2d_canvas.tsx`

**Interfaces:**
- Consumes: Task 3 (`beginFreeformPath`, `addFreeformPoint`, `endFreeformPath`, `getFreeformPath`, `isFreeformActive`, `isFreeformDrawing`, `onFreeformChange`, `resetFreeform`), Task 4 (`runFreeformSelect`).

- [ ] **Step 1: Add imports**

After the `multi_delete_state` import block (currently lines 27–32), add:

```ts
import {
  addFreeformPoint,
  beginFreeformPath,
  endFreeformPath,
  getFreeformPath,
  isFreeformActive,
  isFreeformDrawing,
  onFreeformChange,
  resetFreeform
} from "../common/freeform_select_state"
import { runFreeformSelect } from "../drawable/2d/freeform_select"
```

- [ ] **Step 2: Add the subscription field + lifecycle**

Add the field beside `_offMarkedChange` (currently line 141):

```ts
  /** unsubscribe from freeform-select tool changes */
  private _offFreeformChange: (() => void) | null = null
```

In `componentDidMount`, after the `this._offMarkedChange = onMarkedChange(...)` block, add:

```ts
    this._offFreeformChange = onFreeformChange(() => {
      this.redraw()
    })
```

In `componentWillUnmount`, after the `_offMarkedChange` teardown block, add:

```ts
    if (this._offFreeformChange !== null) {
      this._offFreeformChange()
      this._offFreeformChange = null
    }
```

- [ ] **Step 3: Start the lasso in `onMouseDown`**

In `onMouseDown`, immediately after `resetPanState()` (currently line 500) and **before** the `if (e.ctrlKey || e.metaKey)` branch, insert:

```ts
    // Freeform (lasso) select: armed via the toolbar, or ad-hoc via Shift+drag.
    // Begins the lasso here so the empty-space pan-arming below never runs.
    // Ctrl/Meta are excluded so Ctrl+click-mark and Ctrl-pan keep working.
    const ffConfig = this.state.user.viewerConfigs[
      this.props.id
    ] as ImageViewerConfigType
    const freeformAllowed =
      !this._labelList.isDrawingInProgress() &&
      !this.state.task.config.tracking &&
      ffConfig?.showCurvesOnly !== true
    if (
      (isFreeformActive() || e.shiftKey) &&
      !e.ctrlKey &&
      !e.metaKey &&
      freeformAllowed
    ) {
      beginFreeformPath(mousePos)
      this.setCursor("crosshair")
      return
    }
```

- [ ] **Step 4: Accumulate points in `onMouseMove`**

In `onMouseMove`, after the crosshair block (currently lines 682–684) and **before** `if (isArmed())`, insert:

```ts
    if (isFreeformDrawing()) {
      addFreeformPoint(this.getMousePos(e))
      this.setCursor("crosshair")
      return
    }
```

Then, in the cursor-override block at the end of `onMouseMove` (currently lines 715–718), after the cut/segment cursor `if`, add:

```ts
    if (isFreeformActive()) {
      this.setCursor("crosshair")
    }
```

- [ ] **Step 5: Finalize the selection in `onMouseUp`**

In `onMouseUp`, after the `e.button !== 0 || this.checkFreeze()` guard (currently lines 646–648) and **before** `if (isArmed())`, insert:

```ts
    if (isFreeformDrawing()) {
      const path = endFreeformPath()
      if (path.length >= 3) {
        const config = this.state.user.viewerConfigs[this.props.id]
        runFreeformSelect(path, {
          hideLabels: config.hideLabels,
          hiddenLabelTypes:
            config.hiddenLabelTypes !== undefined
              ? config.hiddenLabelTypes
              : [],
          hiddenCategories:
            config.hiddenCategories !== undefined
              ? config.hiddenCategories
              : []
        })
      }
      this.setDefaultCursor()
      this._labelList.onDrawableUpdate()
      return
    }
```

- [ ] **Step 6: Escape disarms the tool**

In `onKeyDown`, after the `markedCount() > 0` Escape block (currently lines 910–914), add:

```ts
    if (e.key === Key.ESCAPE && isFreeformActive()) {
      // Escape disarms the freeform tool and drops any in-progress lasso.
      resetFreeform()
      this.setDefaultCursor()
      return
    }
```

- [ ] **Step 7: Clear on item navigation**

In `updateState`, inside the item-change block, after `clearMarked()` (currently line 965), add:

```ts
        resetFreeform()
```

- [ ] **Step 8: Draw the lasso overlay**

Add a method after `drawSegmentDeleteOverlay` (currently ends line 884):

```ts
  /**
   * Draw the in-progress freeform lasso: the accumulated path as a dashed
   * magenta polyline, closed back to its start. Drawn on top of the labels
   * like the delete-segment overlay.
   *
   * @param context the label canvas context
   * @param ratio image-to-canvas scale (displayToImageRatio * upResRatio)
   */
  private drawFreeformOverlay(
    context: CanvasRenderingContext2D,
    ratio: number
  ): void {
    const path = getFreeformPath()
    if (path.length < 2) {
      return
    }
    context.save()
    context.beginPath()
    context.strokeStyle = DELETE_HIGHLIGHT_COLOR
    context.lineWidth = 2
    context.setLineDash(DASH_LINE)
    context.lineDashOffset = -getAntsOffset()
    context.moveTo(path[0].x * ratio, path[0].y * ratio)
    for (let i = 1; i < path.length; i++) {
      context.lineTo(path[i].x * ratio, path[i].y * ratio)
    }
    context.closePath()
    context.stroke()
    context.restore()
  }
```

Then, in `redraw`, right after the `this.drawSegmentDeleteOverlay(...)` call (currently lines 444–447), add:

```ts
      this.drawFreeformOverlay(
        this.labelContext,
        this.displayToImageRatio * this._upResRatio
      )
```

- [ ] **Step 9: Remove the leftover DEBUG logs**

In `onMouseDown`, delete the hit-testing debug log (currently lines 492–499):

```ts
    console.log("[DEBUG] Canvas.onMouseDown hit testing:", {
      mousePos,
      labelIndex,
      handleIndex,
      inPanWindow: inPanWindow(Date.now()),
      hasSelectedLabels: this._labelHandler["hasSelectedLabels"](),
      isEditingSelectedLabels: this._labelHandler["isEditingSelectedLabels"]()
    })
```

And delete the rejection debug log (currently line 626):

```ts
      console.log("[DEBUG] Canvas.onMouseDown REJECTED: labelIndex < 0 or inPanWindow", { labelIndex })
```

- [ ] **Step 10: Type-check and lint**

Run: `npx tsc --noEmit`
Expected: no new errors.

Run: `npx eslint -c .eslintrc.json app/src/components/label2d_canvas.tsx`
Expected: no new non-`prettier/prettier` violations vs HEAD (CRLF prettier noise is pre-existing — ignore it).

- [ ] **Step 11: Runtime verification**

Use the `verify` skill to drive the label page in headless Chrome. Confirm:
1. Draw two polylines. Hold `Shift` and drag a loop around one → it turns magenta (marching ants); the other stays normal.
2. Press `Delete` → the marked line is removed; the other remains.
3. `Ctrl+Z` → the deleted line is restored.
4. `Shift`+drag crossing part of a line (endpoints outside) → it still gets marked (crossing).
5. `Shift`+drag a second region → previously-marked lines stay marked (union).
6. Press `Escape` → all magenta marks clear.
7. Plain left-drag on empty space still pans (no Shift).

- [ ] **Step 12: Commit**

```bash
git add app/src/components/label2d_canvas.tsx
git commit -m "feat: freeform lasso gesture wiring, overlay, and DEBUG cleanup"
```

---

## Task 6: Freeform toolbar button + icon

**Files:**
- Modify: `app/src/components/cut_icon.tsx`
- Modify: `app/src/components/viewer2d.tsx`

**Interfaces:**
- Consumes: Task 3 (`armFreeform`, `isFreeformArmed`, `resetFreeform`).
- Produces: `FreeformSelectIcon(props: SvgIconProps): JSX.Element`; `getFreeformSelectButton(): JSX.Element` on `Viewer2D`.

- [ ] **Step 1: Add the icon**

In `app/src/components/cut_icon.tsx`, append:

```tsx
/**
 * Lasso icon for the freeform-select tool: a dashed open loop with a small
 * tail. Drawn with strokes (fill="none") so it reads as a marquee/lasso.
 *
 * @param props standard SvgIcon props (fontSize, style, ...)
 */
export function FreeformSelectIcon(props: SvgIconProps): JSX.Element {
  return (
    <SvgIcon {...props}>
      <path
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeDasharray="3 2"
        strokeLinecap="round"
        d="M12 4c4.5 0 8 2.9 8 6.5S16.5 17 12 17c-3 0-5.6-1-6.6-2.9"
      />
      <path d="M5 13.2l-1.9 4.1 4.1-1.2z" />
    </SvgIcon>
  )
}
```

- [ ] **Step 2: Add imports to viewer2d.tsx**

Update the `cut_icon` import (currently line 51) to include the new icon:

```ts
import {
  ContentCutIcon,
  DeleteSegmentIcon,
  FreeformSelectIcon
} from "./cut_icon"
```

After the `segment_delete_state` import block (currently lines 19–24), add:

```ts
import {
  armFreeform,
  isFreeformArmed,
  resetFreeform
} from "../common/freeform_select_state"
```

- [ ] **Step 3: Add the button builder**

In `app/src/components/viewer2d.tsx`, add a method after `getDeleteSegmentButton` (mirrors the cut button, with the same guards):

```tsx
  /**
   * Build the freeform (lasso) select toolbar button. Arms a sticky mode that
   * lassos polylines/polygons into the batch-delete set; clicking while armed
   * disarms. Mutually exclusive with the cut and delete-segment tools.
   *
   * @return {JSX.Element} the freeform-select button
   */
  protected getFreeformSelectButton(): JSX.Element {
    const armed = isFreeformArmed()
    return (
      <Tooltip
        key={`freeformSelect2dButton${this.props.id}`}
        title="Freeform select"
        enterDelay={500}
        TransitionComponent={Fade}
        TransitionProps={{ timeout: 600 }}
        arrow
      >
        <IconButton
          onClick={() => {
            if (armed) {
              resetFreeform()
            } else if (
              !Session.label2dList.isDrawingInProgress() &&
              !this.state.task.config.tracking &&
              (this._viewerConfig as ImageViewerConfigType)?.showCurvesOnly !==
                true
            ) {
              armFreeform()
            }
          }}
          className={this.props.classes.viewer_button}
          style={{ color: armed ? "#4caf50" : undefined }}
          edge={"start"}
        >
          <FreeformSelectIcon />
        </IconButton>
      </Tooltip>
    )
  }
```

- [ ] **Step 4: Add the button to the toolbar**

In `getMenuComponents`, extend the returned array (currently ends with `this.getCutButton(), this.getDeleteSegmentButton()` at lines 309–310):

```ts
        this.getCutButton(),
        this.getDeleteSegmentButton(),
        this.getFreeformSelectButton()
```

- [ ] **Step 5: Type-check and lint**

Run: `npx tsc --noEmit`
Expected: no new errors.

Run: `npx eslint -c .eslintrc.json app/src/components/viewer2d.tsx app/src/components/cut_icon.tsx`
Expected: no new non-`prettier/prettier` violations vs HEAD.

- [ ] **Step 6: Runtime verification**

Use the `verify` skill. Confirm:
1. A lasso button appears in the 2D toolbar beside the scissors and delete-segment buttons.
2. Clicking it turns it green (armed); the cursor is a crosshair over the canvas.
3. While armed, a plain left-drag (no Shift) lassos and marks lines magenta.
4. Clicking the scissors (cut) or delete-segment button disarms the lasso button (green clears), and vice-versa.
5. Clicking the lasso button again disarms it.

- [ ] **Step 7: Commit**

```bash
git add app/src/components/cut_icon.tsx app/src/components/viewer2d.tsx
git commit -m "feat: freeform-select toolbar button and lasso icon"
```

---

## Self-Review

**1. Spec coverage** (against `docs/superpowers/specs/2026-07-10-freeform-select-polylines-design.md`):

| Spec item | Task |
|---|---|
| Pure crossing hit-test (enclosed or touching) | Task 1 |
| Union combine (`markLabels`) | Task 2 |
| Tool state + Shift-free mutual exclusion | Task 3 |
| Orchestrator (visible polylines/polygons → hits → mark) | Task 4 |
| `Shift`+drag shortcut, pan coexistence, overlay, cursor, Escape, nav-clear, DEBUG cleanup | Task 5 |
| Toolbar button (sticky, guarded, mutually exclusive) + icon | Task 6 |
| Magenta highlight / Delete / undo reuse | Reused unchanged (verified in Task 5 Step 11) |

No spec requirement is left without a task.

**2. Placeholder scan:** No "TBD"/"TODO"/"handle edge cases" placeholders; every code step shows complete code. The one honest environment caveat (Task 4 Step 2) is backed by the authoritative `tsc` gate in Step 4.

**3. Type consistency:** `Pt` / `LassoLine` (Task 1) are consumed unchanged by Tasks 3–4. `markLabels(ids: IdType[])` (Task 2) is called by `runFreeformSelect` (Task 4). `runFreeformSelect(lasso, visibility)` (Task 4) is called with the exact `{ hideLabels, hiddenLabelTypes, hiddenCategories }` shape in Task 5 Step 5. `isFreeformActive` / `isFreeformDrawing` / `getFreeformPath` / `beginFreeformPath` / `addFreeformPoint` / `endFreeformPath` / `resetFreeform` / `onFreeformChange` (Task 3) match their call sites in Task 5. `isFreeformArmed` / `armFreeform` / `resetFreeform` (Task 3) match Task 6. `FreeformSelectIcon` (Task 6 Step 1) matches its import and use (Steps 2–3). Consistent.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-10-freeform-select-polylines.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
