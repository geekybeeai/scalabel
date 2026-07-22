# Curve Cutting via Bezier Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The scissor cut tool splits curved (cubic bezier) polyline spans at the clicked point via de Casteljau subdivision; the two halves jointly render pixel-identical to the original.

**Architecture:** All new math is pure and lives in `app/src/drawable/2d/polyline_cut_geometry.ts` (node-env jest testable). `findCutSite` gains an opt-in `{ splitCurves }` option used only by the cut tool's `performCut`; delete-segment call sites are untouched and keep rejecting curves. Spec (read it first): `docs/superpowers/specs/2026-07-22-curve-cut-bezier-split-design.md`.

**Tech Stack:** TypeScript; jest with the repo's node-env recipe for pure modules; `verify` skill (headless Chrome CDP) for runtime acceptance.

## Global Constraints

- `polyline_cut_geometry.ts` must stay **pure** — no Session/DOM imports (its only new import is `./curve_groups`, also pure).
- **Delete-segment behavior must remain byte-identical**: no call site outside `performCut` may pass `splitCurves`; the option defaults to `false`.
- The cut switch's `"curve"` toast case in `label2d_canvas.tsx` **stays** (reachable malformed-group fallback). Do not remove it.
- Guards use the existing radii: `CUT_CLICK_RADIUS_PX = 20`, `CUT_SNAP_RADIUS_PX = 8` (screen px, converted by the caller) — no new constants.
- All math in original-image coordinates (golden rule); no UI changes.
- Pure-test command (from repo root): `npx jest app/test/drawable/polyline_cut_geometry.test.ts --env=node --globalSetup=app/test/setup/noop.js --globalTeardown=app/test/setup/noop.js`. Do NOT run the drawable/component suites (native canvas + redis are unavailable here).
- Lint caveat: compare only **non-prettier** violations against HEAD (pervasive pre-existing CRLF `prettier/prettier` noise on Windows checkouts).
- Commit messages end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: Pure bezier helpers — `splitCubicBezier` + `nearestTOnCubic`

**Files:**
- Modify: `app/src/drawable/2d/polyline_cut_geometry.ts` (insert after `projectOntoSegment`, ~line 68)
- Test: `app/test/drawable/polyline_cut_geometry.test.ts`

**Interfaces:**
- Consumes: nothing new.
- Produces (Tasks 2-3 rely on these exact names):
  - `export interface Point2D { x: number; y: number }`
  - `export interface CubicSplit { left: { c1: Point2D; c2: Point2D }; point: Point2D; right: { c1: Point2D; c2: Point2D } }`
  - `export function splitCubicBezier(a0: Point2D, c1: Point2D, c2: Point2D, a1: Point2D, t: number): CubicSplit`
  - `export interface NearestOnCubic { t: number; point: Point2D; distance: number }`
  - `export function nearestTOnCubic(a0: Point2D, c1: Point2D, c2: Point2D, a1: Point2D, click: Point2D): NearestOnCubic`

- [ ] **Step 1: Write the failing tests**

In `app/test/drawable/polyline_cut_geometry.test.ts`, extend the import from `../../src/drawable/2d/polyline_cut_geometry` with `splitCubicBezier, nearestTOnCubic`, then add at the end of the file:

