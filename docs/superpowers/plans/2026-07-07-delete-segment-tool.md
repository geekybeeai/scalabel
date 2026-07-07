# Delete Segment Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a delete-segment tool: arm it (toolbar button or right-click menu), pick two points on a polyline, watch a 3-second green dashed preview of the doomed piece, and it is deleted atomically — with one-press undo.

**Architecture:** A phase state machine (`segment_delete_state.ts`, following `cut_state.ts`) tracks `inactive → awaitFirst → awaitSecond → preview`. Picks reuse the cut tool's geometry (`findCutSite`) and visibility filter; a pick near a line end is a valid "trim" pick. On the second pick the doomed piece is computed (`buildSegmentDeletePieces`, pure) and the canvas animates it (marching-ants post-pass in `redraw()` + a 3 s timer). The commit builds the survivors directly and dispatches ONE `makeSequential` — recorded as the existing `"cut"` (two survivors), `"edited"` (one), or `"deleted"` (none) history command. Redux is untouched until the commit, so every cancel path is free.

**Tech Stack:** TypeScript + React 16 class components, redux (custom store in `common/session.ts`), Material-UI v4, jest 26 (ts-jest).

**Spec:** `docs/superpowers/specs/2026-07-07-delete-segment-tool-design.md` — read it first. The shipped cut tool (`docs/superpowers/specs/2026-07-07-polyline-cut-tool-design.md`) is the base.

## Global Constraints

- **No new npm dependencies.** The new toolbar glyph is another inline `SvgIcon` path in `cut_icon.tsx`.
- **Coordinates golden rule:** all geometry runs in ORIGINAL-IMAGE pixel coordinates. Screen-px thresholds (`CUT_CLICK_RADIUS_PX` = 20, `CUT_SNAP_RADIUS_PX` = 8, both reused) are converted by dividing by `displayToImageRatio`.
- **Code style:** no semicolons, double quotes, JSDoc block on every exported/public symbol with `@param` lines. **LINT PITFALL:** the eslint `jsdoc/require-param` rule demands nested `@param click.x` / `@param click.y` lines for inline-object-typed parameters (named interface params do NOT need them).
- **Lint bar:** `npx eslint -c .eslintrc.json --ext .ts,.tsx <changed files>` — no NEW non-prettier rule errors vs HEAD. Pre-existing: pervasive `prettier/prettier` CRLF noise, plus 2 `dot-notation` errors in `label2d_canvas.tsx`.
- **Test environment:** pure suites run locally via `--env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js`. Harness suites (importing `app/test/drawable/util`) fail to LOAD locally (native canvas absent) — they are CI-only; fall back to `npx tsc --noEmit` + hand-trace, note it, commit anyway. Do NOT install canvas/redis, do NOT weaken tests.
- **Behavioral invariants (from spec):** picks on straight segments only; end-region picks are valid trims; both picks on the same polyline; pick order doesn't matter; "too close" picks rejected; 3 s preview then auto-commit; Escape/item-nav cancels at every phase with zero redux impact; one-shot; survivors `manual: true`; left/single survivor keeps the original label id; atomic single dispatch; one-press undo; hidden labels never pickable; arming this tool disarms the cut tool and vice versa.
- Work on branch `feature-opimization`. Commit after every task with trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. Commit ONLY the files each task names — check `git status` first.

---

### Task 1: Geometry — endpoint identification, pick types, normalization

**Files:**
- Modify: `app/src/drawable/2d/polyline_cut_geometry.ts` (extend `CutSiteResult`; append new types/functions)
- Test: `app/test/drawable/polyline_cut_geometry.test.ts` (append)

**Interfaces:**
- Consumes: existing `CutSite`, `findCutSite`, `SimplePathPoint2DType`.
- Produces (Tasks 2–4 rely on these exact shapes):

```ts
// "near-endpoint" gains which endpoint:
| { kind: "near-endpoint"; distance: number; endpointIndex: number }

export type DeleteSitePick =
  | { kind: "interior"; site: CutSite }
  | { kind: "end"; endpointIndex: number }

export function resolvePickPoint(
  points: readonly SimplePathPoint2DType[],
  pick: DeleteSitePick
): { x: number; y: number }

export function sitePositionKey(
  points: readonly SimplePathPoint2DType[],
  pick: DeleteSitePick
): number

export type NormalizedDeletePicks =
  | { kind: "ok"; first: DeleteSitePick; second: DeleteSitePick }
  | { kind: "too-close" }

export function normalizeDeletePicks(
  points: readonly SimplePathPoint2DType[],
  a: DeleteSitePick,
  b: DeleteSitePick,
  snapRadius: number
): NormalizedDeletePicks
```

- [ ] **Step 1: Write the failing tests**

Append to `app/test/drawable/polyline_cut_geometry.test.ts` (extend the existing import from `polyline_cut_geometry` with `normalizeDeletePicks`, `resolvePickPoint`, `sitePositionKey`; the file already has the `pt()` helper and `PathPointType` import):

```ts
describe("near-endpoint endpointIndex", () => {
  test("identifies the start endpoint", () => {
    const points = [pt(0, 0), pt(100, 0)]
    const result = findCutSite(points, { x: 2, y: 3 }, 10, 8)
    expect(result.kind).toBe("near-endpoint")
    if (result.kind === "near-endpoint") {
      expect(result.endpointIndex).toBe(0)
    }
  })

  test("identifies the last endpoint", () => {
    const points = [pt(0, 0), pt(100, 0), pt(200, 0)]
    const result = findCutSite(points, { x: 198, y: 3 }, 10, 8)
    expect(result.kind).toBe("near-endpoint")
    if (result.kind === "near-endpoint") {
      expect(result.endpointIndex).toBe(2)
    }
  })
})

describe("delete-pick normalization", () => {
  const line = [pt(0, 0), pt(100, 0), pt(200, 0)]

  /**
   * Shorthand: an interior projection pick on segment i at (x, y).
   *
   * @param segmentIndex the segment start index
   * @param x cut x
   * @param y cut y
   */
  function interior(segmentIndex: number, x: number, y: number) {
    return {
      kind: "interior" as const,
      site: {
        segmentIndex,
        point: { x, y },
        snappedVertexIndex: null,
        distance: 0
      }
    }
  }

  test("resolvePickPoint resolves interior and end picks", () => {
    expect(resolvePickPoint(line, interior(0, 50, 0))).toEqual({ x: 50, y: 0 })
    expect(resolvePickPoint(line, { kind: "end", endpointIndex: 2 })).toEqual({
      x: 200,
      y: 0
    })
  })

  test("orders picks along the line regardless of click order", () => {
    const a = interior(1, 150, 0)
    const b = interior(0, 50, 0)
    const result = normalizeDeletePicks(line, a, b, 8)
    expect(result.kind).toBe("ok")
    if (result.kind === "ok") {
      expect(result.first).toBe(b)
      expect(result.second).toBe(a)
    }
  })

  test("orders end picks before/after interior picks", () => {
    const start = { kind: "end" as const, endpointIndex: 0 }
    const mid = interior(1, 150, 0)
    expect(sitePositionKey(line, start)).toBeLessThan(
      sitePositionKey(line, mid)
    )
    expect(
      sitePositionKey(line, { kind: "end", endpointIndex: 2 })
    ).toBeGreaterThan(sitePositionKey(line, mid))
  })

  test("orders two picks on the same segment by projection position", () => {
    const nearer = interior(0, 30, 0)
    const farther = interior(0, 80, 0)
    const result = normalizeDeletePicks(line, farther, nearer, 8)
    expect(result.kind).toBe("ok")
    if (result.kind === "ok") {
      expect(result.first).toBe(nearer)
      expect(result.second).toBe(farther)
    }
  })

  test("rejects picks closer than the snap radius", () => {
    const a = interior(0, 50, 0)
    const b = interior(0, 55, 0)
    expect(normalizeDeletePicks(line, a, b, 8).kind).toBe("too-close")
  })

  test("rejects two trims at the same end", () => {
    const a = { kind: "end" as const, endpointIndex: 0 }
    const b = { kind: "end" as const, endpointIndex: 0 }
    expect(normalizeDeletePicks(line, a, b, 8).kind).toBe("too-close")
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
npx jest app/test/drawable/polyline_cut_geometry.test.ts --env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js --coverage=false
```
Expected: FAIL — `normalizeDeletePicks` etc. not exported (and the `endpointIndex` assertions fail: property missing).

- [ ] **Step 3: Implement**

3a. In `app/src/drawable/2d/polyline_cut_geometry.ts`, change the `CutSiteResult` union member:

```ts
  | { kind: "near-endpoint"; distance: number; endpointIndex: number }
```

