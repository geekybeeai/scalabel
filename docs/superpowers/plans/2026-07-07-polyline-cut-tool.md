# Polyline Cut (Scissor) Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a one-shot scissor tool (toolbar button + right-click menu) that splits an open polyline into two polylines at a clicked point, with atomic single-press undo.

**Architecture:** A module-level mode singleton (`cut_state.ts`, mirroring `pointer_pan_state.ts`) arms the tool; `Label2dCanvas.onMouseDown` consumes the armed click and calls `performCut`, which scans every open polyline in the current item from redux, finds the globally nearest straight-segment cut site (pure geometry in `polyline_cut_geometry.ts`), replaces the original label wholesale (delete+add, same id — the `drawHistory.setLine` pattern) and adds the second half as a new label (the `pasteLabel` pattern). A new `"cut"` command kind in `draw_history.ts` makes undo atomic.

**Tech Stack:** TypeScript + React 16 class components, redux (custom store in `common/session.ts`), Material-UI v4 (`@material-ui/core`), jest 26 (ts-jest).

**Spec:** `docs/superpowers/specs/2026-07-07-polyline-cut-tool-design.md` — read it first.

## Global Constraints

- **No new npm dependencies.** The scissors icon is an inline `SvgIcon` path; the cursor is an SVG data-URI.
- **Coordinates golden rule:** annotation coordinates are ALWAYS stored in the ORIGINAL image pixel frame. All cut math runs in image coordinates; screen-px thresholds are converted by dividing by `displayToImageRatio`.
- **Code style:** no semicolons, double quotes, JSDoc block on every exported/public symbol (with `@param` lines) — match the surrounding files exactly.
- **Lint noise caveat:** `npm run lint` has pervasive pre-existing CRLF `prettier/prettier` errors on Windows checkouts. Ignore those; only compare a changed file's **non-prettier** rule violations against HEAD.
- **Test environment caveat:** drawable/component test suites need the native `canvas` module + `redis-server`, usually missing locally — they may fail to LOAD. Pure-logic tests run with `--env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js`. Harness-based tests (Tasks 4–5) are written for CI; if they fail to load locally with a canvas/jsdom error, verify via `npx tsc --noEmit` and note it — do not fight the environment.
- **Behavioral invariants (from spec):** one-shot (disarms only after a *successful* cut; rejections keep it armed), atomic single-press undo, straight segments only, open polylines only, empty-space click swallowed while armed.
- Work on the current branch `feature-opimization`. Commit after every task with the trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Test-run infrastructure + `cut_state.ts` mode singleton

**Files:**
- Create: `app/test/setup/noop.js`
- Create: `app/src/common/cut_state.ts`
- Test: `app/test/common/cut_state.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `isCutMode(): boolean`, `setCutMode(on: boolean): void`, `onCutModeChange(listener: () => void): () => void` (returns unsubscribe). Tasks 6–8 import these from `"../common/cut_state"` / `"../../common/cut_state"`.

- [ ] **Step 1: Create the noop jest global setup/teardown**

The repo's default `globalSetup` spawns a redis server that is not installed locally. This noop replaces it for targeted local runs (used by every test step in this plan).

Create `app/test/setup/noop.js`:

```js
// Noop global setup/teardown for running pure-logic suites locally without
// the redis server that the default global_setup.ts spawns.
module.exports = async () => {}
```

- [ ] **Step 2: Write the failing test**

Create `app/test/common/cut_state.test.ts`:

```ts
import {
  isCutMode,
  onCutModeChange,
  setCutMode
} from "../../src/common/cut_state"

describe("cut_state", () => {
  beforeEach(() => {
    setCutMode(false)
  })

  test("arms and disarms", () => {
    expect(isCutMode()).toBe(false)
    setCutMode(true)
    expect(isCutMode()).toBe(true)
    setCutMode(false)
    expect(isCutMode()).toBe(false)
  })

  test("notifies listeners only on change", () => {
    let calls = 0
    const off = onCutModeChange(() => {
      calls += 1
    })
    setCutMode(true)
    expect(calls).toBe(1)
    setCutMode(true) // same value: no notification
    expect(calls).toBe(1)
    setCutMode(false)
    expect(calls).toBe(2)
    off()
    setCutMode(true)
    expect(calls).toBe(2)
  })
})
```

- [ ] **Step 3: Run test to verify it fails**

Run:
```bash
npx jest app/test/common/cut_state.test.ts --env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js --coverage=false
```
Expected: FAIL — `Cannot find module '../../src/common/cut_state'`.

- [ ] **Step 4: Write the implementation**

Create `app/src/common/cut_state.ts`:

```ts
/**
 * Transient, non-Redux state for the one-shot polyline cut (scissor) tool.
 *
 * Armed from the Viewer2D toolbar button or the Label2dCanvas context menu;
 * consumed by Label2dCanvas, which performs the cut on the next left-click.
 * Mirrors the pointer_pan_state.ts pattern, plus a minimal listener list so
 * the toolbar button can re-render when the mode is cleared from elsewhere
 * (Escape, a successful one-shot cut, item navigation).
 */
let cutMode = false

const listeners = new Set<() => void>()

/** Whether the cut tool is currently armed. */
export function isCutMode(): boolean {
  return cutMode
}

/**
 * Arm or disarm the cut tool. Notifies listeners only on an actual change.
 *
 * @param on whether the tool should be armed
 */
export function setCutMode(on: boolean): void {
  if (cutMode === on) {
    return
  }
  cutMode = on
  listeners.forEach((listener) => listener())
}

/**
 * Subscribe to cut-mode changes.
 *
 * @param listener called after every change
 * @returns an unsubscribe function
 */