```ts
/**
 * Evaluate a cubic bezier at t (Bernstein form) — test-side oracle.
 *
 * @param a0 start anchor
 * @param c1 first control point
 * @param c2 second control point
 * @param a1 end anchor
 * @param t curve parameter in [0, 1]
 */
function evalAt(
  a0: { x: number; y: number },
  c1: { x: number; y: number },
  c2: { x: number; y: number },
  a1: { x: number; y: number },
  t: number
): { x: number; y: number } {
  const s = 1 - t
  const w0 = s * s * s
  const w1 = 3 * s * s * t
  const w2 = 3 * s * t * t
  const w3 = t * t * t
  return {
    x: w0 * a0.x + w1 * c1.x + w2 * c2.x + w3 * a1.x,
    y: w0 * a0.y + w1 * c1.y + w2 * c2.y + w3 * a1.y
  }
}

describe("splitCubicBezier", () => {
  const a0 = pt(0, 0)
  const c1 = pt(30, 60, PathPointType.CURVE)
  const c2 = pt(70, 60, PathPointType.CURVE)
  const a1 = pt(100, 0)

  test("t=0.5 splits a symmetric curve symmetrically", () => {
    const split = splitCubicBezier(a0, c1, c2, a1, 0.5)
    expect(split.point.x).toBeCloseTo(50)
    expect(split.point.y).toBeCloseTo(45)
    expect(split.left.c1).toEqual({ x: 15, y: 30 })
    expect(split.right.c2).toEqual({ x: 85, y: 30 })
  })

  test("halves jointly trace the original cubic", () => {
    const t0 = 0.3
    const split = splitCubicBezier(a0, c1, c2, a1, t0)
    for (const u of [0, 0.1, 0.2, 0.3]) {
      const orig = evalAt(a0, c1, c2, a1, u)
      const left = evalAt(a0, split.left.c1, split.left.c2, split.point, u / t0)
      expect(left.x).toBeCloseTo(orig.x, 6)
      expect(left.y).toBeCloseTo(orig.y, 6)
    }
    for (const u of [0.3, 0.5, 0.8, 1]) {
      const orig = evalAt(a0, c1, c2, a1, u)
      const right = evalAt(
        split.point,
        split.right.c1,
        split.right.c2,
        a1,
        (u - t0) / (1 - t0)
      )
      expect(right.x).toBeCloseTo(orig.x, 6)
      expect(right.y).toBeCloseTo(orig.y, 6)
    }
  })
})

describe("nearestTOnCubic", () => {
  const a0 = pt(0, 0)
  const c1 = pt(30, 60, PathPointType.CURVE)
  const c2 = pt(70, 60, PathPointType.CURVE)
  const a1 = pt(100, 0)

  test("recovers t for an on-curve click", () => {
    const target = evalAt(a0, c1, c2, a1, 0.4)
    const near = nearestTOnCubic(a0, c1, c2, a1, target)
    expect(near.distance).toBeLessThan(0.01)
    expect(near.t).toBeCloseTo(0.4, 2)
  })

  test("finds the nearest curve point for an off-curve click", () => {
    // The symmetric curve's apex is (50, 45); click straight above it.
    const near = nearestTOnCubic(a0, c1, c2, a1, { x: 50, y: 55 })
    expect(near.point.x).toBeCloseTo(50, 1)
    expect(near.point.y).toBeCloseTo(45, 1)
    expect(near.distance).toBeCloseTo(10, 1)
  })

  test("clamps to the ends for clicks beyond them", () => {
    const nearStart = nearestTOnCubic(a0, c1, c2, a1, { x: -20, y: -5 })
    expect(nearStart.t).toBeLessThan(0.05)
    const nearEnd = nearestTOnCubic(a0, c1, c2, a1, { x: 120, y: -5 })
    expect(nearEnd.t).toBeGreaterThan(0.95)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx jest app/test/drawable/polyline_cut_geometry.test.ts --env=node --globalSetup=app/test/setup/noop.js --globalTeardown=app/test/setup/noop.js`
Expected: FAIL — the module has no export named `splitCubicBezier` / `nearestTOnCubic` (TS compile error in the test file).

- [ ] **Step 3: Implement the helpers**

In `app/src/drawable/2d/polyline_cut_geometry.ts`, insert directly after the `projectOntoSegment` function:

```ts
/** A plain 2D coordinate. */
export interface Point2D {
  x: number
  y: number
}

/**
 * Linear interpolation between two points.
 *
 * @param a start point
 * @param b end point
 * @param t interpolation parameter in [0, 1]
 */
function lerp(a: Point2D, b: Point2D, t: number): Point2D {
  return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t }
}

/**
 * Evaluate a cubic bezier at t (Bernstein form).
 *
 * @param a0 start anchor
 * @param c1 first control point
 * @param c2 second control point
 * @param a1 end anchor
 * @param t curve parameter in [0, 1]
 */
function evalCubic(
  a0: Point2D,
  c1: Point2D,
  c2: Point2D,
  a1: Point2D,
  t: number
): Point2D {
  const s = 1 - t
  const w0 = s * s * s
  const w1 = 3 * s * s * t
  const w2 = 3 * s * t * t
  const w3 = t * t * t
  return {
    x: w0 * a0.x + w1 * c1.x + w2 * c2.x + w3 * a1.x,
    y: w0 * a0.y + w1 * c1.y + w2 * c2.y + w3 * a1.y
  }
}

/** Result of splitting a cubic bezier at a parameter t. */
export interface CubicSplit {
  /** left half's control points (anchors: original a0 .. point) */
  left: { c1: Point2D; c2: Point2D }
  /** the on-curve split point */
  point: Point2D
  /** right half's control points (anchors: point .. original a1) */
  right: { c1: Point2D; c2: Point2D }
}

/**
 * Split a cubic bezier at parameter t (de Casteljau). The two halves
 * jointly trace exactly the original curve.
 *
 * @param a0 start anchor
 * @param c1 first control point
 * @param c2 second control point
 * @param a1 end anchor
 * @param t split parameter in [0, 1]
 */
export function splitCubicBezier(
  a0: Point2D,
  c1: Point2D,
  c2: Point2D,
  a1: Point2D,
  t: number
): CubicSplit {
  const p01 = lerp(a0, c1, t)
  const p12 = lerp(c1, c2, t)
  const p23 = lerp(c2, a1, t)
  const p012 = lerp(p01, p12, t)
  const p123 = lerp(p12, p23, t)
  const point = lerp(p012, p123, t)
  return {
    left: { c1: p01, c2: p012 },
    point,
    right: { c1: p123, c2: p23 }
  }
}

/** Nearest point on a cubic bezier to a query point. */
export interface NearestOnCubic {
  /** curve parameter of the nearest point */
  t: number
  /** the nearest point on the curve */
  point: Point2D
  /** distance from the query point (image px) */
  distance: number
}

/** Coarse samples for the nearest-t search. */
const NEAREST_T_SAMPLES = 32
/** Ternary-search refinement iterations around the best sample. */
const NEAREST_T_REFINEMENTS = 24

/**
 * Find the point on a cubic bezier nearest to a click: coarse sampling
 * followed by ternary-search refinement in the bracket around the best
 * sample. Sub-pixel accurate at annotation scales.
 *
 * @param a0 start anchor
 * @param c1 first control point
 * @param c2 second control point
 * @param a1 end anchor
 * @param click the query point
 */
export function nearestTOnCubic(
  a0: Point2D,
  c1: Point2D,
  c2: Point2D,
  a1: Point2D,
  click: Point2D
): NearestOnCubic {
  let bestT = 0
  let bestD = Number.POSITIVE_INFINITY
  for (let i = 0; i <= NEAREST_T_SAMPLES; i++) {
    const t = i / NEAREST_T_SAMPLES
    const p = evalCubic(a0, c1, c2, a1, t)
    const d = Math.hypot(click.x - p.x, click.y - p.y)
    if (d < bestD) {
      bestD = d
      bestT = t
    }
  }
  let lo = Math.max(0, bestT - 1 / NEAREST_T_SAMPLES)
  let hi = Math.min(1, bestT + 1 / NEAREST_T_SAMPLES)
  for (let i = 0; i < NEAREST_T_REFINEMENTS; i++) {
    const m1 = lo + (hi - lo) / 3
    const m2 = hi - (hi - lo) / 3
    const p1 = evalCubic(a0, c1, c2, a1, m1)
    const p2 = evalCubic(a0, c1, c2, a1, m2)
    const d1 = Math.hypot(click.x - p1.x, click.y - p1.y)
    const d2 = Math.hypot(click.x - p2.x, click.y - p2.y)
    if (d1 <= d2) {
      hi = m2
    } else {
      lo = m1
    }
  }
  const t = (lo + hi) / 2
  const point = evalCubic(a0, c1, c2, a1, t)
  return {
    t,
    point,
    distance: Math.hypot(click.x - point.x, click.y - point.y)
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx jest app/test/drawable/polyline_cut_geometry.test.ts --env=node --globalSetup=app/test/setup/noop.js --globalTeardown=app/test/setup/noop.js`
Expected: PASS (all pre-existing tests in the file too).

- [ ] **Step 5: Commit**