3b. In `findCutSite`, the near-endpoint return becomes (note `snapped` is non-null here):

```ts
  if (snapped !== null && (snapped === 0 || snapped === points.length - 1)) {
    return { kind: "near-endpoint", distance: best.dist, endpointIndex: snapped }
  }
```

3c. Append at the end of the file:

```ts
/**
 * One of the two user picks for a segment delete: an interior cut site or a
 * pick near the line's first/last vertex (an end trim).
 */
export type DeleteSitePick =
  | { kind: "interior"; site: CutSite }
  | { kind: "end"; endpointIndex: number }

/** Result of ordering/validating a pair of delete picks. */
export type NormalizedDeletePicks =
  | { kind: "ok"; first: DeleteSitePick; second: DeleteSitePick }
  | { kind: "too-close" }

/**
 * Resolve a pick to its coordinate on the polyline (image frame).
 *
 * @param points the polyline's stored vertices
 * @param pick the pick to resolve
 */
export function resolvePickPoint(
  points: readonly SimplePathPoint2DType[],
  pick: DeleteSitePick
): { x: number; y: number } {
  if (pick.kind === "end") {
    const p = points[pick.endpointIndex]
    return { x: p.x, y: p.y }
  }
  return { x: pick.site.point.x, y: pick.site.point.y }
}

/**
 * Monotonic position of a pick along the polyline, for ordering the two
 * delete picks: start end < vertex/segment positions < last end.
 *
 * @param points the polyline's stored vertices
 * @param pick the pick to position
 */
export function sitePositionKey(
  points: readonly SimplePathPoint2DType[],
  pick: DeleteSitePick
): number {
  if (pick.kind === "end") {
    return pick.endpointIndex === 0 ? -1 : points.length
  }
  if (pick.site.snappedVertexIndex !== null) {
    return pick.site.snappedVertexIndex
  }
  const i = pick.site.segmentIndex
  const a = points[i]
  const b = points[i + 1]
  const segLen = Math.hypot(b.x - a.x, b.y - a.y)
  const t =
    segLen > 0
      ? Math.hypot(pick.site.point.x - a.x, pick.site.point.y - a.y) / segLen
      : 0
  return i + t
}

/**
 * Validate and order the two picks of a segment delete. Picks whose resolved
 * coordinates are within `snapRadius` of each other (including two trims at
 * the same end) are rejected as "too-close"; otherwise the picks are returned
 * ordered by position along the line, so the caller never cares which one
 * the user clicked first.
 *
 * @param points the polyline's stored vertices
 * @param a one pick
 * @param b the other pick
 * @param snapRadius minimum separation (image px)
 */
export function normalizeDeletePicks(
  points: readonly SimplePathPoint2DType[],
  a: DeleteSitePick,
  b: DeleteSitePick,
  snapRadius: number
): NormalizedDeletePicks {
  const pa = resolvePickPoint(points, a)
  const pb = resolvePickPoint(points, b)
  if (Math.hypot(pa.x - pb.x, pa.y - pb.y) <= snapRadius) {
    return { kind: "too-close" }
  }
  return sitePositionKey(points, a) <= sitePositionKey(points, b)
    ? { kind: "ok", first: a, second: b }
    : { kind: "ok", first: b, second: a }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Same command as Step 2. Expected: PASS (12 pre-existing + 8 new = 20 tests). Also run `npx tsc --noEmit` (exit 0) — `performCut` ignores the added `endpointIndex` field, nothing else consumes near-endpoint destructured.

- [ ] **Step 5: Lint and commit**

Run: `npx eslint -c .eslintrc.json --ext .ts app/src/drawable/2d/polyline_cut_geometry.ts app/test/drawable/polyline_cut_geometry.test.ts` — zero rule errors.

```bash
git add app/src/drawable/2d/polyline_cut_geometry.ts app/test/drawable/polyline_cut_geometry.test.ts
git commit -m "feat: delete-pick types, endpoint identification and pick normalization

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Geometry — `buildSegmentDeletePieces`

**Files:**
- Modify: `app/src/drawable/2d/polyline_cut_geometry.ts` (append)
- Test: `app/test/drawable/polyline_cut_geometry.test.ts` (append)

**Interfaces:**
- Consumes: `DeleteSitePick` (Task 1), `SimplePathPoint2DType`, `PathPointType`.
- Produces (Task 4 and the canvas overlay rely on this exact shape):

```ts
export interface SegmentDeletePieces {
  left?: SimplePathPoint2DType[]
  right?: SimplePathPoint2DType[]
  doomed: SimplePathPoint2DType[]
}
export function buildSegmentDeletePieces(
  points: readonly SimplePathPoint2DType[],
  first: DeleteSitePick,
  second: DeleteSitePick
): SegmentDeletePieces
```

(`first`/`second` MUST already be ordered by `normalizeDeletePicks`.)

- [ ] **Step 1: Write the failing tests**

Append to `app/test/drawable/polyline_cut_geometry.test.ts` (add `buildSegmentDeletePieces` to the geometry import; reuse the `interior()` helper by moving it up next to `pt()` at the top of the file if needed — it is used by two describes now):

```ts
describe("buildSegmentDeletePieces", () => {
  const line = [pt(0, 0), pt(100, 0), pt(200, 0), pt(300, 0)]

  test("middle delete: left keeps head, right keeps tail, doomed spans picks", () => {
    const { left, right, doomed } = buildSegmentDeletePieces(
      line,
      interior(0, 50, 0),
      interior(2, 250, 0)
    )
    expect(left).toEqual([pt(0, 0), pt(50, 0)])
    expect(right).toEqual([pt(250, 0), pt(300, 0)])
    expect(doomed).toEqual([pt(50, 0), pt(100, 0), pt(200, 0), pt(250, 0)])
    expect(line).toHaveLength(4) // input not mutated
  })

  test("both picks on the same segment", () => {
    const { left, right, doomed } = buildSegmentDeletePieces(
      line,
      interior(1, 130, 0),
      interior(1, 170, 0)
    )
    expect(left).toEqual([pt(0, 0), pt(100, 0), pt(130, 0)])
    expect(right).toEqual([pt(170, 0), pt(200, 0), pt(300, 0)])
    expect(doomed).toEqual([pt(130, 0), pt(170, 0)])
  })

  test("vertex-snapped picks share the vertex without duplicating it", () => {
    const snapped = (v: number): DeleteSitePick => ({
      kind: "interior",
      site: {
        segmentIndex: v - 1,
        point: { x: line[v].x, y: line[v].y },
        snappedVertexIndex: v,
        distance: 0
      }
    })
    const { left, right, doomed } = buildSegmentDeletePieces(
      line,
      snapped(1),
      snapped(2)
    )
    expect(left).toEqual([pt(0, 0), pt(100, 0)])
    expect(right).toEqual([pt(200, 0), pt(300, 0)])
    expect(doomed).toEqual([pt(100, 0), pt(200, 0)])
  })

  test("start trim: no left piece", () => {
    const { left, right, doomed } = buildSegmentDeletePieces(
      line,
      { kind: "end", endpointIndex: 0 },
      interior(1, 150, 0)
    )
    expect(left).toBeUndefined()
    expect(right).toEqual([pt(150, 0), pt(200, 0), pt(300, 0)])
    expect(doomed).toEqual([pt(0, 0), pt(100, 0), pt(150, 0)])
  })

  test("end trim: no right piece", () => {
    const { left, right, doomed } = buildSegmentDeletePieces(
      line,
      interior(1, 150, 0),
      { kind: "end", endpointIndex: 3 }
    )
    expect(left).toEqual([pt(0, 0), pt(100, 0), pt(150, 0)])
    expect(right).toBeUndefined()
    expect(doomed).toEqual([pt(150, 0), pt(200, 0), pt(300, 0)])
  })

  test("both ends: whole line doomed, no survivors", () => {
    const { left, right, doomed } = buildSegmentDeletePieces(
      line,
      { kind: "end", endpointIndex: 0 },
      { kind: "end", endpointIndex: 3 }
    )
    expect(left).toBeUndefined()
    expect(right).toBeUndefined()
    expect(doomed).toEqual(line)
  })

  test("curve spans in survivors are preserved verbatim", () => {
    const curvy = [
      pt(0, 0),
      pt(30, 10, PathPointType.CURVE),
      pt(60, 10, PathPointType.CURVE),
      pt(100, 0),
      pt(200, 0),
      pt(300, 0)
    ]
    const { left, right, doomed } = buildSegmentDeletePieces(
      curvy,
      interior(3, 150, 0),
      interior(4, 250, 0)
    )
    expect(left).toEqual([
      pt(0, 0),
      pt(30, 10, PathPointType.CURVE),
      pt(60, 10, PathPointType.CURVE),
      pt(100, 0),
      pt(150, 0)
    ])
    expect(right).toEqual([pt(250, 0), pt(300, 0)])
    expect(doomed).toEqual([pt(150, 0), pt(200, 0), pt(250, 0)])
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Same jest command as Task 1 Step 2. Expected: FAIL — `buildSegmentDeletePieces` not exported.

- [ ] **Step 3: Implement**

Append to `app/src/drawable/2d/polyline_cut_geometry.ts`:

```ts
/** The pieces of a segment delete: optional survivors and the removed path. */
export interface SegmentDeletePieces {
  /** survivor before the first pick (absent on a start trim / whole-line) */
  left?: SimplePathPoint2DType[]
  /** survivor after the second pick (absent on an end trim / whole-line) */
  right?: SimplePathPoint2DType[]
  /** the removed path, including the pick coordinates (for the preview) */
  doomed: SimplePathPoint2DType[]
}