export function onCutModeChange(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
```

- [ ] **Step 5: Run test to verify it passes**

Same command as Step 3. Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add app/test/setup/noop.js app/src/common/cut_state.ts app/test/common/cut_state.test.ts
git commit -m "feat: cut-tool mode singleton + noop jest setup for local pure tests

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Pure geometry — `findCutSite`

**Files:**
- Create: `app/src/drawable/2d/polyline_cut_geometry.ts`
- Test: `app/test/drawable/polyline_cut_geometry.test.ts`

**Interfaces:**
- Consumes: `PathPointType`, `SimplePathPoint2DType` from `app/src/types/state` (`SimplePathPoint2DType` = `{ x: number; y: number; pointType: PathPointType }`).
- Produces (Tasks 3 and 5 rely on these exact shapes):

```ts
export interface CutSite {
  segmentIndex: number
  point: { x: number; y: number }
  snappedVertexIndex: number | null
  distance: number
}
export type CutSiteResult =
  | { kind: "site"; site: CutSite }
  | { kind: "curve"; distance: number }
  | { kind: "near-endpoint"; distance: number }
  | { kind: "miss" }
export function findCutSite(
  points: readonly SimplePathPoint2DType[],
  click: { x: number; y: number },
  radius: number,
  snapRadius: number
): CutSiteResult
```

**IMPORTANT:** this file must import ONLY from `app/src/types/state` — no Session, no DOM — so it stays testable under `--env=node`.

- [ ] **Step 1: Write the failing tests**

Create `app/test/drawable/polyline_cut_geometry.test.ts`:

```ts
import { findCutSite } from "../../src/drawable/2d/polyline_cut_geometry"
import { PathPointType, SimplePathPoint2DType } from "../../src/types/state"

/**
 * Shorthand: build a path point.
 *
 * @param x x coordinate
 * @param y y coordinate
 * @param pointType point type (defaults to LINE)
 */
function pt(
  x: number,
  y: number,
  pointType: PathPointType = PathPointType.LINE
): SimplePathPoint2DType {
  return { x, y, pointType }
}

describe("findCutSite", () => {
  const RADIUS = 10
  const SNAP = 8

  test("projects a mid-segment click onto the segment", () => {
    const points = [pt(0, 0), pt(100, 0)]
    const result = findCutSite(points, { x: 50, y: 5 }, RADIUS, SNAP)
    expect(result.kind).toBe("site")
    if (result.kind === "site") {
      expect(result.site.segmentIndex).toBe(0)
      expect(result.site.point).toEqual({ x: 50, y: 0 })
      expect(result.site.snappedVertexIndex).toBeNull()
      expect(result.site.distance).toBeCloseTo(5)
    }
  })

  test("misses when the click is outside the radius", () => {
    const points = [pt(0, 0), pt(100, 0)]
    expect(findCutSite(points, { x: 50, y: 30 }, RADIUS, SNAP).kind).toBe(
      "miss"
    )
  })

  test("misses on fewer than 2 points", () => {
    expect(findCutSite([pt(0, 0)], { x: 0, y: 0 }, RADIUS, SNAP).kind).toBe(
      "miss"
    )
    expect(findCutSite([], { x: 0, y: 0 }, RADIUS, SNAP).kind).toBe("miss")
  })

  test("snaps to a nearby interior vertex", () => {
    const points = [pt(0, 0), pt(100, 0), pt(200, 0)]
    const result = findCutSite(points, { x: 103, y: 4 }, RADIUS, SNAP)
    expect(result.kind).toBe("site")
    if (result.kind === "site") {
      expect(result.site.snappedVertexIndex).toBe(1)
      expect(result.site.point).toEqual({ x: 100, y: 0 })
    }
  })

  test("rejects a cut too close to an endpoint", () => {
    const points = [pt(0, 0), pt(100, 0)]
    const result = findCutSite(points, { x: 2, y: 3 }, RADIUS, SNAP)
    expect(result.kind).toBe("near-endpoint")
  })

  test("rejects a click nearest to a bezier span", () => {
    const points = [
      pt(0, 0),
      pt(30, 10, PathPointType.CURVE),
      pt(60, 10, PathPointType.CURVE),
      pt(100, 0)
    ]
    const result = findCutSite(points, { x: 45, y: 12 }, RADIUS, SNAP)
    expect(result.kind).toBe("curve")
  })

  test("cuts a straight span of a partially curved polyline", () => {
    // Straight span (100,0)-(200,0) after a bezier group.
    const points = [
      pt(0, 0),
      pt(30, 10, PathPointType.CURVE),
      pt(60, 10, PathPointType.CURVE),
      pt(100, 0),
      pt(200, 0)
    ]
    const result = findCutSite(points, { x: 150, y: 4 }, RADIUS, SNAP)
    expect(result.kind).toBe("site")
    if (result.kind === "site") {
      expect(result.site.segmentIndex).toBe(3)
      expect(result.site.point).toEqual({ x: 150, y: 0 })
    }
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
npx jest app/test/drawable/polyline_cut_geometry.test.ts --env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js --coverage=false
```
Expected: FAIL — `Cannot find module '../../src/drawable/2d/polyline_cut_geometry'`.

- [ ] **Step 3: Write the implementation**

Create `app/src/drawable/2d/polyline_cut_geometry.ts`:

```ts
import { PathPointType, SimplePathPoint2DType } from "../../types/state"

/** A validated location to cut a polyline. */
export interface CutSite {
  /** Segment start index: the cut lies on points[i] -> points[i + 1] */
  segmentIndex: number
  /** Cut coordinate (original image frame) */
  point: { x: number; y: number }
  /** When the cut snapped to an existing interior vertex, its index */
  snappedVertexIndex: number | null
  /** Click-to-polyline distance (image px), comparable across polylines */
  distance: number
}

/** Result of searching one polyline for a cut site. */
export type CutSiteResult =
  | { kind: "site"; site: CutSite }
  | { kind: "curve"; distance: number }
  | { kind: "near-endpoint"; distance: number }
  | { kind: "miss" }

/** Both halves of a cut polyline, as plain (id-less) path points. */
export interface CutHalves {
  /** points[0..i] plus the cut point (unless vertex-snapped) */
  first: SimplePathPoint2DType[]
  /** the cut point (unless vertex-snapped) plus points[i + 1..] */
  second: SimplePathPoint2DType[]
}

interface SegmentProjection {
  /** distance from the query point to the segment */
  dist: number
  /** closest point x */
  x: number
  /** closest point y */
  y: number
}

/**
 * Project a point onto a segment, clamped to the segment's extent.
 *
 * @param px query point x
 * @param py query point y
 * @param ax segment start x
 * @param ay segment start y
 * @param bx segment end x
 * @param by segment end y
 */
function projectOntoSegment(
  px: number,
  py: number,
  ax: number,
  ay: number,
  bx: number,
  by: number
): SegmentProjection {
  const dx = bx - ax
  const dy = by - ay
  const lenSq = dx * dx + dy * dy
  let t = 0
  if (lenSq > 0) {
    t = ((px - ax) * dx + (py - ay) * dy) / lenSq
    t = Math.max(0, Math.min(1, t))
  }
  const x = ax + t * dx
  const y = ay + t * dy
  return { dist: Math.hypot(px - x, py - y), x, y }
}

/**
 * Find where a click would cut a polyline.
 *
 * Measures the click against EVERY span (bezier control spans included, so a
 * click that genuinely lands on a curved part is reported as "curve" instead
 * of cutting a farther straight segment), takes the nearest span, and
 * validates it: within `radius`, both span ends plain LINE vertices, and not
 * within `snapRadius` of the polyline's first/last vertex (which would create
 * a zero-length stub). A cut within `snapRadius` of an interior vertex snaps
 * exactly to that vertex.
 *
 * All inputs are in original-image coordinates.
 *
 * @param points the polyline's stored vertices (never contains MID points)
 * @param click the click position
 * @param radius max click-to-line distance for a cut (image px)
 * @param snapRadius vertex snap / endpoint-guard distance (image px)
 */
export function findCutSite(
  points: readonly SimplePathPoint2DType[],
  click: { x: number; y: number },
  radius: number,
  snapRadius: number
): CutSiteResult {
  if (points.length < 2) {
    return { kind: "miss" }
  }
  let bestIndex = -1
  let best: SegmentProjection | null = null
  for (let i = 0; i < points.length - 1; i++) {
    const proj = projectOntoSegment(
      click.x,
      click.y,
      points[i].x,
      points[i].y,
      points[i + 1].x,
      points[i + 1].y
    )
    if (best === null || proj.dist < best.dist) {
      best = proj
      bestIndex = i
    }
  }
  if (best === null || best.dist > radius) {
    return { kind: "miss" }
  }
  // A span is only cuttable when both ends are plain LINE vertices; CURVE
  // (or any other) point types mark bezier control spans.
  if (
    points[bestIndex].pointType !== PathPointType.LINE ||
    points[bestIndex + 1].pointType !== PathPointType.LINE
  ) {
    return { kind: "curve", distance: best.dist }
  }
  // Snap to a nearby vertex so cuts never create hair-thin sliver segments.
  const dStart = Math.hypot(
    best.x - points[bestIndex].x,
    best.y - points[bestIndex].y
  )
  const dEnd = Math.hypot(
    best.x - points[bestIndex + 1].x,
    best.y - points[bestIndex + 1].y
  )
  let snapped: number | null = null
  if (dStart <= snapRadius && dStart <= dEnd) {
    snapped = bestIndex
  } else if (dEnd <= snapRadius) {
    snapped = bestIndex + 1
  }
  if (snapped !== null && (snapped === 0 || snapped === points.length - 1)) {
    return { kind: "near-endpoint", distance: best.dist }
  }
  const point =
    snapped !== null
      ? { x: points[snapped].x, y: points[snapped].y }
      : { x: best.x, y: best.y }
  return {
    kind: "site",
    site: {
      segmentIndex: bestIndex,
      point,
      snappedVertexIndex: snapped,
      distance: best.dist
    }
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Same command as Step 2. Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add app/src/drawable/2d/polyline_cut_geometry.ts app/test/drawable/polyline_cut_geometry.test.ts
git commit -m "feat: pure cut-site geometry for the polyline scissor tool

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Pure geometry — `buildCutHalves`

**Files:**
- Modify: `app/src/drawable/2d/polyline_cut_geometry.ts` (append)
- Test: `app/test/drawable/polyline_cut_geometry.test.ts` (append)

**Interfaces:**
- Consumes: `CutSite`, `CutHalves` from Task 2 (same file).
- Produces: `buildCutHalves(points: readonly SimplePathPoint2DType[], site: CutSite): CutHalves` — Task 5 relies on this exact signature.

- [ ] **Step 1: Write the failing tests**

Append to `app/test/drawable/polyline_cut_geometry.test.ts` (add `buildCutHalves` to the existing import from `polyline_cut_geometry` — the site literals below need no `CutSite` import; TypeScript infers them structurally):

```ts
describe("buildCutHalves", () => {
  test("splits mid-segment, both halves sharing the cut coordinate", () => {
    const points = [pt(0, 0), pt(100, 0), pt(200, 0)]
    const site = {
      segmentIndex: 0,
      point: { x: 50, y: 0 },
      snappedVertexIndex: null,
      distance: 0
    }
    const { first, second } = buildCutHalves(points, site)
    expect(first).toEqual([pt(0, 0), pt(50, 0)])
    expect(second).toEqual([pt(50, 0), pt(100, 0), pt(200, 0)])
    // Input is not mutated
    expect(points).toHaveLength(3)
  })

  test("splits at a snapped interior vertex without duplicating it in a half", () => {
    const points = [pt(0, 0), pt(100, 0), pt(200, 0)]
    const site = {
      segmentIndex: 0,
      point: { x: 100, y: 0 },
      snappedVertexIndex: 1,
      distance: 0
    }
    const { first, second } = buildCutHalves(points, site)
    expect(first).toEqual([pt(0, 0), pt(100, 0)])
    expect(second).toEqual([pt(100, 0), pt(200, 0)])
  })

  test("preserves bezier spans away from the cut", () => {
    const points = [
      pt(0, 0),
      pt(30, 10, PathPointType.CURVE),
      pt(60, 10, PathPointType.CURVE),
      pt(100, 0),
      pt(200, 0)
    ]
    const site = {
      segmentIndex: 3,
      point: { x: 150, y: 0 },
      snappedVertexIndex: null,
      distance: 0
    }
    const { first, second } = buildCutHalves(points, site)
    expect(first).toEqual([
      pt(0, 0),
      pt(30, 10, PathPointType.CURVE),
      pt(60, 10, PathPointType.CURVE),
      pt(100, 0),
      pt(150, 0)
    ])
    expect(second).toEqual([pt(150, 0), pt(200, 0)])
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
npx jest app/test/drawable/polyline_cut_geometry.test.ts --env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js --coverage=false
```
Expected: FAIL — `buildCutHalves` is not exported.

- [ ] **Step 3: Write the implementation**

Append to `app/src/drawable/2d/polyline_cut_geometry.ts`:

```ts
/**
 * Build the two halves of a cut polyline.
 *
 * Mid-segment cut: the first half ends with a new vertex at the cut point and
 * the second half starts with its own vertex at the same coordinate
 * (coincident, no gap). Vertex-snapped cut: no new coordinate is introduced
 * within a half — the halves share only the snapped vertex's coordinate.
 * Returned points are fresh copies; the input is never mutated.
 *
 * @param points the polyline's stored vertices
 * @param site a "site" result from findCutSite for these points
 */
export function buildCutHalves(
  points: readonly SimplePathPoint2DType[],
  site: CutSite
): CutHalves {
  const copy = (p: SimplePathPoint2DType): SimplePathPoint2DType => ({
    x: p.x,
    y: p.y,
    pointType: p.pointType
  })
  if (site.snappedVertexIndex !== null) {
    const j = site.snappedVertexIndex
    return {
      first: points.slice(0, j + 1).map(copy),
      second: points.slice(j).map(copy)
    }
  }
  const i = site.segmentIndex
  const cutPoint: SimplePathPoint2DType = {
    x: site.point.x,
    y: site.point.y,
    pointType: PathPointType.LINE
  }
  return {
    first: [...points.slice(0, i + 1).map(copy), { ...cutPoint }],
    second: [{ ...cutPoint }, ...points.slice(i + 1).map(copy)]
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Same command as Step 2. Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add app/src/drawable/2d/polyline_cut_geometry.ts app/test/drawable/polyline_cut_geometry.test.ts
git commit -m "feat: buildCutHalves splits polyline vertices at a cut site

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Atomic `"cut"` command in DrawHistory

**Files:**
- Modify: `app/src/common/draw_history.ts`
- Test: `app/test/drawable/draw_history_cut.test.ts`

**Interfaces:**
- Consumes: existing `LineSnapshot`, `setLine`, `removeLine` in `draw_history.ts`.
- Produces: `drawHistory.recordCut(itemIndex: number, labelId: IdType, before: LineSnapshot, after: LineSnapshot, newLine: LineSnapshot): void` — Task 5 calls this.

- [ ] **Step 1: Write the failing test**

Create `app/test/drawable/draw_history_cut.test.ts`:

```ts
import _ from "lodash"

import * as action from "../../src/action/common"
import { drawHistory, LineSnapshot } from "../../src/common/draw_history"
import Session, { getState } from "../../src/common/session"
import { LabelTypeName } from "../../src/const/common"
import { getShapes } from "../../src/functional/state_util"
import { makeLabel, makePathPoint2D } from "../../src/functional/states"
import { PathPoint2DType, PathPointType } from "../../src/types/state"
import { initializeTestingObjects } from "./util"

/**
 * Seed a polyline with a fixed label id into item 0.
 *
 * @param labelId the label id to use
 * @param vertices [x, y] pairs
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

/**
 * Snapshot a line from the current state.
 *
 * @param labelId the label id to snapshot
 */
function snapshot(labelId: string): LineSnapshot {
  const state = getState()
  return {
    label: _.cloneDeep(state.task.items[0].labels[labelId]),
    shapes: _.cloneDeep(getShapes(state, 0, labelId))
  }
}

describe("DrawHistory cut command", () => {
  test("one undo restores the original; one redo re-applies the cut", () => {
    initializeTestingObjects()
    drawHistory.reset()

    seedLine("lineA", [
      [0, 0],
      [100, 0],
      [200, 0]
    ])
    const before = snapshot("lineA")

    // Apply the cut by hand (exactly what performCut dispatches):
    // truncate A to its first half, add B as the second half.
    const shapesA = [
      makePathPoint2D({
        x: 0,
        y: 0,
        pointType: PathPointType.LINE,
        label: ["lineA"]
      }),
      makePathPoint2D({
        x: 50,
        y: 0,
        pointType: PathPointType.LINE,
        label: ["lineA"]
      })
    ]
    const labelA = _.cloneDeep(before.label)
    labelA.shapes = shapesA.map((s) => s.id)
    const shapesB = [
      makePathPoint2D({
        x: 50,
        y: 0,
        pointType: PathPointType.LINE,
        label: ["lineB"]
      }),
      makePathPoint2D({
        x: 100,
        y: 0,
        pointType: PathPointType.LINE,
        label: ["lineB"]
      }),
      makePathPoint2D({
        x: 200,
        y: 0,
        pointType: PathPointType.LINE,
        label: ["lineB"]
      })
    ]
    const labelB = _.cloneDeep(before.label)
    labelB.id = "lineB"
    labelB.shapes = shapesB.map((s) => s.id)
    Session.dispatch(
      action.makeSequential([
        action.deleteLabel(0, "lineA"),
        action.addLabel(0, labelA, shapesA),
        action.addLabel(0, labelB, shapesB)
      ])
    )
    drawHistory.recordCut(
      0,
      "lineA",
      before,
      snapshot("lineA"),
      snapshot("lineB")
    )

    expect(getShapes(getState(), 0, "lineA")).toHaveLength(2)
    expect(getShapes(getState(), 0, "lineB")).toHaveLength(3)

    // One undo: B gone, A back to its 3 original vertices.
    expect(drawHistory.undo()).toBe(true)
    expect(getState().task.items[0].labels.lineB).toBeUndefined()
    const restored = getShapes(getState(), 0, "lineA") as PathPoint2DType[]
    expect(restored).toHaveLength(3)
    expect(restored.map((p) => [p.x, p.y])).toEqual([
      [0, 0],
      [100, 0],
      [200, 0]
    ])
    expect(drawHistory.canRedo()).toBe(true)

    // One redo: the cut is back.
    expect(drawHistory.redo()).toBe(true)
    expect(getShapes(getState(), 0, "lineA")).toHaveLength(2)
    expect(getShapes(getState(), 0, "lineB")).toHaveLength(3)
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
npx jest app/test/drawable/draw_history_cut.test.ts --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js --coverage=false
```
Expected: FAIL — `recordCut` does not exist. **Environment caveat:** if the suite instead fails to LOAD with a native-canvas/jsdom error, this suite can only run in CI; proceed using `npx tsc --noEmit` as the per-step check and note this in the commit message.

- [ ] **Step 3: Write the implementation**

In `app/src/common/draw_history.ts`, make four edits:

3a. Extend the kind union (currently `type CommandKind = "created" | "deleted" | "edited"`):

```ts
/** Kind of recorded user action on a polyline. */
type CommandKind = "created" | "deleted" | "edited" | "cut"
```

3b. Extend the `Command` interface — add one field after `after`:

```ts
  /** "cut": the new second-half polyline created by the cut. */
  newLine?: LineSnapshot
```

Also extend the doc comment above `Command` with:

```ts
 * - "cut": the user cut a line in two. Undo removes the new half and restores
 * the original; redo re-truncates the original and re-adds the new half.
```

3c. Add `recordCut` after `recordEdit`:

```ts
  /**
   * Record that the user cut a polyline in two, as ONE atomic undo step.
   * Any pending redo is invalidated.
   *
   * @param itemIndex the item the polyline belongs to
   * @param labelId the original polyline's label id (kept by the first half)
   * @param before the original geometry before the cut (undo target)
   * @param after the truncated original after the cut (redo target)
   * @param newLine the new second-half polyline (id inside its label)
   */
  public recordCut(
    itemIndex: number,
    labelId: IdType,
    before: LineSnapshot,
    after: LineSnapshot,
    newLine: LineSnapshot
  ): void {
    this._undoStack.push({
      kind: "cut",
      itemIndex,
      labelId,
      before,
      after,
      newLine
    })
    this._redoStack = []
  }
```

3d. Add a `"cut"` branch in BOTH `undo()` and `redo()`, between the `"deleted"` branch and the final `else` (the final else handles `"edited"` and must stay last):

In `undo()`:

```ts
      } else if (command.kind === "cut") {
        // Undo a cut = remove the new second half, restore the original line.
        if (command.before === undefined || command.newLine === undefined) {
          continue
        }
        this.removeLine(command.itemIndex, command.newLine.label.id)
        this.setLine(command.itemIndex, command.before)
        this._redoStack.push(command)
        return true
```

In `redo()`:

```ts
      } else if (command.kind === "cut") {
        // Redo a cut = re-truncate the original, re-add the second half.
        if (command.after === undefined || command.newLine === undefined) {
          continue
        }
        this.setLine(command.itemIndex, command.after)
        this.setLine(command.itemIndex, command.newLine)
        this._undoStack.push(command)
        return true
```

`canUndo()` needs no change: `"cut"` is not `"created"`, so it is always considered actionable — correct, because undo of a cut always acts.

- [ ] **Step 4: Run the test to verify it passes**

Same command as Step 2. Expected: PASS (or, if the suite cannot load locally, `npx tsc --noEmit` exits 0).

- [ ] **Step 5: Commit**

```bash
git add app/src/common/draw_history.ts app/test/drawable/draw_history_cut.test.ts
git commit -m "feat: atomic 'cut' command kind in polyline draw history

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: `performCut` — scan, split, dispatch, record

**Files:**
- Create: `app/src/drawable/2d/polyline_cut.ts`
- Test: `app/test/drawable/polyline_cut.test.ts`

**Interfaces:**
- Consumes: `findCutSite`, `buildCutHalves`, `CutSite` (Task 2/3); `drawHistory.recordCut` (Task 4); `addLabel`, `deleteLabel`, `makeSequential` from `app/src/action/common`; `selectLabels` from `app/src/action/select`; `getShapes` from `app/src/functional/state_util`; `makePathPoint2D` from `app/src/functional/states`; `uid` from `app/src/common/uid`; `Session, { dispatch, getState }` from `app/src/common/session`.
- Produces (Task 7 relies on these):

```ts
export const CUT_CLICK_RADIUS_PX = 20 // screen px
export const CUT_SNAP_RADIUS_PX = 8 // screen px
export type CutResult = "cut" | "miss" | "curve" | "near-endpoint" | "closed"
export function performCut(
  click: { x: number; y: number },
  radius: number,
  snapRadius: number
): CutResult
```

(`click`, `radius`, `snapRadius` are in IMAGE pixels; the caller converts the exported screen-px constants by dividing by `displayToImageRatio`.)

- [ ] **Step 1: Write the failing tests**

Create `app/test/drawable/polyline_cut.test.ts`:

```ts
import * as action from "../../src/action/common"
import { drawHistory } from "../../src/common/draw_history"
import Session, { getState } from "../../src/common/session"
import { LabelTypeName } from "../../src/const/common"
import { performCut } from "../../src/drawable/2d/polyline_cut"
import { getShapes } from "../../src/functional/state_util"
import { makeLabel, makePathPoint2D } from "../../src/functional/states"
import { PathPoint2DType, PathPointType } from "../../src/types/state"
import { initializeTestingObjects } from "./util"

/**
 * Seed a polyline/polygon with a fixed label id into item 0.
 *
 * @param labelId the label id to use
 * @param vertices [x, y] pairs (all plain LINE vertices)
 * @param labelType label type name
 */
function seedLine(
  labelId: string,
  vertices: number[][],
  labelType: string = LabelTypeName.POLYLINE_2D
): void {
  const shapes = vertices.map(([x, y]) =>
    makePathPoint2D({ x, y, pointType: PathPointType.LINE, label: [labelId] })
  )
  const label = makeLabel(
    {
      id: labelId,
      item: 0,
      type: labelType,
      shapes: shapes.map((s) => s.id)
    },
    false
  )
  Session.dispatch(action.addLabel(0, label, shapes))
}

describe("performCut", () => {
  test("cuts the nearest open polyline into two, undo restores it", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [100, 0],
      [200, 0]
    ])

    expect(performCut({ x: 50, y: 3 }, 20, 8)).toBe("cut")

    const state = getState()
    const ids = Object.keys(state.task.items[0].labels)
    expect(ids).toHaveLength(2)
    const halfA = getShapes(state, 0, "lineA") as PathPoint2DType[]
    expect(halfA.map((p) => [p.x, p.y])).toEqual([
      [0, 0],
      [50, 0]
    ])
    const newId = ids.filter((id) => id !== "lineA")[0]
    const halfB = getShapes(state, 0, newId) as PathPoint2DType[]
    expect(halfB.map((p) => [p.x, p.y])).toEqual([
      [50, 0],
      [100, 0],
      [200, 0]
    ])
    // Category/type inherited
    expect(state.task.items[0].labels[newId].type).toBe(
      LabelTypeName.POLYLINE_2D
    )
    expect(state.task.items[0].labels[newId].manual).toBe(true)

    // Atomic undo
    expect(drawHistory.undo()).toBe(true)
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(1)
    expect(getShapes(getState(), 0, "lineA")).toHaveLength(3)
  })

  test("returns miss when nothing is within the radius", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [100, 0]
    ])
    expect(performCut({ x: 50, y: 500 }, 20, 8)).toBe("miss")
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(1)
  })

  test("returns near-endpoint next to a line end", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [100, 0]
    ])
    expect(performCut({ x: 2, y: 3 }, 20, 8)).toBe("near-endpoint")
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(1)
  })

  test("returns closed when the nearest shape is a polygon", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine(
      "polyA",
      [
        [0, 0],
        [100, 0],
        [100, 100],
        [0, 100]
      ],
      LabelTypeName.POLYGON_2D
    )
    expect(performCut({ x: 50, y: 3 }, 20, 8)).toBe("closed")
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(1)
  })

  test("returns curve when the nearest span is bezier", () => {
    initializeTestingObjects()
    drawHistory.reset()
    const shapes = [
      makePathPoint2D({
        x: 0,
        y: 0,
        pointType: PathPointType.LINE,
        label: ["curvy"]
      }),
      makePathPoint2D({
        x: 30,
        y: 10,
        pointType: PathPointType.CURVE,
        label: ["curvy"]
      }),
      makePathPoint2D({
        x: 60,
        y: 10,
        pointType: PathPointType.CURVE,
        label: ["curvy"]
      }),
      makePathPoint2D({
        x: 100,
        y: 0,
        pointType: PathPointType.LINE,
        label: ["curvy"]
      })
    ]
    const label = makeLabel(
      {
        id: "curvy",
        item: 0,
        type: LabelTypeName.POLYLINE_2D,
        shapes: shapes.map((s) => s.id)
      },
      false
    )
    Session.dispatch(action.addLabel(0, label, shapes))
    expect(performCut({ x: 45, y: 12 }, 20, 8)).toBe("curve")
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(1)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
npx jest app/test/drawable/polyline_cut.test.ts --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js --coverage=false
```
Expected: FAIL — `Cannot find module '../../src/drawable/2d/polyline_cut'`. Same environment caveat as Task 4 Step 2 (fall back to `npx tsc --noEmit` if the suite cannot load locally).

- [ ] **Step 3: Write the implementation**

Create `app/src/drawable/2d/polyline_cut.ts`:

```ts
import _ from "lodash"

import { addLabel, deleteLabel, makeSequential } from "../../action/common"
import { selectLabels } from "../../action/select"
import { drawHistory, LineSnapshot } from "../../common/draw_history"
import Session, { dispatch, getState } from "../../common/session"
import { uid } from "../../common/uid"
import { LabelTypeName } from "../../const/common"
import { getShapes } from "../../functional/state_util"
import { makePathPoint2D } from "../../functional/states"
import {
  IdType,
  LabelType,
  PathPoint2DType,
  SimplePathPoint2DType,
  State
} from "../../types/state"
import {
  buildCutHalves,
  CutSite,
  findCutSite
} from "./polyline_cut_geometry"

/** Screen-space search radius for the cut click (display px). */
export const CUT_CLICK_RADIUS_PX = 20
/** Screen-space vertex-snap / endpoint-guard radius (display px). */
export const CUT_SNAP_RADIUS_PX = 8

/** Outcome of a cut attempt, mapped to user feedback by the caller. */
export type CutResult = "cut" | "miss" | "curve" | "near-endpoint" | "closed"

interface Candidate {
  /** the polyline's label id */
  labelId: IdType
  /** where it would be cut */
  site: CutSite
}

/**
 * Read a label's stored path points as plain (id-less) points.
 *
 * @param state the current state
 * @param itemIndex the item index
 * @param labelId the label id
 */
function storedPoints(
  state: State,
  itemIndex: number,
  labelId: IdType
): SimplePathPoint2DType[] {
  const stored = getShapes(state, itemIndex, labelId) as PathPoint2DType[]
  return stored.map((p) => ({ x: p.x, y: p.y, pointType: p.pointType }))
}

/**
 * Try to cut the open polyline nearest to a click.
 *
 * Scans EVERY polyline/polygon in the current item from redux (no dependency
 * on the control-canvas hit-test, so thin lines are easy to hit), finds the
 * globally nearest cut site within `radius`, and applies it. Closed shapes
 * are scanned only so a click nearest to one reports "closed" instead of
 * silently missing. All parameters are in ORIGINAL-IMAGE pixels.
 *
 * @param click the click position (image frame)
 * @param radius max click-to-line distance (image px)
 * @param snapRadius vertex snap / endpoint-guard distance (image px)
 */
export function performCut(
  click: { x: number; y: number },
  radius: number,
  snapRadius: number
): CutResult {
  const state = getState()
  if (state.task.config.tracking) {
    return "miss"
  }
  const itemIndex = state.user.select.item
  const item = state.task.items[itemIndex]
  if (item === undefined) {
    return "miss"
  }

  let best: Candidate | null = null
  let bestRejection: { kind: CutResult; distance: number } | null = null

  for (const labelId of Object.keys(item.labels)) {
    const label = item.labels[labelId]
    if (
      label.type !== LabelTypeName.POLYLINE_2D &&
      label.type !== LabelTypeName.POLYGON_2D
    ) {
      continue
    }
    const isOpen =
      label.type === LabelTypeName.POLYLINE_2D && label.closed !== true
    const points = storedPoints(state, itemIndex, labelId)
    if (points.length < 2) {
      continue
    }
    // Closed shapes participate only for the "closed" rejection message;
    // include their closing edge so clicks on it are attributed to them.
    const scanPoints = isOpen ? points : [...points, points[0]]
    const result = findCutSite(scanPoints, click, radius, snapRadius)
    if (result.kind === "miss") {
      continue
    }
    if (!isOpen) {
      const distance =
        result.kind === "site" ? result.site.distance : result.distance
      if (bestRejection === null || distance < bestRejection.distance) {
        bestRejection = { kind: "closed", distance }
      }
      continue
    }
    if (result.kind === "site") {
      if (best === null || result.site.distance < best.site.distance) {
        best = { labelId, site: result.site }
      }
    } else {
      if (bestRejection === null || result.distance < bestRejection.distance) {
        bestRejection = { kind: result.kind, distance: result.distance }
      }
    }
  }

  if (best === null) {
    return bestRejection !== null ? bestRejection.kind : "miss"
  }
  // If a rejection is strictly nearer than the best cuttable site, the user
  // most likely clicked the rejected thing — report it instead of cutting.
  if (bestRejection !== null && bestRejection.distance < best.site.distance) {
    return bestRejection.kind
  }
  return commitCut(itemIndex, best)
}

/**
 * Apply a validated cut: replace the original with its first half (same
 * label id — the drawHistory.setLine wholesale-replacement pattern), add the
 * second half as a new label (the pasteLabel pattern), and record ONE atomic
 * undo command.
 *
 * @param itemIndex the item being edited
 * @param candidate the polyline and site to cut
 */
function commitCut(itemIndex: number, candidate: Candidate): CutResult {
  const state = getState()
  const { labelId, site } = candidate
  const label = state.task.items[itemIndex].labels[labelId]
  const stored = getShapes(state, itemIndex, labelId) as PathPoint2DType[]

  const before: LineSnapshot = {
    label: _.cloneDeep(label),
    shapes: _.cloneDeep(stored)
  }

  const halves = buildCutHalves(
    stored.map((p) => ({ x: p.x, y: p.y, pointType: p.pointType })),
    site
  )

  // First half keeps the original label id.
  const shapesA = halves.first.map((p) =>
    makePathPoint2D({
      x: p.x,
      y: p.y,
      pointType: p.pointType,
      label: [labelId]
    })
  )
  const labelA: LabelType = _.cloneDeep(label)
  labelA.shapes = shapesA.map((s) => s.id)

  // Second half is a brand-new polyline inheriting category/attributes.
  const newLabelId = uid()
  const shapesB = halves.second.map((p) =>
    makePathPoint2D({
      x: p.x,
      y: p.y,
      pointType: p.pointType,
      label: [newLabelId]
    })
  )
  const labelB: LabelType = _.cloneDeep(label)
  labelB.id = newLabelId
  labelB.item = itemIndex
  labelB.track = ""
  labelB.parent = ""
  labelB.children = []
  labelB.shapes = shapesB.map((s) => s.id)
  labelB.manual = true

  // Deselect so no stale selected drawable survives the rebuild.
  dispatch(
    selectLabels(
      {},
      -1,
      [],
      state.user.select.category,
      state.user.select.attributes
    )
  )
  Session.label2dList.selectedLabels.length = 0

  dispatch(
    makeSequential([
      deleteLabel(itemIndex, labelId),
      addLabel(itemIndex, labelA, shapesA),
      addLabel(itemIndex, labelB, shapesB)
    ])
  )

  const committed = getState()
  const after: LineSnapshot = {
    label: _.cloneDeep(committed.task.items[itemIndex].labels[labelId]),
    shapes: _.cloneDeep(getShapes(committed, itemIndex, labelId))
  }
  const newLine: LineSnapshot = {
    label: _.cloneDeep(committed.task.items[itemIndex].labels[newLabelId]),
    shapes: _.cloneDeep(getShapes(committed, itemIndex, newLabelId))
  }
  drawHistory.recordCut(itemIndex, labelId, before, after, newLine)
  return "cut"
}
```

- [ ] **Step 4: Run tests to verify they pass**

Same command as Step 2 (or `npx tsc --noEmit` per the environment caveat). Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/src/drawable/2d/polyline_cut.ts app/test/drawable/polyline_cut.test.ts
git commit -m "feat: performCut scans open polylines and applies an atomic cut

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Scissors icon/cursor + toolbar button

**Files:**
- Create: `app/src/components/cut_icon.tsx`
- Modify: `app/src/components/viewer2d.tsx` (imports at top; fields ~line 60; `getMenuComponents` return array ~line 261; new lifecycle + `getCutButton` methods)

**Interfaces:**
- Consumes: `isCutMode`, `setCutMode`, `onCutModeChange` (Task 1); `Session.label2dList.isDrawingInProgress()`.
- Produces: `ContentCutIcon(props: SvgIconProps): JSX.Element` and `CUT_CURSOR: string` from `app/src/components/cut_icon` — Task 7/8 import both.

There is no automated test for this task (React component; drawable/component suites do not run locally). Per-step check is `npx tsc --noEmit`; behavior is verified in Task 9's manual QA.

- [ ] **Step 1: Create the icon/cursor module**

Create `app/src/components/cut_icon.tsx`:

```tsx
import SvgIcon, { SvgIconProps } from "@material-ui/core/SvgIcon"
import React from "react"

/**
 * The material design "content_cut" scissors path (24x24 viewBox). Inlined
 * because @material-ui/icons v4 does not ship a ContentCut icon.
 */
export const CONTENT_CUT_PATH =
  "M9.64 7.64c.23-.5.36-1.05.36-1.64 0-2.21-1.79-4-4-4S2 3.79 2 6s1.79 4 4 " +
  "4c.59 0 1.14-.13 1.64-.36L10 12l-2.36 2.36C7.14 14.13 6.59 14 6 " +
  "14c-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4c0-.59-.13-1.14-.36-1.64L12 " +
  "14l7 7h3v-1L9.64 7.64zM6 8c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 " +
  "2zm0 12c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm6-7.5c-.28 " +
  "0-.5-.22-.5-.5s.22-.5.5-.5.5.22.5.5-.22.5-.5.5zM19 3l-6 6 2 2 7-7V3z"

/**
 * Scissors icon for the cut tool (toolbar button + context-menu item).
 *
 * @param props standard SvgIcon props (fontSize, style, ...)
 */
export function ContentCutIcon(props: SvgIconProps): JSX.Element {
  return (
    <SvgIcon {...props}>
      <path d={CONTENT_CUT_PATH} />
    </SvgIcon>
  )
}

const CUT_CURSOR_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" ' +
  'viewBox="0 0 24 24"><path d="' +
  CONTENT_CUT_PATH +
  '" fill="white" stroke="black" stroke-width="1"/></svg>'

/**
 * CSS cursor shown while the cut tool is armed: a scissors glyph (white fill,
 * black outline, visible on any image) with the hotspot at the blade crossing
 * (12, 12). Falls back to crosshair where SVG cursors are unsupported.
 */
export const CUT_CURSOR = `url('data:image/svg+xml;utf8,${encodeURIComponent(
  CUT_CURSOR_SVG
)}') 12 12, crosshair`
```

- [ ] **Step 2: Add the toolbar button to Viewer2D**

In `app/src/components/viewer2d.tsx`:

2a. Add imports (after the existing `import Session from "../common/session"` block):

```ts
import { isCutMode, onCutModeChange, setCutMode } from "../common/cut_state"
import { ContentCutIcon } from "./cut_icon"
```

2b. Add a private field next to the other private fields of `Viewer2D` (after `private _panRAFPending: boolean = false`):

```ts
  /** unsubscribe from cut-mode change notifications */
  private _offCutModeChange: (() => void) | null = null
```

2c. Add lifecycle overrides (the base `DrawableViewer` defines both and must be called via super) — place them right before `getDrawableComponents`:

```ts
  /**
   * Mount: re-render the toolbar tint when cut mode changes elsewhere
   * (Escape in the canvas, a successful one-shot cut, context-menu arming).
   */
  public componentDidMount(): void {
    super.componentDidMount()
    this._offCutModeChange = onCutModeChange(() => this.forceUpdate())
  }

  /**
   * Unmount: stop listening for cut-mode changes.
   */
  public componentWillUnmount(): void {
    super.componentWillUnmount()
    if (this._offCutModeChange !== null) {
      this._offCutModeChange()
      this._offCutModeChange = null
    }
  }
```

2d. Add the button builder after `getHistoryButtons`:

```tsx
  /**
   * Build the scissor (cut polyline) toolbar button. One-shot: arming it cuts
   * on the next canvas click; a successful cut (or Escape) disarms it.
   *
   * @return {JSX.Element} the cut button
   */
  protected getCutButton(): JSX.Element {
    const armed = isCutMode()
    return (
      <Tooltip
        key={`cut2dButton${this.props.id}`}
        title="Cut polyline"
        enterDelay={500}
        TransitionComponent={Fade}
        TransitionProps={{ timeout: 600 }}
        arrow
      >
        <IconButton
          onClick={() => {
            if (armed) {
              setCutMode(false)
            } else if (
              !Session.label2dList.isDrawingInProgress() &&
              !this.state.task.config.tracking
            ) {
              setCutMode(true)
            }
          }}
          className={this.props.classes.viewer_button}
          style={{ color: armed ? "#4caf50" : undefined }}
          edge={"start"}
        >
          <ContentCutIcon />
        </IconButton>
      </Tooltip>
    )
  }
```

2e. In `getMenuComponents`, change the return array to include the button after undo/redo:

```ts
      return [
        zoomInButton,
        zoomOutButton,
        resetZoomButton,
        widthUpButton,
        widthDownButton,
        widthResetButton,
        ...this.getHistoryButtons(),
        this.getCutButton()
      ]
```

- [ ] **Step 3: Typecheck**

Run: `npx tsc --noEmit`
Expected: exit 0, no errors.

- [ ] **Step 4: Commit**

```bash
git add app/src/components/cut_icon.tsx app/src/components/viewer2d.tsx
git commit -m "feat: scissor toolbar button + inline content_cut icon and cursor

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Canvas wiring — cut click, scissors cursor, Escape, item-change disarm

**Files:**
- Modify: `app/src/components/label2d_canvas.tsx` (imports; new field; `onMouseDown` ~line 341; `onMouseMove` ~line 432; `onKeyDown` ~line 476; `updateState` ~line 520)

**Interfaces:**
- Consumes: `isCutMode`, `setCutMode` (Task 1); `performCut`, `CUT_CLICK_RADIUS_PX`, `CUT_SNAP_RADIUS_PX` (Task 5); `CUT_CURSOR` (Task 6). `alert`, `Severity`, `Key` are already imported in this file.
- Produces: nothing new for later tasks (Task 8 modifies the same file).

Per-step check is `npx tsc --noEmit`; behavior is verified in Task 9's manual QA.

- [ ] **Step 1: Add imports**

In `app/src/components/label2d_canvas.tsx`, after the existing `pointer_pan_state` import block, add:

```ts
import { isCutMode, setCutMode } from "../common/cut_state"
import {
  CUT_CLICK_RADIUS_PX,
  CUT_SNAP_RADIUS_PX,
  performCut
} from "../drawable/2d/polyline_cut"
import { CUT_CURSOR } from "./cut_icon"
```

- [ ] **Step 2: Add the item-tracking field**

Next to `private _offIdle: (() => void) | null = null` add:

```ts
  /** last seen item index, to disarm the cut tool on item navigation */
  private _cutItemIndex: number = -1
```

- [ ] **Step 3: Consume the armed click in `onMouseDown`**

In `onMouseDown`, directly AFTER the `if (e.ctrlKey || e.metaKey) { return }` block and BEFORE the `if (labelIndex < 0 || inPanWindow(Date.now()))` empty-space/pan-window block, insert (`mousePos` is already in scope from earlier in the method):

```ts
    // One-shot cut tool: while armed, this click belongs to the scissors.
    // It never starts a draw or select; a successful cut disarms the tool,
    // any rejection keeps it armed so the user can re-aim.
    if (isCutMode()) {
      const result = performCut(
        mousePos,
        CUT_CLICK_RADIUS_PX / this.displayToImageRatio,
        CUT_SNAP_RADIUS_PX / this.displayToImageRatio
      )
      switch (result) {
        case "cut":
          setCutMode(false)
          this.setDefaultCursor()
          break
        case "curve":
          alert(
            Severity.WARNING,
            "Cannot cut a curved segment — straighten it first."
          )
          break
        case "closed":
          alert(Severity.WARNING, "Cut works on open polylines only.")
          break
        case "near-endpoint":
          alert(Severity.WARNING, "Too close to an endpoint to cut.")
          break
        case "miss":
          break
      }
      return
    }
```

- [ ] **Step 4: Scissors cursor in `onMouseMove`**

At the END of `onMouseMove`, after the existing `if (this._labelHandler.highlightedLabel !== null) { ... } else { this.setDefaultCursor() }` block, add:

```ts
    if (isCutMode()) {
      // The scissors cursor overrides hover cursors while the tool is armed.
      this.setCursor(CUT_CURSOR)
    }
```

- [ ] **Step 5: Escape disarms in `onKeyDown`**

In `onKeyDown`, directly after the `if (this.checkFreeze()) { return }` block, add:

```ts
    if (e.key === Key.ESCAPE && isCutMode()) {
      // Escape disarms the one-shot cut tool.
      setCutMode(false)
      this.setDefaultCursor()
      return
    }
```

- [ ] **Step 6: Item navigation disarms in `updateState`**

In `updateState`, before the `this._labelHandler.updateState(state)` line, add:

```ts
    if (this._cutItemIndex !== state.user.select.item) {
      // Navigating to another image disarms the one-shot cut tool.
      if (this._cutItemIndex !== -1) {
        setCutMode(false)
      }
      this._cutItemIndex = state.user.select.item
    }
```

- [ ] **Step 7: Typecheck**

Run: `npx tsc --noEmit`
Expected: exit 0, no errors.

- [ ] **Step 8: Commit**

```bash
git add app/src/components/label2d_canvas.tsx
git commit -m "feat: cut-tool click handling, scissors cursor and disarm paths on the 2D canvas

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Right-click context menu that arms the cut tool

**Files:**
- Modify: `app/src/components/label2d_canvas.tsx` (imports; new field; `render` ~line 193; new `onContextMenu` method)

**Interfaces:**
- Consumes: `setCutMode` (already imported in Task 7); `ContentCutIcon`, `CUT_CURSOR` (Task 6, `CUT_CURSOR` already imported).
- Produces: nothing for later tasks.

Note: the browser's native context menu is already suppressed globally in `app/src/components/window.tsx` (`document.addEventListener("contextmenu", preventDefault)`), so the right-click is free for this menu.

- [ ] **Step 1: Add imports**

Add to `app/src/components/label2d_canvas.tsx`:

```ts
import Menu from "@material-ui/core/Menu"
import MenuItem from "@material-ui/core/MenuItem"
```

and extend the Task 7 cut_icon import to:

```ts
import { ContentCutIcon, CUT_CURSOR } from "./cut_icon"
```

- [ ] **Step 2: Add the menu-anchor field**

Next to `private _cutItemIndex: number = -1` add:

```ts
  /** context-menu anchor (viewport px), null while the menu is closed */
  private _menuAnchor: { left: number; top: number } | null = null
```

(React component state is not available here — `this.state` is the mapped redux state — so the anchor is an instance field flushed with `forceUpdate`, the same pattern the class already uses for `display`.)

- [ ] **Step 3: Add the `onContextMenu` handler method**

Add after `onMouseMove`:

```ts
  /**
   * Open the canvas context menu on right-click. The menu's only entry arms
   * the one-shot cut tool — arming via menu beats cutting at the right-click
   * point because precisely right-clicking a thin polyline is hard.
   *
   * @param {MouseEvent} e - event
   */
  public onContextMenu(e: React.MouseEvent<HTMLCanvasElement>): void {
    e.preventDefault()
    if (this.checkFreeze()) {
      return
    }
    this._menuAnchor = { left: e.clientX, top: e.clientY }
    this.forceUpdate()
  }
```

- [ ] **Step 4: Wire the handler and render the menu**

4a. In `render()`, add the handler to the label canvas JSX (next to the existing `onMouseMove` prop):

```tsx
        onContextMenu={(e) => {
          this.onContextMenu(e)
        }}
```

4b. Still in `render()`, after the `if (this.display !== null) { ... }` block and before the return, build the menu:

```tsx
    const contextMenu = (
      <Menu
        key="cut-context-menu"
        open={this._menuAnchor !== null}
        onClose={() => {
          this._menuAnchor = null
          this.forceUpdate()
        }}
        anchorReference="anchorPosition"
        anchorPosition={
          this._menuAnchor !== null ? this._menuAnchor : undefined
        }
      >
        <MenuItem
          dense
          disabled={
            Session.label2dList.isDrawingInProgress() ||
            this.state.task.config.tracking
          }
          onClick={() => {
            this._menuAnchor = null
            setCutMode(true)
            this.setCursor(CUT_CURSOR)
            this.forceUpdate()
          }}
        >
          <ContentCutIcon fontSize="small" style={{ marginRight: 8 }} />
          Cut polyline
        </MenuItem>
      </Menu>
    )
```

4c. Change the return to include it:

```tsx
    return [ch, controlCanvas, labelCanvas, contextMenu]
```

- [ ] **Step 5: Typecheck**

Run: `npx tsc --noEmit`
Expected: exit 0, no errors.

- [ ] **Step 6: Commit**

```bash
git add app/src/components/label2d_canvas.tsx
git commit -m "feat: right-click context menu arms the polyline cut tool

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Full verification, docs update

**Files:**
- Modify: `docs/polyline-feature-map.md`

- [ ] **Step 1: Run the full local verification suite**

```bash
npx tsc --noEmit
```
Expected: exit 0.

```bash
npx jest app/test/common/cut_state.test.ts app/test/drawable/polyline_cut_geometry.test.ts --env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js --coverage=false
```
Expected: PASS (12 tests).

```bash
npx jest app/test/drawable/draw_history_cut.test.ts app/test/drawable/polyline_cut.test.ts --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js --coverage=false
```
Expected: PASS — or suite-load failure on missing native canvas (known local limitation; these run in CI).

```bash
npm run lint
```
Expected: for each file this plan touched, zero NON-`prettier/prettier` violations that are not already present on HEAD (the CRLF prettier noise is pre-existing — ignore it).

- [ ] **Step 2: Manual QA (runtime)**

Launch the app the usual way for this fork (webpack build + `npm run serve`, or the running dev deployment). On a 2D polyline task:

1. Toolbar scissor button appears after Redo; clicking it tints it green and the canvas cursor becomes scissors on the next mouse move.
2. Left-click mid-segment of a polyline → it splits into two independently selectable polylines; the tool disarms; cursor restores.
3. One Ctrl+Z → original restored exactly; Ctrl+Y → cut re-applied.
4. While armed: click empty space (nothing happens, stays armed), click a curved segment (toast, stays armed), click near an endpoint (toast, stays armed), click a closed polygon (toast, stays armed).
5. Escape while armed → disarms, button un-tints (listener-driven re-render).
6. Right-click anywhere → menu with "✂ Cut polyline"; selecting it arms the tool; the item is disabled while drawing a polyline.
7. Cut → export (or check state) → both halves' coordinates are in the original image frame; zoom/pan/rotation do not change them.
8. Navigate to the next image while armed → tool disarms.

- [ ] **Step 3: Update the feature map**

In `docs/polyline-feature-map.md`:

- §1 (drawable): add a bullet:
  ```
  - `app/src/drawable/2d/polyline_cut_geometry.ts` — pure cut-site math:
    `findCutSite` (nearest-span projection, curve/endpoint guards, vertex snap),
    `buildCutHalves`. No Session/DOM imports — testable with the node-env recipe.
  - `app/src/drawable/2d/polyline_cut.ts` — `performCut` (scan open polylines →
    split → delete+add original id, add new label → `drawHistory.recordCut`).
  ```
- §4 (undo): extend the DrawHistory bullet's command kinds to `created`/`edited`/`deleted`/`cut`, and mention `recordCut` (atomic cut undo).
- §7 (toolbar): add:
  ```
  - `app/src/common/cut_state.ts` — cut-tool armed flag (+ change listeners);
    `app/src/components/cut_icon.tsx` — scissors icon + CSS cursor. Toolbar
    button in `viewer2d.tsx getCutButton`; click/cursor/Escape/context-menu
    wiring in `label2d_canvas.tsx`.
  ```
- §8 (tests): mention `app/test/setup/noop.js` as the ready-made noop globalSetup for the pure-test recipe.

- [ ] **Step 4: Commit**

```bash
git add docs/polyline-feature-map.md
git commit -m "docs: map entries for the polyline cut tool

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