```bash
git add app/src/drawable/2d/polyline_cut_geometry.ts app/test/drawable/polyline_cut_geometry.test.ts
git commit -m "feat: pure cubic bezier split and nearest-t helpers

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `findCutSite` resolves curve clicks to split sites (opt-in)

**Files:**
- Modify: `app/src/drawable/2d/polyline_cut_geometry.ts` (`CutSite` interface, `findCutSite` body, new private `findCurveSite`)
- Test: `app/test/drawable/polyline_cut_geometry.test.ts`

**Interfaces:**
- Consumes: `nearestTOnCubic` (Task 1); `curveGroupIndices(types: readonly PathPointType[], closed?: boolean): number[][]` from `./curve_groups` (existing; returns `[a0, c1, c2, a1]` index quadruples).
- Produces (Task 3-4 rely on these):
  - `CutSite` gains `curveSplit?: { groupStart: number; t: number }`
  - `export interface FindCutSiteOptions { splitCurves?: boolean }`
  - `findCutSite(points, click, radius, snapRadius, options?: FindCutSiteOptions): CutSiteResult`

- [ ] **Step 1: Write the failing tests**

Add to `app/test/drawable/polyline_cut_geometry.test.ts`:

```ts
describe("findCutSite on curved spans (splitCurves)", () => {
  const RADIUS = 10
  const SNAP = 8
  // Straight lead-in, one bezier group [1..4], straight tail. The group's
  // symmetric apex is at (150, 45).
  const points = [
    pt(0, 0),
    pt(100, 0),
    pt(130, 60, PathPointType.CURVE),
    pt(170, 60, PathPointType.CURVE),
    pt(200, 0),
    pt(300, 0)
  ]

  test("without the flag a curve click still rejects as curve", () => {
    // (150, 52): 8 px from the control-polygon span, within RADIUS.
    const result = findCutSite(points, { x: 150, y: 52 }, RADIUS, SNAP)
    expect(result.kind).toBe("curve")
  })

  test("mid-curve click yields a site carrying curveSplit", () => {
    const result = findCutSite(points, { x: 150, y: 52 }, RADIUS, SNAP, {
      splitCurves: true
    })
    expect(result.kind).toBe("site")
    if (result.kind === "site") {
      expect(result.site.curveSplit).toBeDefined()
      expect(result.site.curveSplit?.groupStart).toBe(1)
      expect(result.site.curveSplit?.t).toBeCloseTo(0.5, 1)
      expect(result.site.segmentIndex).toBe(1)
      expect(result.site.snappedVertexIndex).toBeNull()
      expect(result.site.point.x).toBeCloseTo(150, 0)
      expect(result.site.point.y).toBeCloseTo(45, 0)
      expect(result.site.distance).toBeCloseTo(7, 0)
    }
  })

  test("radius is measured against the true curve, not the polygon", () => {
    // (150, 58): only 2 px from the polygon span but 13 px from the curve.
    const result = findCutSite(points, { x: 150, y: 58 }, RADIUS, SNAP, {
      splitCurves: true
    })
    expect(result.kind).toBe("miss")
  })

  test("a click near a group anchor snaps to it (no curveSplit)", () => {
    const result = findCutSite(points, { x: 198, y: 6 }, RADIUS, SNAP, {
      splitCurves: true
    })
    expect(result.kind).toBe("site")
    if (result.kind === "site") {
      expect(result.site.snappedVertexIndex).toBe(4)
      expect(result.site.curveSplit).toBeUndefined()
      expect(result.site.point).toEqual({ x: 200, y: 0 })
    }
  })

  test("snap onto the line's end anchor rejects as near-endpoint", () => {
    // A fully-curved 2-anchor line; click near its start anchor.
    const curveOnly = [
      pt(0, 0),
      pt(30, 60, PathPointType.CURVE),
      pt(70, 60, PathPointType.CURVE),
      pt(100, 0)
    ]
    const result = findCutSite(curveOnly, { x: 2, y: 4 }, RADIUS, SNAP, {
      splitCurves: true
    })
    expect(result.kind).toBe("near-endpoint")
  })

  test("mid-curve cut on a fully-curved 2-anchor line works", () => {
    const curveOnly = [
      pt(0, 0),
      pt(30, 60, PathPointType.CURVE),
      pt(70, 60, PathPointType.CURVE),
      pt(100, 0)
    ]
    const result = findCutSite(curveOnly, { x: 50, y: 48 }, RADIUS, SNAP, {
      splitCurves: true
    })
    expect(result.kind).toBe("site")
    if (result.kind === "site") {
      expect(result.site.curveSplit?.groupStart).toBe(0)
    }
  })

  test("malformed curve data falls back to the curve rejection", () => {
    // A stray single CURVE point: no well-formed group.
    const malformed = [pt(0, 0), pt(50, 10, PathPointType.CURVE), pt(100, 0)]
    const result = findCutSite(malformed, { x: 25, y: 8 }, RADIUS, SNAP, {
      splitCurves: true
    })
    expect(result.kind).toBe("curve")
  })
})
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `npx jest app/test/drawable/polyline_cut_geometry.test.ts --env=node --globalSetup=app/test/setup/noop.js --globalTeardown=app/test/setup/noop.js`
Expected: FAIL — `findCutSite` takes 4 arguments (TS error), or the `splitCurves` tests return `"curve"`/`"miss"` instead of sites.