/**
 * Build the survivors and the doomed piece of a segment delete.
 *
 * `first`/`second` MUST already be ordered by normalizeDeletePicks. Interior
 * picks split exactly like the cut tool (projection point becomes a new LINE
 * vertex in both the survivor and the doomed piece; a vertex-snapped pick
 * shares the snapped vertex's coordinate without duplicating it within any
 * piece). End picks produce no survivor on their side. Returned points are
 * fresh copies; the input is never mutated.
 *
 * @param points the polyline's stored vertices
 * @param first the earlier pick along the line
 * @param second the later pick along the line
 */
export function buildSegmentDeletePieces(
  points: readonly SimplePathPoint2DType[],
  first: DeleteSitePick,
  second: DeleteSitePick
): SegmentDeletePieces {
  const copy = (p: SimplePathPoint2DType): SimplePathPoint2DType => ({
    x: p.x,
    y: p.y,
    pointType: p.pointType
  })

  let left: SimplePathPoint2DType[] | undefined
  let head: SimplePathPoint2DType[]
  let from: number
  if (first.kind === "end") {
    left = undefined
    head = []
    from = 0
  } else if (first.site.snappedVertexIndex !== null) {
    const s = first.site.snappedVertexIndex
    left = points.slice(0, s + 1).map(copy)
    head = [copy(points[s])]
    from = s + 1
  } else {
    const i = first.site.segmentIndex
    const p1: SimplePathPoint2DType = {
      x: first.site.point.x,
      y: first.site.point.y,
      pointType: PathPointType.LINE
    }
    left = [...points.slice(0, i + 1).map(copy), { ...p1 }]
    head = [{ ...p1 }]
    from = i + 1
  }

  let right: SimplePathPoint2DType[] | undefined
  let tail: SimplePathPoint2DType[]
  let to: number
  if (second.kind === "end") {
    right = undefined
    tail = []
    to = points.length - 1
  } else if (second.site.snappedVertexIndex !== null) {
    const u = second.site.snappedVertexIndex
    right = points.slice(u).map(copy)
    tail = []
    to = u
  } else {
    const j = second.site.segmentIndex
    const p2: SimplePathPoint2DType = {
      x: second.site.point.x,
      y: second.site.point.y,
      pointType: PathPointType.LINE
    }
    right = [{ ...p2 }, ...points.slice(j + 1).map(copy)]
    tail = [{ ...p2 }]
    to = j
  }

  const doomed = [...head, ...points.slice(from, to + 1).map(copy), ...tail]
  return { left, right, doomed }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Same jest command. Expected: PASS (27 tests). `npx tsc --noEmit` exit 0.

- [ ] **Step 5: Lint and commit**

Run the eslint invocation from Task 1 Step 5 — zero rule errors.

```bash
git add app/src/drawable/2d/polyline_cut_geometry.ts app/test/drawable/polyline_cut_geometry.test.ts
git commit -m "feat: buildSegmentDeletePieces computes survivors and doomed path

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `segment_delete_state.ts` — the phase state machine

**Files:**
- Create: `app/src/common/segment_delete_state.ts`
- Test: `app/test/common/segment_delete_state.test.ts`

**Interfaces:**
- Consumes: `setCutMode`, `isCutMode`, `onCutModeChange` from `./cut_state`; type-only `DeleteSitePick` from `../drawable/2d/polyline_cut_geometry`; `IdType`, `SimplePathPoint2DType` from `../types/state`.
- Produces (Tasks 4–7 rely on these exact signatures):

```ts
export type SegmentDeletePhase =
  | "inactive" | "awaitFirst" | "awaitSecond" | "preview"
export interface SegmentDeletePickData {
  itemIndex: number
  labelId: IdType
  pick1: DeleteSitePick
  pick1Point: { x: number; y: number }
  points: SimplePathPoint2DType[]
}
export interface SegmentDeletePreviewData extends SegmentDeletePickData {
  first: DeleteSitePick
  second: DeleteSitePick
  doomed: SimplePathPoint2DType[]
}
export function getSegmentDeletePhase(): SegmentDeletePhase
export function isSegmentDeleteActive(): boolean
export function armSegmentDelete(): void
export function recordFirstPick(data: SegmentDeletePickData): void
export function recordSecondPick(first: DeleteSitePick, second: DeleteSitePick, doomed: SimplePathPoint2DType[]): void
export function getPickData(): SegmentDeletePickData | null
export function getPreviewData(): SegmentDeletePreviewData | null
export function resetSegmentDelete(): void
export function onSegmentDeleteChange(listener: () => void): () => void
```

- [ ] **Step 1: Write the failing tests**

Create `app/test/common/segment_delete_state.test.ts`:

```ts
import { isCutMode, setCutMode } from "../../src/common/cut_state"
import {
  armSegmentDelete,
  getPickData,
  getPreviewData,
  getSegmentDeletePhase,
  isSegmentDeleteActive,
  onSegmentDeleteChange,
  recordFirstPick,
  recordSecondPick,
  resetSegmentDelete
} from "../../src/common/segment_delete_state"
import { DeleteSitePick } from "../../src/drawable/2d/polyline_cut_geometry"
import { PathPointType } from "../../src/types/state"

const PICK1: DeleteSitePick = {
  kind: "interior",
  site: {
    segmentIndex: 0,
    point: { x: 50, y: 0 },
    snappedVertexIndex: null,
    distance: 0
  }
}
const PICK2: DeleteSitePick = { kind: "end", endpointIndex: 2 }
const POINTS = [
  { x: 0, y: 0, pointType: PathPointType.LINE },
  { x: 100, y: 0, pointType: PathPointType.LINE },
  { x: 200, y: 0, pointType: PathPointType.LINE }
]

describe("segment_delete_state", () => {
  beforeEach(() => {
    resetSegmentDelete()
    setCutMode(false)
  })

  test("walks the phases and exposes the data", () => {
    expect(getSegmentDeletePhase()).toBe("inactive")
    expect(isSegmentDeleteActive()).toBe(false)

    armSegmentDelete()
    expect(getSegmentDeletePhase()).toBe("awaitFirst")
    expect(getPickData()).toBeNull()

    recordFirstPick({
      itemIndex: 0,
      labelId: "lineA",
      pick1: PICK1,
      pick1Point: { x: 50, y: 0 },
      points: POINTS
    })
    expect(getSegmentDeletePhase()).toBe("awaitSecond")
    expect(getPickData()?.labelId).toBe("lineA")
    expect(getPreviewData()).toBeNull()

    recordSecondPick(PICK1, PICK2, POINTS)
    expect(getSegmentDeletePhase()).toBe("preview")
    expect(getPreviewData()?.second).toBe(PICK2)
    expect(getPreviewData()?.doomed).toBe(POINTS)

    resetSegmentDelete()
    expect(getSegmentDeletePhase()).toBe("inactive")
    expect(getPickData()).toBeNull()
    expect(getPreviewData()).toBeNull()
  })

  test("notifies listeners on every transition, not on no-ops", () => {
    let calls = 0
    const off = onSegmentDeleteChange(() => {
      calls += 1
    })
    armSegmentDelete()
    expect(calls).toBe(1)
    resetSegmentDelete()
    expect(calls).toBe(2)
    resetSegmentDelete() // already inactive: no notification
    expect(calls).toBe(2)
    off()
    armSegmentDelete()
    expect(calls).toBe(2)
  })

  test("arming disarms the cut tool, and arming the cut tool resets this", () => {
    setCutMode(true)
    armSegmentDelete()
    expect(isCutMode()).toBe(false)
    expect(getSegmentDeletePhase()).toBe("awaitFirst")

    setCutMode(true)
    expect(getSegmentDeletePhase()).toBe("inactive")
    expect(isCutMode()).toBe(true)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
npx jest app/test/common/segment_delete_state.test.ts --env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js --coverage=false
```
Expected: FAIL — `Cannot find module '../../src/common/segment_delete_state'`.

- [ ] **Step 3: Implement**

Create `app/src/common/segment_delete_state.ts`:

```ts
import { DeleteSitePick } from "../drawable/2d/polyline_cut_geometry"
import { IdType, SimplePathPoint2DType } from "../types/state"
import { isCutMode, onCutModeChange, setCutMode } from "./cut_state"

/**
 * Transient, non-Redux state machine for the delete-segment tool.
 *
 * Phases: inactive -> awaitFirst -> awaitSecond -> preview -> (commit or
 * cancel) -> inactive. Picks and the preview live entirely here; redux is
 * only touched when the pending delete commits, so cancelling at any phase
 * needs no rollback. Mirrors cut_state.ts, with a payload per phase.
 */
export type SegmentDeletePhase =
  | "inactive"
  | "awaitFirst"
  | "awaitSecond"
  | "preview"

/** Data captured at the first pick. */
export interface SegmentDeletePickData {
  /** item the polyline belongs to */
  itemIndex: number
  /** the picked polyline's label id */
  labelId: IdType
  /** the first pick */
  pick1: DeleteSitePick
  /** the first pick's resolved coordinate (image frame), for the halo */
  pick1Point: { x: number; y: number }
  /** the polyline's stored vertices at pick time (staleness guard) */
  points: SimplePathPoint2DType[]
}

/** Data available during the preview countdown. */
export interface SegmentDeletePreviewData extends SegmentDeletePickData {
  /** the earlier pick along the line (normalized order) */
  first: DeleteSitePick
  /** the later pick along the line (normalized order) */
  second: DeleteSitePick
  /** the doomed path, for the marching-ants overlay */
  doomed: SimplePathPoint2DType[]
}

let phase: SegmentDeletePhase = "inactive"
let pickData: SegmentDeletePickData | null = null
let previewData: SegmentDeletePreviewData | null = null

const listeners = new Set<() => void>()

/** Notify all listeners of a state change. */
function notify(): void {
  listeners.forEach((listener) => listener())
}

/** The current phase of the delete-segment tool. */
export function getSegmentDeletePhase(): SegmentDeletePhase {
  return phase
}

/** Whether the tool owns canvas clicks (any phase but inactive). */
export function isSegmentDeleteActive(): boolean {
  return phase !== "inactive"
}

/** Arm the tool (disarms the cut tool — the tools are mutually exclusive). */
export function armSegmentDelete(): void {
  setCutMode(false)
  if (phase === "awaitFirst") {
    return
  }
  phase = "awaitFirst"
  pickData = null
  previewData = null
  notify()
}

/**
 * Record a validated first pick and await the second.
 *
 * @param data the pick and its context
 */
export function recordFirstPick(data: SegmentDeletePickData): void {
  phase = "awaitSecond"
  pickData = data
  previewData = null
  notify()
}

/**
 * Record a validated second pick and start the preview.
 *
 * @param first the earlier pick along the line (normalized order)
 * @param second the later pick along the line (normalized order)
 * @param doomed the removed path, for the overlay
 */
export function recordSecondPick(
  first: DeleteSitePick,
  second: DeleteSitePick,
  doomed: SimplePathPoint2DType[]
): void {
  if (pickData === null) {
    return
  }
  phase = "preview"
  previewData = { ...pickData, first, second, doomed }
  notify()
}

/** The pick-1 context (awaitSecond and preview phases), else null. */
export function getPickData(): SegmentDeletePickData | null {
  return phase === "awaitSecond" || phase === "preview" ? pickData : null
}

/** The preview payload (preview phase only), else null. */
export function getPreviewData(): SegmentDeletePreviewData | null {
  return phase === "preview" ? previewData : null
}

/** Cancel/finish: back to inactive. Safe to call in any phase. */
export function resetSegmentDelete(): void {
  if (phase === "inactive") {
    return
  }
  phase = "inactive"
  pickData = null
  previewData = null
  notify()
}

/**
 * Subscribe to phase changes.
 *
 * @param listener called after every transition
 * @returns an unsubscribe function
 */
export function onSegmentDeleteChange(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

// Mutual exclusion: arming the cut tool cancels any pending segment delete.
onCutModeChange(() => {
  if (isCutMode()) {
    resetSegmentDelete()
  }
})
```

- [ ] **Step 4: Run tests to verify they pass**

Same command as Step 2. Expected: PASS (3 tests). Also re-run the cut_state suite (its module-level subscription now exists):
```bash
npx jest app/test/common/ --env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js --coverage=false
```
Expected: all pass.

- [ ] **Step 5: Lint and commit**

`npx eslint -c .eslintrc.json --ext .ts app/src/common/segment_delete_state.ts app/test/common/segment_delete_state.test.ts` — zero rule errors. `npx tsc --noEmit` — exit 0.

```bash
git add app/src/common/segment_delete_state.ts app/test/common/segment_delete_state.test.ts
git commit -m "feat: delete-segment phase state machine with cut-tool exclusion

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `polyline_segment_delete.ts` — pick handling and atomic commit

**Files:**
- Create: `app/src/drawable/2d/polyline_segment_delete.ts`
- Test: `app/test/drawable/polyline_segment_delete.test.ts`

**Interfaces:**
- Consumes: geometry (Tasks 1–2), state machine (Task 3), `CutVisibilityFilter` + patterns from `./polyline_cut`, `drawHistory.recordCut/recordEdit/recordDeletedLine`, actions, `makePathPoint2D`, `uid`.
- Produces (Tasks 5–6 rely on):

```ts
export const SEGMENT_DELETE_PREVIEW_MS = 3000
export type SegmentDeletePickOutcome =
  | "first-picked" | "preview-started"
  | "miss" | "curve" | "closed" | "wrong-line" | "too-close" | "stale"
  | "ignored"
export function handleSegmentDeletePick(
  click: { x: number; y: number },
  radius: number,
  snapRadius: number,
  visibility?: CutVisibilityFilter
): SegmentDeletePickOutcome
export type SegmentDeleteCommitOutcome = "deleted" | "stale" | "ignored"
export function commitPendingSegmentDelete(): SegmentDeleteCommitOutcome
```

- [ ] **Step 1: Write the failing tests**

Create `app/test/drawable/polyline_segment_delete.test.ts`:

```ts
import * as action from "../../src/action/common"
import { drawHistory } from "../../src/common/draw_history"
import {
  armSegmentDelete,
  getSegmentDeletePhase,
  resetSegmentDelete
} from "../../src/common/segment_delete_state"
import Session, { getState } from "../../src/common/session"
import { LabelTypeName } from "../../src/const/common"
import {
  commitPendingSegmentDelete,
  handleSegmentDeletePick
} from "../../src/drawable/2d/polyline_segment_delete"
import { getShapes } from "../../src/functional/state_util"
import { makeLabel, makePathPoint2D } from "../../src/functional/states"
import { PathPoint2DType, PathPointType } from "../../src/types/state"
import { initializeTestingObjects } from "./util"

/**
 * Seed a polyline with a fixed label id into item 0.
 *
 * @param labelId the label id to use
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

/**
 * Read a label's vertices as [x, y] pairs.
 *
 * @param labelId the label id
 */
function coords(labelId: string): number[][] {
  const pts = getShapes(getState(), 0, labelId) as PathPoint2DType[]
  return pts.map((p) => [p.x, p.y])
}

describe("segment delete", () => {
  beforeEach(() => {
    resetSegmentDelete()
  })

  test("middle delete: two survivors, atomic undo restores the original", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [100, 0],
      [200, 0],
      [300, 0]
    ])

    armSegmentDelete()
    expect(handleSegmentDeletePick({ x: 50, y: 3 }, 20, 8)).toBe("first-picked")
    expect(handleSegmentDeletePick({ x: 250, y: 3 }, 20, 8)).toBe(
      "preview-started"
    )
    expect(commitPendingSegmentDelete()).toBe("deleted")
    expect(getSegmentDeletePhase()).toBe("inactive")

    const ids = Object.keys(getState().task.items[0].labels)
    expect(ids).toHaveLength(2)
    expect(coords("lineA")).toEqual([
      [0, 0],
      [50, 0]
    ])
    const rightId = ids.filter((id) => id !== "lineA")[0]
    expect(coords(rightId)).toEqual([
      [250, 0],
      [300, 0]
    ])
    expect(getState().task.items[0].labels.lineA.manual).toBe(true)
    expect(getState().task.items[0].labels[rightId].manual).toBe(true)

    expect(drawHistory.undo()).toBe(true)
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(1)
    expect(coords("lineA")).toEqual([
      [0, 0],
      [100, 0],
      [200, 0],
      [300, 0]
    ])
  })

  test("end trim: one survivor keeps the id; atomic undo restores", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [100, 0],
      [200, 0]
    ])

    armSegmentDelete()
    // First pick near the LAST endpoint (a trim pick), second mid-line:
    expect(handleSegmentDeletePick({ x: 198, y: 3 }, 20, 8)).toBe(
      "first-picked"
    )
    expect(handleSegmentDeletePick({ x: 50, y: 3 }, 20, 8)).toBe(
      "preview-started"
    )
    expect(commitPendingSegmentDelete()).toBe("deleted")

    expect(Object.keys(getState().task.items[0].labels)).toEqual(["lineA"])
    expect(coords("lineA")).toEqual([
      [0, 0],
      [50, 0]
    ])

    expect(drawHistory.undo()).toBe(true)
    expect(coords("lineA")).toEqual([
      [0, 0],
      [100, 0],
      [200, 0]
    ])
  })

  test("both ends: whole line deleted; undo restores it", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [100, 0]
    ])

    armSegmentDelete()
    expect(handleSegmentDeletePick({ x: 2, y: 3 }, 20, 8)).toBe("first-picked")
    expect(handleSegmentDeletePick({ x: 98, y: 3 }, 20, 8)).toBe(
      "preview-started"
    )
    expect(commitPendingSegmentDelete()).toBe("deleted")

    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(0)
    expect(drawHistory.undo()).toBe(true)
    expect(coords("lineA")).toEqual([
      [0, 0],
      [100, 0]
    ])
  })

  test("second pick on a different polyline is rejected", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [200, 0]
    ])
    seedLine("lineB", [
      [0, 300],
      [200, 300]
    ])

    armSegmentDelete()
    expect(handleSegmentDeletePick({ x: 100, y: 3 }, 20, 8)).toBe(
      "first-picked"
    )
    expect(handleSegmentDeletePick({ x: 100, y: 303 }, 20, 8)).toBe(
      "wrong-line"
    )
    expect(getSegmentDeletePhase()).toBe("awaitSecond")
  })

  test("coincident picks are rejected as too-close", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [200, 0]
    ])

    armSegmentDelete()
    expect(handleSegmentDeletePick({ x: 100, y: 3 }, 20, 8)).toBe(
      "first-picked"
    )
    expect(handleSegmentDeletePick({ x: 103, y: 2 }, 20, 8)).toBe("too-close")
    expect(getSegmentDeletePhase()).toBe("awaitSecond")
  })

  test("commit is cancelled when the line changed since pick 1", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [100, 0],
      [200, 0],
      [300, 0]
    ])

    armSegmentDelete()
    handleSegmentDeletePick({ x: 50, y: 3 }, 20, 8)
    handleSegmentDeletePick({ x: 250, y: 3 }, 20, 8)

    // The line is replaced behind the tool's back (like an undo would).
    Session.dispatch(action.deleteLabel(0, "lineA"))
    seedLine("lineA", [
      [0, 0],
      [500, 0]
    ])

    expect(commitPendingSegmentDelete()).toBe("stale")
    expect(getSegmentDeletePhase()).toBe("inactive")
    expect(coords("lineA")).toEqual([
      [0, 0],
      [500, 0]
    ])
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
npx jest app/test/drawable/polyline_segment_delete.test.ts --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js --coverage=false
```
Expected: FAIL — module not found. **Environment caveat:** this suite imports the drawable harness and will likely fail to LOAD locally (native canvas). If so: your red step is `npx tsc --noEmit` failing on the test file's import of the not-yet-created module; after implementing, tsc must exit 0. Hand-trace all 6 tests in your report and commit anyway — they run in CI.

- [ ] **Step 3: Implement**

Create `app/src/drawable/2d/polyline_segment_delete.ts`:

```ts
import _ from "lodash"

import { addLabel, deleteLabel, makeSequential } from "../../action/common"
import { selectLabels } from "../../action/select"
import { drawHistory, LineSnapshot } from "../../common/draw_history"
import {
  getPickData,
  getPreviewData,
  getSegmentDeletePhase,
  recordFirstPick,
  recordSecondPick,
  resetSegmentDelete
} from "../../common/segment_delete_state"
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
import { CutVisibilityFilter } from "./polyline_cut"
import {
  buildSegmentDeletePieces,
  DeleteSitePick,
  findCutSite,
  normalizeDeletePicks,
  resolvePickPoint
} from "./polyline_cut_geometry"

/** How long the doomed piece is previewed before the delete commits (ms). */
export const SEGMENT_DELETE_PREVIEW_MS = 3000

/** Outcome of a pick click, mapped to user feedback by the caller. */
export type SegmentDeletePickOutcome =
  | "first-picked"
  | "preview-started"
  | "miss"
  | "curve"
  | "closed"
  | "wrong-line"
  | "too-close"
  | "stale"
  | "ignored"

/** Outcome of committing the pending delete. */
export type SegmentDeleteCommitOutcome = "deleted" | "stale" | "ignored"

interface PickCandidate {
  /** the polyline's label id */
  labelId: IdType
  /** the resolved pick */
  pick: DeleteSitePick
  /** click-to-line distance (image px) */
  distance: number
  /** the polyline's stored vertices */
  points: SimplePathPoint2DType[]
}

type ScanOutcome =
  | { kind: "pick"; candidate: PickCandidate }
  | { kind: "curve" | "closed"; distance: number }
  | { kind: "miss" }

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
 * Whether two vertex lists are identical (staleness guard).
 *
 * @param a one list
 * @param b the other list
 */
function pointsEqual(
  a: readonly SimplePathPoint2DType[],
  b: readonly SimplePathPoint2DType[]
): boolean {
  if (a.length !== b.length) {
    return false
  }
  for (let i = 0; i < a.length; i++) {
    if (
      a[i].x !== b[i].x ||
      a[i].y !== b[i].y ||
      a[i].pointType !== b[i].pointType
    ) {
      return false
    }
  }
  return true
}

/**
 * Scan labels for the pick nearest to a click. Mirrors performCut's scan,
 * except a "near-endpoint" result is a VALID end-trim pick here. When
 * `onlyLabelId` is set, only that polyline is scanned.
 *
 * @param state the current state
 * @param itemIndex the item to scan
 * @param click the click position (image frame)
 * @param click.x click x (image px)
 * @param click.y click y (image px)
 * @param radius max click-to-line distance (image px)
 * @param snapRadius vertex snap / endpoint radius (image px)
 * @param visibility optional visibility filter (hidden labels excluded)
 * @param onlyLabelId restrict the scan to this label
 */
function scanForPick(
  state: State,
  itemIndex: number,
  click: { x: number; y: number },
  radius: number,
  snapRadius: number,
  visibility?: CutVisibilityFilter,
  onlyLabelId?: IdType
): ScanOutcome {
  const item = state.task.items[itemIndex]
  if (item === undefined) {
    return { kind: "miss" }
  }
  let best: PickCandidate | null = null
  let bestRejection: { kind: "curve" | "closed"; distance: number } | null =
    null

  const labelIds =
    onlyLabelId !== undefined ? [onlyLabelId] : Object.keys(item.labels)
  for (const labelId of labelIds) {
    const label = item.labels[labelId]
    if (label === undefined) {
      continue
    }
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
    const isOpen =
      label.type === LabelTypeName.POLYLINE_2D && label.closed !== true
    const points = storedPoints(state, itemIndex, labelId)
    if (points.length < 2) {
      continue
    }
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
    if (result.kind === "curve") {
      if (bestRejection === null || result.distance < bestRejection.distance) {
        bestRejection = { kind: "curve", distance: result.distance }
      }
      continue
    }
    // A site or an end-trim pick — both are candidates for this tool.
    const candidate: PickCandidate =
      result.kind === "site"
        ? {
            labelId,
            pick: { kind: "interior", site: result.site },
            distance: result.site.distance,
            points
          }
        : {
            labelId,
            pick: { kind: "end", endpointIndex: result.endpointIndex },
            distance: result.distance,
            points
          }
    if (best === null || candidate.distance < best.distance) {
      best = candidate
    }
  }

  if (best === null) {
    return bestRejection !== null ? bestRejection : { kind: "miss" }
  }
  if (bestRejection !== null && bestRejection.distance < best.distance) {
    return bestRejection
  }
  return { kind: "pick", candidate: best }
}

/**
 * Handle a canvas click while the delete-segment tool is active: resolve the
 * first or second pick and advance the state machine. All parameters are in
 * ORIGINAL-IMAGE pixels.
 *
 * @param click the click position (image frame)
 * @param click.x click x (image px)
 * @param click.y click y (image px)
 * @param radius max click-to-line distance (image px)
 * @param snapRadius vertex snap / endpoint / pick-separation radius (image px)
 * @param visibility optional visibility filter (hidden labels excluded)
 */
export function handleSegmentDeletePick(
  click: { x: number; y: number },
  radius: number,
  snapRadius: number,
  visibility?: CutVisibilityFilter
): SegmentDeletePickOutcome {
  const phase = getSegmentDeletePhase()
  if (phase !== "awaitFirst" && phase !== "awaitSecond") {
    return "ignored"
  }
  const state = getState()
  if (state.task.config.tracking || visibility?.hideLabels === true) {
    return "miss"
  }
  const itemIndex = state.user.select.item

  if (phase === "awaitFirst") {
    const outcome = scanForPick(
      state,
      itemIndex,
      click,
      radius,
      snapRadius,
      visibility
    )
    if (outcome.kind !== "pick") {
      return outcome.kind
    }
    const { candidate } = outcome
    recordFirstPick({
      itemIndex,
      labelId: candidate.labelId,
      pick1: candidate.pick,
      pick1Point: resolvePickPoint(candidate.points, candidate.pick),
      points: candidate.points
    })
    return "first-picked"
  }

  // awaitSecond
  const data = getPickData()
  if (data === null) {
    return "ignored"
  }
  // The line must be unchanged since pick 1 (an undo/edit invalidates it).
  const current = state.task.items[data.itemIndex]?.labels[data.labelId]
  if (
    data.itemIndex !== itemIndex ||
    current === undefined ||
    !pointsEqual(storedPoints(state, data.itemIndex, data.labelId), data.points)
  ) {
    resetSegmentDelete()
    return "stale"
  }
  const outcome = scanForPick(
    state,
    itemIndex,
    click,
    radius,
    snapRadius,
    visibility,
    data.labelId
  )
  if (outcome.kind === "curve") {
    return "curve"
  }
  if (outcome.kind === "miss" || outcome.kind === "closed") {
    // Not on pick 1's line — was the click on some OTHER polyline?
    const other = scanForPick(
      state,
      itemIndex,
      click,
      radius,
      snapRadius,
      visibility
    )
    if (other.kind === "pick" && other.candidate.labelId !== data.labelId) {
      return "wrong-line"
    }
    return "miss"
  }
  const normalized = normalizeDeletePicks(
    data.points,
    data.pick1,
    outcome.candidate.pick,
    snapRadius
  )
  if (normalized.kind === "too-close") {
    return "too-close"
  }
  const pieces = buildSegmentDeletePieces(
    data.points,
    normalized.first,
    normalized.second
  )
  recordSecondPick(normalized.first, normalized.second, pieces.doomed)
  return "preview-started"
}

/**
 * Materialize a survivor piece as fresh path-point shapes for a label.
 *
 * @param piece the piece's vertices
 * @param labelId the owning label id
 */
function materialize(
  piece: SimplePathPoint2DType[],
  labelId: IdType
): PathPoint2DType[] {
  return piece.map((p) =>
    makePathPoint2D({
      x: p.x,
      y: p.y,
      pointType: p.pointType,
      label: [labelId]
    })
  )
}

/**
 * Commit the pending segment delete (called when the preview timer fires):
 * re-validate the line, build the survivors, dispatch ONE atomic sequential
 * action, and record undo via the existing command kinds — "cut" for two
 * survivors, "edited" for one, "deleted" for none. Resets the tool.
 */
export function commitPendingSegmentDelete(): SegmentDeleteCommitOutcome {
  const data = getPreviewData()
  if (data === null) {
    return "ignored"
  }
  const state = getState()
  const label = state.task.items[data.itemIndex]?.labels[data.labelId]
  if (
    state.user.select.item !== data.itemIndex ||
    label === undefined ||
    !pointsEqual(
      storedPoints(state, data.itemIndex, data.labelId),
      data.points
    )
  ) {
    resetSegmentDelete()
    return "stale"
  }

  const itemIndex = data.itemIndex
  const labelId = data.labelId
  const stored = getShapes(state, itemIndex, labelId) as PathPoint2DType[]
  const before: LineSnapshot = {
    label: _.cloneDeep(label),
    shapes: _.cloneDeep(stored)
  }
  const pieces = buildSegmentDeletePieces(data.points, data.first, data.second)

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

  if (pieces.left !== undefined && pieces.right !== undefined) {
    // Two survivors: identical state shape to a cut.
    const shapesA = materialize(pieces.left, labelId)
    const labelA: LabelType = _.cloneDeep(label)
    labelA.shapes = shapesA.map((s) => s.id)
    labelA.manual = true

    const newLabelId = uid()
    const shapesB = materialize(pieces.right, newLabelId)
    const labelB: LabelType = _.cloneDeep(label)
    labelB.id = newLabelId
    labelB.item = itemIndex
    labelB.track = ""
    labelB.parent = ""
    labelB.children = []
    labelB.shapes = shapesB.map((s) => s.id)
    labelB.manual = true

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
  } else if (pieces.left !== undefined || pieces.right !== undefined) {
    // One survivor (end trim): keeps the original id — an edit.
    const piece = pieces.left !== undefined ? pieces.left : pieces.right
    const shapesA = materialize(piece as SimplePathPoint2DType[], labelId)
    const labelA: LabelType = _.cloneDeep(label)
    labelA.shapes = shapesA.map((s) => s.id)
    labelA.manual = true

    dispatch(
      makeSequential([
        deleteLabel(itemIndex, labelId),
        addLabel(itemIndex, labelA, shapesA)
      ])
    )
    const committed = getState()
    const after: LineSnapshot = {
      label: _.cloneDeep(committed.task.items[itemIndex].labels[labelId]),
      shapes: _.cloneDeep(getShapes(committed, itemIndex, labelId))
    }
    drawHistory.recordEdit(itemIndex, labelId, before, after)
  } else {
    // No survivors: a whole-line delete.
    drawHistory.recordDeletedLine(itemIndex, labelId, before)
    dispatch(deleteLabel(itemIndex, labelId))
  }

  resetSegmentDelete()
  return "deleted"
}
```

- [ ] **Step 4: Run tests / typecheck**

Same jest command as Step 2 (PASS 6 tests, or the documented load-failure + `npx tsc --noEmit` exit 0 with a hand-trace of all 6 tests in the report).

- [ ] **Step 5: Lint and commit**

`npx eslint -c .eslintrc.json --ext .ts app/src/drawable/2d/polyline_segment_delete.ts app/test/drawable/polyline_segment_delete.test.ts` — zero NEW non-prettier errors.

```bash
git add app/src/drawable/2d/polyline_segment_delete.ts app/test/drawable/polyline_segment_delete.test.ts
git commit -m "feat: segment-delete pick handling and atomic commit

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Canvas input wiring — pick clicks, Escape, item-nav reset, cursor

**Files:**
- Modify: `app/src/components/label2d_canvas.tsx`

**Interfaces:**
- Consumes: `isSegmentDeleteActive`, `getSegmentDeletePhase`, `resetSegmentDelete` (Task 3); `handleSegmentDeletePick` (Task 4); existing `CUT_CLICK_RADIUS_PX`, `CUT_SNAP_RADIUS_PX`, `CUT_CURSOR`, `alert`, `Severity`, `Key`.
- Produces: nothing new (Task 6 modifies the same file).

No automated test (component suites are CI-only); verification is `npx tsc --noEmit` + eslint-vs-HEAD. Behavior lands in Task 8 manual QA.

- [ ] **Step 1: Add imports**

In `app/src/components/label2d_canvas.tsx`, after the existing `cut_state` import add:

```ts
import {
  getSegmentDeletePhase,
  isSegmentDeleteActive,
  resetSegmentDelete
} from "../common/segment_delete_state"
import { handleSegmentDeletePick } from "../drawable/2d/polyline_segment_delete"
```

- [ ] **Step 2: Intercept pick clicks in `onMouseDown`**

Directly AFTER the `if (e.ctrlKey || e.metaKey) { return }` block and BEFORE the existing `if (isCutMode())` block, insert (`mousePos` is already in scope):

```ts
    // Delete-segment tool: while active, clicks are picks (or ignored
    // entirely during the preview countdown).
    if (isSegmentDeleteActive()) {
      if (getSegmentDeletePhase() === "preview") {
        return
      }
      const config = this.state.user.viewerConfigs[this.props.id]
      const outcome = handleSegmentDeletePick(
        mousePos,
        CUT_CLICK_RADIUS_PX / this.displayToImageRatio,
        CUT_SNAP_RADIUS_PX / this.displayToImageRatio,
        {
          hideLabels: config.hideLabels,
          hiddenLabelTypes:
            config.hiddenLabelTypes !== undefined
              ? config.hiddenLabelTypes
              : [],
          hiddenCategories:
            config.hiddenCategories !== undefined
              ? config.hiddenCategories
              : []
        }
      )
      switch (outcome) {
        case "curve":
          alert(
            Severity.WARNING,
            "Cannot cut a curved segment — straighten it first."
          )
          break
        case "closed":
          alert(Severity.WARNING, "Delete segment works on open polylines only.")
          break
        case "wrong-line":
          alert(Severity.WARNING, "Pick both points on the same polyline.")
          break
        case "too-close":
          alert(Severity.WARNING, "Picked points are too close.")
          break
        case "stale":
          alert(
            Severity.WARNING,
            "The line changed — segment delete cancelled."
          )
          this.setDefaultCursor()
          break
        case "first-picked":
        case "preview-started":
        case "miss":
        case "ignored":
          break
      }
      return
    }
```

- [ ] **Step 3: Escape disarms in `onKeyDown`**

Directly BEFORE the existing `if (e.key === Key.ESCAPE && isCutMode())` block, insert:

```ts
    if (e.key === Key.ESCAPE && isSegmentDeleteActive()) {
      // Escape cancels the pending delete at any phase — nothing committed.
      resetSegmentDelete()
      this.setDefaultCursor()
      return
    }
```

- [ ] **Step 4: Item navigation resets the tool in `updateState`**

Inside the existing `if (this._cutItemIndex !== state.user.select.item)` block, next to the `setCutMode(false)` call, add:

```ts
        resetSegmentDelete()
```

(so the inner block reads: on a real item change, both tools reset). Update the `_cutItemIndex` field's doc comment to `/** last seen item index, to disarm the cut tools on item navigation */`.

- [ ] **Step 5: Scissors cursor while active in `onMouseMove`**

Change the existing cursor override at the end of `onMouseMove` from `if (isCutMode()) {` to:

```ts
    if (isCutMode() || isSegmentDeleteActive()) {
      // The scissors cursor overrides hover cursors while a tool is armed.
      this.setCursor(CUT_CURSOR)
    }
```

- [ ] **Step 6: Typecheck + lint**

Run: `npx tsc --noEmit` → exit 0. `npx eslint -c .eslintrc.json --ext .tsx app/src/components/label2d_canvas.tsx` → only the pre-existing prettier CRLF noise + 2 dot-notation errors (compare to HEAD if unsure).

- [ ] **Step 7: Commit**

```bash
git add app/src/components/label2d_canvas.tsx
git commit -m "feat: delete-segment pick clicks, Escape and item-nav wiring on the canvas

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Canvas overlay — pick halo, marching-ants preview, 3 s timer

**Files:**
- Modify: `app/src/components/label2d_canvas.tsx`

**Interfaces:**
- Consumes: `getPickData`, `getPreviewData`, `getSegmentDeletePhase`, `onSegmentDeleteChange` (Task 3); `commitPendingSegmentDelete`, `SEGMENT_DELETE_PREVIEW_MS` (Task 4); `DASH_LINE` from `../drawable/2d/common`.
- Produces: nothing new.

No automated test; verification is `npx tsc --noEmit` + eslint-vs-HEAD; behavior lands in Task 8 manual QA.

- [ ] **Step 1: Add imports**

Extend the Task 5 `segment_delete_state` import with `getPickData`, `getPreviewData`, `onSegmentDeleteChange`; extend the `polyline_segment_delete` import with `commitPendingSegmentDelete`, `SEGMENT_DELETE_PREVIEW_MS`; and add:

```ts
import { DASH_LINE } from "../drawable/2d/common"
```

- [ ] **Step 2: Add fields**

Next to `private _menuAnchor ...` add:

```ts
  /** unsubscribe from delete-segment state changes */
  private _offSegmentDelete: (() => void) | null = null
  /** pending commit timer for the delete-segment preview */
  private _segmentDeleteTimer: number | null = null
  /** rAF handle for the marching-ants animation */
  private _antsRAF: number | null = null
  /** marching-ants dash offset (canvas px) */
  private _antsOffset: number = 0
```

- [ ] **Step 3: Subscribe in the lifecycle**

In `componentDidMount`, after the `this._offIdle = onIdle(...)` line, add:

```ts
    this._offSegmentDelete = onSegmentDeleteChange(() =>
      this.onSegmentDeleteStateChange()
    )
```

In `componentWillUnmount`, after the `_offIdle` cleanup block, add:

```ts
    if (this._offSegmentDelete !== null) {
      this._offSegmentDelete()
      this._offSegmentDelete = null
    }
    this.clearSegmentDeleteTimers()
```

- [ ] **Step 4: Add the state-change handler and timer management**

Add these methods after `onContextMenu`:

```ts
  /**
   * React to delete-segment phase changes: start the commit countdown and the
   * marching-ants animation when a preview begins; tear both down on any
   * other transition (cancel, commit, disarm).
   */
  private onSegmentDeleteStateChange(): void {
    if (getSegmentDeletePhase() === "preview") {
      if (this._segmentDeleteTimer === null) {
        this._segmentDeleteTimer = window.setTimeout(() => {
          this._segmentDeleteTimer = null
          const outcome = commitPendingSegmentDelete()
          if (outcome === "stale") {
            alert(
              Severity.WARNING,
              "The line changed — segment delete cancelled."
            )
          }
          this.setDefaultCursor()
        }, SEGMENT_DELETE_PREVIEW_MS)
      }
      if (this._antsRAF === null) {
        const step = (): void => {
          this._antsOffset += 0.75
          this.redraw()
          this._antsRAF =
            getSegmentDeletePhase() === "preview"
              ? window.requestAnimationFrame(step)
              : null
        }
        this._antsRAF = window.requestAnimationFrame(step)
      }
    } else {
      this.clearSegmentDeleteTimers()
      // Repaint to add/remove the pick halo or erase the overlay.
      this.redraw()
    }
  }

  /**
   * Clear the delete-segment preview timer and animation, if running.
   */
  private clearSegmentDeleteTimers(): void {
    if (this._segmentDeleteTimer !== null) {
      window.clearTimeout(this._segmentDeleteTimer)
      this._segmentDeleteTimer = null
    }
    if (this._antsRAF !== null) {
      window.cancelAnimationFrame(this._antsRAF)
      this._antsRAF = null
    }
  }

  /**
   * Draw the delete-segment overlay: a green halo on the first pick and,
   * during the preview, the doomed piece as a green dashed marching-ants
   * path. Drawn after the labels so it always sits on top.
   *
   * @param context the label canvas context
   * @param ratio image-to-canvas scale (displayToImageRatio * upResRatio)
   */
  private drawSegmentDeleteOverlay(context: CanvasRenderingContext2D, ratio: number): void {
    const pickData = getPickData()
    if (pickData === null) {
      return
    }
    context.save()
    // Pick-1 halo (matches the endpoint-snap indicator styling).
    const hx = pickData.pick1Point.x * ratio
    const hy = pickData.pick1Point.y * ratio
    context.beginPath()
    context.strokeStyle = "rgba(0, 255, 0, 0.8)"
    context.fillStyle = "rgba(0, 255, 0, 0.2)"
    context.lineWidth = 2
    context.arc(hx, hy, 12, 0, 2 * Math.PI)
    context.fill()
    context.stroke()
    context.beginPath()
    context.fillStyle = "rgba(0, 255, 0, 0.9)"
    context.arc(hx, hy, 5, 0, 2 * Math.PI)
    context.fill()

    const preview = getPreviewData()
    if (preview !== null && preview.doomed.length >= 2) {
      context.beginPath()
      context.strokeStyle = "rgba(0, 230, 0, 0.95)"
      context.lineWidth = 4
      context.setLineDash(DASH_LINE)
      context.lineDashOffset = -this._antsOffset
      context.moveTo(preview.doomed[0].x * ratio, preview.doomed[0].y * ratio)
      for (let i = 1; i < preview.doomed.length; i++) {
        context.lineTo(preview.doomed[i].x * ratio, preview.doomed[i].y * ratio)
      }
      context.stroke()
    }
    context.restore()
  }
```

- [ ] **Step 5: Call the overlay from `redraw()`**

In `redraw()`, immediately after the `this._labelList.redraw(...)` call (inside the same `if` block), add:

```ts
      this.drawSegmentDeleteOverlay(
        this.labelContext,
        this.displayToImageRatio * this._upResRatio
      )
```

- [ ] **Step 6: Typecheck + lint**

`npx tsc --noEmit` → exit 0. Same eslint bar as Task 5.

- [ ] **Step 7: Commit**

```bash
git add app/src/components/label2d_canvas.tsx
git commit -m "feat: delete-segment halo, marching-ants preview and commit timer

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Entry points — icon, toolbar button, context-menu item

**Files:**
- Modify: `app/src/components/cut_icon.tsx` (append icon)
- Modify: `app/src/components/viewer2d.tsx` (button + subscription)
- Modify: `app/src/components/label2d_canvas.tsx` (menu item)

**Interfaces:**
- Consumes: `armSegmentDelete`, `resetSegmentDelete`, `isSegmentDeleteActive`, `onSegmentDeleteChange` (Task 3); existing `Session.label2dList.isDrawingInProgress()`, `CUT_CURSOR`.
- Produces: `DeleteSegmentIcon(props: SvgIconProps): JSX.Element` from `cut_icon.tsx`.

No automated test; verification is tsc + eslint; behavior in Task 8 manual QA.

- [ ] **Step 1: Add the icon**

Append to `app/src/components/cut_icon.tsx`:

```tsx
/**
 * Glyph for the delete-segment tool: a line with its dashed middle removed
 * (two solid end stubs, two middle dashes). Inlined like CONTENT_CUT_PATH.
 */
export const DELETE_SEGMENT_PATH =
  "M2 11h5v2H2v-2zm15 0h5v2h-5v-2zm-8 0h2v2H9v-2zm4 0h2v2h-2v-2z"

/**
 * Delete-segment icon (toolbar button + context-menu item).
 *
 * @param props standard SvgIcon props (fontSize, style, ...)
 */
export function DeleteSegmentIcon(props: SvgIconProps): JSX.Element {
  return (
    <SvgIcon {...props}>
      <path d={DELETE_SEGMENT_PATH} />
    </SvgIcon>
  )
}
```

- [ ] **Step 2: Toolbar button in `viewer2d.tsx`**

2a. Extend imports: add `armSegmentDelete`, `isSegmentDeleteActive`, `onSegmentDeleteChange`, `resetSegmentDelete` to a new import from `"../common/segment_delete_state"`, and `DeleteSegmentIcon` to the existing `./cut_icon` import.

2b. Add a field next to `_offCutModeChange`:

```ts
  /** unsubscribe from delete-segment state changes */
  private _offSegmentDeleteChange: (() => void) | null = null
```

2c. In `componentDidMount`, after the `_offCutModeChange` line:

```ts
    this._offSegmentDeleteChange = onSegmentDeleteChange(() =>
      this.forceUpdate()
    )
```

In `componentWillUnmount`, after the `_offCutModeChange` cleanup:

```ts
    if (this._offSegmentDeleteChange !== null) {
      this._offSegmentDeleteChange()
      this._offSegmentDeleteChange = null
    }
```

2d. Add the button builder after `getCutButton`:

```tsx
  /**
   * Build the delete-segment toolbar button. Arms the two-pick delete tool;
   * clicking it while armed cancels. Mutually exclusive with the cut tool.
   *
   * @return {JSX.Element} the delete-segment button
   */
  protected getDeleteSegmentButton(): JSX.Element {
    const armed = isSegmentDeleteActive()
    return (
      <Tooltip
        key={`deleteSegment2dButton${this.props.id}`}
        title="Delete segment"
        enterDelay={500}
        TransitionComponent={Fade}
        TransitionProps={{ timeout: 600 }}
        arrow
      >
        <IconButton
          onClick={() => {
            if (armed) {
              resetSegmentDelete()
            } else if (
              !Session.label2dList.isDrawingInProgress() &&
              !this.state.task.config.tracking
            ) {
              armSegmentDelete()
            }
          }}
          className={this.props.classes.viewer_button}
          style={{ color: armed ? "#4caf50" : undefined }}
          edge={"start"}
        >
          <DeleteSegmentIcon />
        </IconButton>
      </Tooltip>
    )
  }
```

2e. In `getMenuComponents`, append `this.getDeleteSegmentButton()` after `this.getCutButton()` in the returned array.

- [ ] **Step 3: Context-menu item in `label2d_canvas.tsx`**

3a. Extend the `./cut_icon` import with `DeleteSegmentIcon`, and the `segment_delete_state` import with `armSegmentDelete`.

3b. In `render()`, inside the existing `<Menu>` after the "Cut polyline" `MenuItem`, add:

```tsx
        <MenuItem
          dense
          disabled={
            Session.label2dList.isDrawingInProgress() ||
            this.state.task.config.tracking
          }
          onClick={() => {
            this._menuAnchor = null
            armSegmentDelete()
            this.setCursor(CUT_CURSOR)
            this.forceUpdate()
          }}
        >
          <DeleteSegmentIcon fontSize="small" style={{ marginRight: 8 }} />
          Delete segment
        </MenuItem>
```

- [ ] **Step 4: Typecheck + lint**

`npx tsc --noEmit` → exit 0. `npx eslint -c .eslintrc.json --ext .ts,.tsx app/src/components/cut_icon.tsx app/src/components/viewer2d.tsx app/src/components/label2d_canvas.tsx` → no NEW non-prettier errors vs HEAD.

- [ ] **Step 5: Commit**

```bash
git add app/src/components/cut_icon.tsx app/src/components/viewer2d.tsx app/src/components/label2d_canvas.tsx
git commit -m "feat: delete-segment toolbar button, icon and context-menu item

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Full verification + docs

**Files:**
- Modify: `docs/polyline-feature-map.md`

- [ ] **Step 1: Run the full local verification suite**

```bash
npx tsc --noEmit
```
Expected: exit 0.

```bash
npx jest app/test/common/ app/test/drawable/polyline_cut_geometry.test.ts --env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js --coverage=false
```
Expected: PASS — cut_state (2) + segment_delete_state (3) + geometry (27) = 32 tests.

```bash
npx jest app/test/drawable/polyline_segment_delete.test.ts app/test/drawable/polyline_cut.test.ts app/test/drawable/draw_history_cut.test.ts --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js --coverage=false
```
Expected: PASS in CI; locally the documented native-canvas load failure is acceptable — record the failure mode.

Run eslint over every file this plan touched; the bar is zero NEW non-prettier errors vs the pre-feature commit.

- [ ] **Step 2: Manual QA (deferred to the human — list for the report)**

1. Toolbar: new dashed-segment button after the scissor; arming tints it green and shows the scissors cursor; arming it while cut is armed un-tints the scissor button (and vice versa).
2. Pick 1 on a line → green halo appears and stays; pick 2 elsewhere on the same line → green dashed marching-ants on the doomed piece for ~3 s → piece disappears; two independent lines remain; tool disarmed.
3. One Ctrl+Z restores the original; Ctrl+Y re-applies.
4. End trim: pick 1 near a line end + pick 2 mid-line → line trimmed, id preserved (selection/category unchanged); undo restores.
5. Both ends → whole line deleted; undo restores.
6. Rejection toasts: pick 2 on a different line, picks too close, curved segment, closed polygon.
7. Escape during awaitFirst/awaitSecond/preview → nothing deleted, overlay gone, button un-tints.
8. Navigate items during preview → cancelled, nothing deleted.
9. Ctrl+Z during preview (changes the line) → after 3 s: "line changed" toast, nothing deleted.
10. Hidden category line near the picks is never picked.
11. Zoom/pan during preview → overlay tracks the line correctly.

- [ ] **Step 3: Update the feature map**

In `docs/polyline-feature-map.md`:
- §1 (drawable) after the polyline_cut bullets, add:
  ```
  - `app/src/drawable/2d/polyline_segment_delete.ts` — delete-segment tool:
    `handleSegmentDeletePick` (two picks; end picks = trims),
    `commitPendingSegmentDelete` (atomic; records cut/edited/deleted per
    outcome). Geometry: `buildSegmentDeletePieces`/`normalizeDeletePicks` in
    `polyline_cut_geometry.ts`.
  ```
- §7 (toolbar) extend the cut-tool bullet with:
  ```
  `app/src/common/segment_delete_state.ts` — delete-segment phase machine
  (awaitFirst/awaitSecond/preview, mutually exclusive with cut mode);
  toolbar button in `viewer2d.tsx getDeleteSegmentButton`; picks/overlay/
  timer wiring in `label2d_canvas.tsx` (marching-ants preview, 3 s commit).
  ```

- [ ] **Step 4: Commit**

```bash
git add docs/polyline-feature-map.md
git commit -m "docs: map entries for the delete-segment tool

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