- [ ] **Step 3: Implement**

In `app/src/drawable/2d/polyline_cut_geometry.ts`:

(a) Add the import at the top (below the existing types import):

```ts
import { curveGroupIndices } from "./curve_groups"
```

(b) Extend `CutSite` — add one field before the closing brace:

```ts
  /** Present for a mid-curve cut: the bezier group start (its a0 index)
   * and the split parameter for splitCubicBezier. */
  curveSplit?: { groupStart: number; t: number }
```

(c) Add the options interface directly above `findCutSite`:

```ts
/** Options for findCutSite. */
export interface FindCutSiteOptions {
  /** Resolve clicks on curved spans to bezier split sites (the cut tool).
   * Default false: curved spans reject as "curve" (delete-segment). */
  splitCurves?: boolean
}
```

(d) Change `findCutSite`'s signature to accept the options:

```ts
export function findCutSite(
  points: readonly SimplePathPoint2DType[],
  click: { x: number; y: number },
  radius: number,
  snapRadius: number,
  options?: FindCutSiteOptions
): CutSiteResult {
```

(e) Replace this exact block inside `findCutSite`:

```ts
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
```

with:

```ts
  if (best === null) {
    return { kind: "miss" }
  }
  // A span is only directly cuttable when both ends are plain LINE
  // vertices; CURVE (or any other) point types mark bezier control spans.
  const curveSpan =
    points[bestIndex].pointType !== PathPointType.LINE ||
    points[bestIndex + 1].pointType !== PathPointType.LINE
  if (curveSpan && options?.splitCurves === true) {
    // The bezier itself, not its control polygon, is the click target —
    // findCurveSite re-checks radius/snap against true curve distance.
    return findCurveSite(
      points,
      click,
      radius,
      snapRadius,
      bestIndex,
      best.dist
    )
  }
  if (best.dist > radius) {
    return { kind: "miss" }
  }
  if (curveSpan) {
    return { kind: "curve", distance: best.dist }
  }
```

(f) Add `findCurveSite` directly below `findCutSite`:

```ts
/**
 * Resolve a click on a bezier control span to a cut site on the actual
 * curve. Guards mirror the straight-span rules but measure true distance
 * to the cubic: farther than `radius` → miss; nearest curve point within
 * `snapRadius` of a group anchor → snap to that anchor (or the
 * near-endpoint rejection at the line's first/last vertex); otherwise a
 * mid-curve site carrying `curveSplit` for buildCutHalves.
 *
 * @param points the polyline's stored vertices
 * @param click the click position
 * @param radius max click-to-curve distance (image px)
 * @param snapRadius anchor snap / endpoint-guard distance (image px)
 * @param spanIndex the nearest control-polygon span's start index
 * @param spanDist the click's distance to that span (for the fallback)
 */
function findCurveSite(
  points: readonly SimplePathPoint2DType[],
  click: { x: number; y: number },
  radius: number,
  snapRadius: number,
  spanIndex: number,
  spanDist: number
): CutSiteResult {
  const types = points.map((p) => p.pointType)
  const group = curveGroupIndices(types).find(
    (g) => spanIndex >= g[0] && spanIndex < g[3]
  )
  if (group === undefined) {
    // Malformed curve data (stray control points): keep the old rejection.
    return { kind: "curve", distance: spanDist }
  }
  const [a0, i1, i2, a1] = group
  const near = nearestTOnCubic(
    points[a0],
    points[i1],
    points[i2],
    points[a1],
    click
  )
  if (near.distance > radius) {
    return { kind: "miss" }
  }
  const dA0 = Math.hypot(
    near.point.x - points[a0].x,
    near.point.y - points[a0].y
  )
  const dA1 = Math.hypot(
    near.point.x - points[a1].x,
    near.point.y - points[a1].y
  )
  let snapped: number | null = null
  if (dA0 <= snapRadius && dA0 <= dA1) {
    snapped = a0
  } else if (dA1 <= snapRadius) {
    snapped = a1
  }
  if (snapped !== null && (snapped === 0 || snapped === points.length - 1)) {
    return {
      kind: "near-endpoint",
      distance: near.distance,
      endpointIndex: snapped
    }
  }
  if (snapped !== null) {
    return {
      kind: "site",
      site: {
        segmentIndex: a0,
        point: { x: points[snapped].x, y: points[snapped].y },
        snappedVertexIndex: snapped,
        distance: near.distance
      }
    }
  }
  return {
    kind: "site",
    site: {
      segmentIndex: a0,
      point: near.point,
      snappedVertexIndex: null,
      distance: near.distance,
      curveSplit: { groupStart: a0, t: near.t }
    }
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx jest app/test/drawable/polyline_cut_geometry.test.ts --env=node --globalSetup=app/test/setup/noop.js --globalTeardown=app/test/setup/noop.js`
Expected: PASS, including all pre-existing tests (flag-off behavior unchanged).

- [ ] **Step 5: Verify no other caller passes the option**

Run: `grep -rn "findCutSite(" app/src`
Expected: exactly the definition in `polyline_cut_geometry.ts`, the call in `polyline_cut.ts`, and call(s) in `polyline_segment_delete.ts` — none of them passing a 5th argument yet.

- [ ] **Step 6: Commit**

```bash
git add app/src/drawable/2d/polyline_cut_geometry.ts app/test/drawable/polyline_cut_geometry.test.ts
git commit -m "feat: findCutSite resolves curve clicks to bezier split sites (opt-in)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `buildCutHalves` assembles bezier halves

**Files:**
- Modify: `app/src/drawable/2d/polyline_cut_geometry.ts` (`buildCutHalves`)
- Test: `app/test/drawable/polyline_cut_geometry.test.ts`

**Interfaces:**
- Consumes: `splitCubicBezier`, `Point2D` (Task 1); `CutSite.curveSplit` (Task 2).
- Produces: `buildCutHalves` (unchanged signature) now handles curve sites; Task 4 relies on this transparently.

- [ ] **Step 1: Write the failing test**

Add to `app/test/drawable/polyline_cut_geometry.test.ts` (uses `evalAt` from Task 1):

```ts
describe("buildCutHalves on a curve split", () => {
  const points = [
    pt(0, 0),
    pt(100, 0),
    pt(130, 60, PathPointType.CURVE),
    pt(170, 60, PathPointType.CURVE),
    pt(200, 0),
    pt(300, 0)
  ]

  test("assembles bezier halves that jointly trace the original", () => {
    const result = findCutSite(points, { x: 150, y: 52 }, 10, 8, {
      splitCurves: true
    })
    expect(result.kind).toBe("site")
    if (result.kind !== "site") {
      return
    }
    const halves = buildCutHalves(points, result.site)
    expect(halves.first.map((p) => p.pointType)).toEqual([
      PathPointType.LINE,
      PathPointType.LINE,
      PathPointType.CURVE,
      PathPointType.CURVE,
      PathPointType.LINE
    ])
    expect(halves.second.map((p) => p.pointType)).toEqual([
      PathPointType.LINE,
      PathPointType.CURVE,
      PathPointType.CURVE,
      PathPointType.LINE,
      PathPointType.LINE
    ])
    const pA = halves.first[halves.first.length - 1]
    const pB = halves.second[0]
    expect(pA.x).toBeCloseTo(result.site.point.x)
    expect(pA.y).toBeCloseTo(result.site.point.y)
    expect(pB.x).toBeCloseTo(pA.x)
    expect(pB.y).toBeCloseTo(pA.y)
    const t0 = result.site.curveSplit?.t ?? 0
    for (const u of [0.1, 0.25, 0.45, 0.7, 0.9]) {
      const orig = evalAt(points[1], points[2], points[3], points[4], u)
      const h =
        u <= t0
          ? evalAt(
              halves.first[1],
              halves.first[2],
              halves.first[3],
              halves.first[4],
              u / t0
            )
          : evalAt(
              halves.second[0],
              halves.second[1],
              halves.second[2],
              halves.second[3],
              (u - t0) / (1 - t0)
            )
      expect(h.x).toBeCloseTo(orig.x, 4)
      expect(h.y).toBeCloseTo(orig.y, 4)
    }
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx jest app/test/drawable/polyline_cut_geometry.test.ts --env=node --globalSetup=app/test/setup/noop.js --globalTeardown=app/test/setup/noop.js`
Expected: FAIL — `buildCutHalves` treats the curve site as a mid-segment cut on span 1→2 (wrong point types/counts in the halves).

- [ ] **Step 3: Implement**

In `buildCutHalves` (`app/src/drawable/2d/polyline_cut_geometry.ts`), insert between the existing `snappedVertexIndex` branch and the mid-segment tail:

```ts
  if (site.curveSplit !== undefined) {
    const { groupStart, t } = site.curveSplit
    const split = splitCubicBezier(
      points[groupStart],
      points[groupStart + 1],
      points[groupStart + 2],
      points[groupStart + 3],
      t
    )
    const curvePoint = (p: Point2D): SimplePathPoint2DType => ({
      x: p.x,
      y: p.y,
      pointType: PathPointType.CURVE
    })
    const splitPoint: SimplePathPoint2DType = {
      x: split.point.x,
      y: split.point.y,
      pointType: PathPointType.LINE
    }
    return {
      first: [
        ...points.slice(0, groupStart + 1).map(copy),
        curvePoint(split.left.c1),
        curvePoint(split.left.c2),
        { ...splitPoint }
      ],
      second: [
        { ...splitPoint },
        curvePoint(split.right.c1),
        curvePoint(split.right.c2),
        ...points.slice(groupStart + 3).map(copy)
      ]
    }
  }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx jest app/test/drawable/polyline_cut_geometry.test.ts --env=node --globalSetup=app/test/setup/noop.js --globalTeardown=app/test/setup/noop.js`
Expected: PASS (whole file).

- [ ] **Step 5: Commit**

```bash
git add app/src/drawable/2d/polyline_cut_geometry.ts app/test/drawable/polyline_cut_geometry.test.ts
git commit -m "feat: buildCutHalves assembles bezier halves for curve splits

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Wire the cut tool — `performCut` opts in

**Files:**
- Modify: `app/src/drawable/2d/polyline_cut.ts` (the `findCutSite` call in `performCut`, ~line 123)

**Interfaces:**
- Consumes: `FindCutSiteOptions` (Task 2) — via the existing `findCutSite` import.
- Produces: the user-facing feature. No signature changes anywhere.

- [ ] **Step 1: Pass the flag**

In `performCut`, change:

```ts
    const result = findCutSite(scanPoints, click, radius, snapRadius)
```

to:

```ts
    const result = findCutSite(scanPoints, click, radius, snapRadius, {
      splitCurves: true
    })
```

Do NOT touch `label2d_canvas.tsx` — the cut switch's `"curve"` case stays (malformed-group fallback, per spec).

- [ ] **Step 2: Type-check and lint**

Run: `npx tsc --noEmit`
Expected: exit 0.
Run: `npx eslint app/src/drawable/2d/polyline_cut.ts app/src/drawable/2d/polyline_cut_geometry.ts 2>&1 | grep -v "prettier/prettier"`
Expected: no new non-prettier violations vs HEAD.

- [ ] **Step 3: Run the pure suite once more**

Run: `npx jest app/test/drawable/polyline_cut_geometry.test.ts --env=node --globalSetup=app/test/setup/noop.js --globalTeardown=app/test/setup/noop.js`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add app/src/drawable/2d/polyline_cut.ts
git commit -m "feat: cut tool cuts curved segments via bezier split

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Feature-map doc update

**Files:**
- Modify: `docs/polyline-feature-map.md` (section 1 entries for `polyline_cut_geometry.ts` and `polyline_cut.ts`)

**Interfaces:** none (documentation).

- [ ] **Step 1: Update the two entries**

Find:

```markdown
- `app/src/drawable/2d/polyline_cut_geometry.ts` — pure cut-site math:
  `findCutSite` (nearest-span projection, curve/endpoint guards, vertex snap),
  `buildCutHalves`. Also the delete-segment geometry: `DeleteSitePick`,
  `normalizeDeletePicks`, `buildSegmentDeletePieces` (survivors + doomed path).
  No Session/DOM imports — testable with the node-env recipe.
- `app/src/drawable/2d/polyline_cut.ts` — `performCut` (scan open polylines →
  split → delete+add original id, add new label → `drawHistory.recordCut`).
```

Replace with:

```markdown
- `app/src/drawable/2d/polyline_cut_geometry.ts` — pure cut-site math:
  `findCutSite` (nearest-span projection, endpoint guards, vertex snap; the
  opt-in `splitCurves` option resolves curve clicks to bezier split sites
  via `nearestTOnCubic` + `curveGroupIndices` — without it curve spans
  reject, which is what delete-segment relies on), `buildCutHalves`
  (assembles `splitCubicBezier` de Casteljau halves for curve sites). Also
  the delete-segment geometry: `DeleteSitePick`, `normalizeDeletePicks`,
  `buildSegmentDeletePieces` (survivors + doomed path).
  No Session/DOM imports — testable with the node-env recipe.
- `app/src/drawable/2d/polyline_cut.ts` — `performCut` (scan open polylines →
  split → delete+add original id, add new label → `drawHistory.recordCut`;
  passes `splitCurves: true`, so curved spans are cut by bezier split).
```

- [ ] **Step 2: Commit**

```bash
git add docs/polyline-feature-map.md
git commit -m "docs: feature map notes curve cutting via bezier split

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Runtime verification (acceptance gate)

**Files:** none modified. If a scenario fails, fix through the relevant earlier task and re-run.

**Interfaces:** consumes the running app with Tasks 1-5 applied.

- [ ] **Step 1: Invoke the repo `verify` skill** (headless Chrome CDP) with the scenarios below.

- [ ] **Step 2: Mid-curve cut** — draw a 3-vertex polyline; curve its first segment (press-release C, click the MID handle, drag); screenshot; arm the scissors (toolbar); click mid-curve.
Expected: two labels; the joint render visually matches the pre-cut screenshot (same curve silhouette, new anchor at the click); no toast.

- [ ] **Step 3: Undo/redo** — Ctrl+Z → the original single curved line returns; Ctrl+Y → the split reappears. One press each (atomic).

- [ ] **Step 4: Anchor snap + endpoint guard** — click within ~8 px of the curve group's interior anchor → cut lands exactly on the anchor. On a 2-vertex fully-curved line, click near an end anchor → "Too close to an endpoint to cut." toast, tool stays armed.

- [ ] **Step 5: Regressions** — straight-segment cut still works; a click 15+ px from everything is a silent miss (stays armed); delete-segment pick on a curved span still shows "Cannot cut a curved segment." and cut/delete-segment mutual exclusion still holds.

- [ ] **Step 6: Final sweep** — `npx tsc --noEmit` (exit 0); the pure jest suite (PASS); report all scenario outcomes with pass/fail.

---

## Self-Review (completed at write time)

- **Spec coverage:** bezier split + nearest-t (Task 1); `splitCurves` option, `curveSplit` field, group resolver, true-distance radius, anchor snap, endpoint guard, malformed fallback (Task 2); halves assembly (Task 3); `performCut` opt-in + toast case retained (Task 4); feature-map docs (Task 5); runtime scenarios incl. delete-segment regression (Task 6). Untouched list → Global Constraints.
- **Placeholders:** none — every code step carries complete code; every run step has a command and expected outcome.
- **Type consistency:** `Point2D`/`CubicSplit`/`NearestOnCubic`/`FindCutSiteOptions`/`curveSplit: { groupStart, t }` used identically across Tasks 1-4; test fixture indices ([1..4] group, apex (150, 45)) consistent across Tasks 2-3.
