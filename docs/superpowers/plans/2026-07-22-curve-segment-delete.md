# Curve-Aware Delete-Segment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Both delete-segment picks work on curved (bezier) spans — including both picks inside one curved sweep — with survivors that keep exact curve shape and a preview that renders true arcs.

**Architecture:** Curve picks are normalized into vertex-snapped picks on a bezier-split copy of the point list inside `buildSegmentDeletePieces` (pure), so the existing slicing runs unchanged. `scanForPick` opts in via `findCutSite`'s existing `splitCurves` flag; the preview overlay's path walk becomes bezier-aware. Spec (read first): `docs/superpowers/specs/2026-07-22-curve-segment-delete-design.md`.

**Tech Stack:** TypeScript; jest node-env recipe for the pure module; `verify` skill (headless Chrome CDP) for runtime acceptance.

## Global Constraints

- `polyline_cut_geometry.ts` stays **pure** (no Session/DOM imports; it already imports only types + `./curve_groups`).
- **Cut tool behavior unchanged** — no edits to `polyline_cut.ts` or the cut switch.
- **Toasts unchanged**; `"curve"` outcomes remain only as the malformed-group fallback.
- **Normalization order rule (load-bearing):** split at the **later** pick first; when both picks share a group, remap the earlier parameter to `t1' = t1 / t2` before its own split; after splitting the earlier pick, shift the later pick's indices by **+3** (a split replaces the 2 control points with 5 points).
- Pure-test command (from repo root; the `./` prefix on the noop paths is required):
  `npx jest app/test/drawable/polyline_cut_geometry.test.ts --env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js`
  Do NOT run the drawable/component suites (native canvas + redis unavailable).
- Lint caveat: compare only **non-prettier** violations against HEAD (pre-existing CRLF `prettier/prettier` noise on Windows checkouts).
- Commit messages end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: `sitePositionKey` orders curve picks

**Files:**
- Modify: `app/src/drawable/2d/polyline_cut_geometry.ts` (`sitePositionKey`)
- Test: `app/test/drawable/polyline_cut_geometry.test.ts`

**Interfaces:**
- Consumes: `CutSite.curveSplit?: { groupStart: number; t: number }` (already shipped by the curve-cut feature).
- Produces: `sitePositionKey` returns `groupStart + 3 * t` for curve picks — Task 2's ordering (via the unchanged `normalizeDeletePicks`) relies on this.

- [ ] **Step 1: Write the failing tests**

In `app/test/drawable/polyline_cut_geometry.test.ts`, add below the existing `interior(...)` helper (near the top of the file):

```ts
/**
 * Shorthand: a mid-curve interior pick (carries curveSplit).
 *
 * @param groupStart the bezier group's start anchor index
 * @param t the split parameter
 * @param x pick x
 * @param y pick y
 */
function curveInterior(
  groupStart: number,
  t: number,
  x: number,
  y: number
): DeleteSitePick {
  return {
    kind: "interior",
    site: {
      segmentIndex: groupStart,
      point: { x, y },
      snappedVertexIndex: null,
      distance: 0,
      curveSplit: { groupStart, t }
    }
  }
}
```

And add at the end of the file:

```ts
describe("sitePositionKey for curve picks", () => {
  // Group [1..4]: 0:(0,0) 1:(100,0) 2:C 3:C 4:(200,0) 5:(300,0)
  const points = [
    pt(0, 0),
    pt(100, 0),
    pt(130, 60, PathPointType.CURVE),
    pt(170, 60, PathPointType.CURVE),
    pt(200, 0),
    pt(300, 0)
  ]

  test("a curve pick sits between its group's anchors", () => {
    const key = sitePositionKey(points, curveInterior(1, 0.5, 150, 45))
    expect(key).toBeGreaterThan(1)
    expect(key).toBeLessThan(4)
  })

  test("two same-group picks order by t", () => {
    const early = sitePositionKey(points, curveInterior(1, 0.3, 140, 40))
    const late = sitePositionKey(points, curveInterior(1, 0.7, 160, 40))
    expect(early).toBeLessThan(late)
  })

  test("curve picks order against straight and end picks", () => {
    const beforeGroup = sitePositionKey(points, interior(0, 50, 0))
    const inGroup = sitePositionKey(points, curveInterior(1, 0.5, 150, 45))
    const afterGroup = sitePositionKey(points, interior(4, 250, 0))
    const startEnd = sitePositionKey(points, {
      kind: "end",
      endpointIndex: 0
    })
    const lastEnd = sitePositionKey(points, {
      kind: "end",
      endpointIndex: points.length - 1
    })
    expect(startEnd).toBeLessThan(beforeGroup)
    expect(beforeGroup).toBeLessThan(inGroup)
    expect(inGroup).toBeLessThan(afterGroup)
    expect(afterGroup).toBeLessThan(lastEnd)
  })
})
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `npx jest app/test/drawable/polyline_cut_geometry.test.ts --env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js`
Expected: FAIL — "a curve pick sits between its group's anchors" gets a key in `(1, 2)` from the linear-fraction fall-through (the projection math treats `segmentIndex 1 → 2`, a control span), which breaks the same-group ordering test too (both keys computed from distance to span `1→2`, not from `t`). If any of the three passes coincidentally, proceed — Step 3's change makes the intent explicit either way.

- [ ] **Step 3: Implement**

In `sitePositionKey` (`app/src/drawable/2d/polyline_cut_geometry.ts`), insert a `curveSplit` case after the `snappedVertexIndex` check:

Find:

```ts
  if (pick.site.snappedVertexIndex !== null) {
    return pick.site.snappedVertexIndex
  }
  const i = pick.site.segmentIndex
```

Replace with:

```ts
  if (pick.site.snappedVertexIndex !== null) {
    return pick.site.snappedVertexIndex
  }
  if (pick.site.curveSplit !== undefined) {
    // A mid-curve pick sits between its group's anchors, which occupy
    // indices groupStart and groupStart + 3.
    return pick.site.curveSplit.groupStart + 3 * pick.site.curveSplit.t
  }
  const i = pick.site.segmentIndex
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx jest app/test/drawable/polyline_cut_geometry.test.ts --env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js`
Expected: PASS (whole file).

- [ ] **Step 5: Commit**

```bash
git add app/src/drawable/2d/polyline_cut_geometry.ts app/test/drawable/polyline_cut_geometry.test.ts
git commit -m "feat: sitePositionKey orders mid-curve picks by bezier parameter

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `buildSegmentDeletePieces` normalizes curve picks

**Files:**
- Modify: `app/src/drawable/2d/polyline_cut_geometry.ts` (`buildSegmentDeletePieces` + two new private helpers)
- Test: `app/test/drawable/polyline_cut_geometry.test.ts`

**Interfaces:**
- Consumes: `splitCubicBezier`, `Point2D` (curve-cut feature); `sitePositionKey` curve case (Task 1, via `normalizeDeletePicks` ordering); `findCutSite(..., { splitCurves: true })` in tests to build realistic picks.
- Produces: `buildSegmentDeletePieces` (signature unchanged) handles picks carrying `curveSplit`. Tasks 3-4 rely on this transparently.

- [ ] **Step 1: Write the failing tests**

Add at the end of `app/test/drawable/polyline_cut_geometry.test.ts` (uses the `evalAt` oracle already in the file and `findCutSite` for realistic picks):

```ts
describe("buildSegmentDeletePieces with curve picks", () => {
  const RADIUS = 10
  const SNAP = 8
  // 0:(0,0) 1:(100,0) 2:C 3:C 4:(200,0) 5:(300,0); group [1..4], apex (150,45).
  const points = [
    pt(0, 0),
    pt(100, 0),
    pt(130, 60, PathPointType.CURVE),
    pt(170, 60, PathPointType.CURVE),
    pt(200, 0),
    pt(300, 0)
  ]

  /**
   * Build a real interior pick from a click via findCutSite (splitCurves on).
   *
   * @param x click x
   * @param y click y
   */
  function pickAt(x: number, y: number): DeleteSitePick {
    const r = findCutSite(points, { x, y }, RADIUS, SNAP, {
      splitCurves: true
    })
    if (r.kind !== "site") {
      throw new Error(`pickAt(${x},${y}) got ${r.kind}`)
    }
    return { kind: "interior", site: r.site }
  }

  test("curve pick + straight pick across mixed geometry", () => {
    const a = pickAt(150, 52) // mid-curve, t ~ 0.5
    const b = pickAt(250, 3) // straight segment 4->5
    const norm = normalizeDeletePicks(points, a, b, SNAP)
    expect(norm.kind).toBe("ok")
    if (norm.kind !== "ok") return
    const { left, right, doomed } = buildSegmentDeletePieces(
      points,
      norm.first,
      norm.second
    )
    // Left survivor: [v0, A0, L1, L2, P] — keeps its curved lead-out.
    expect(left).toBeDefined()
    expect((left as SimplePathPoint2DType[]).map((p) => p.pointType)).toEqual([
      PathPointType.LINE,
      PathPointType.LINE,
      PathPointType.CURVE,
      PathPointType.CURVE,
      PathPointType.LINE
    ])
    // Right survivor: [(250,0), (300,0)].
    expect(right).toBeDefined()
    const r = right as SimplePathPoint2DType[]
    expect(r.length).toBe(2)
    expect(r[0].x).toBeCloseTo(250, 0)
    // Doomed: P -> R1 -> R2 -> A1(200,0) -> cut point on the straight span.
    expect(doomed[0].x).toBeCloseTo(150, 0)
    expect(doomed[doomed.length - 1].x).toBeCloseTo(250, 0)
    expect(doomed.map((p) => p.pointType)).toEqual([
      PathPointType.LINE,
      PathPointType.CURVE,
      PathPointType.CURVE,
      PathPointType.LINE,
      PathPointType.LINE
    ])
    // The left survivor's curve segment traces the original up to t.
    const t0 = norm.first.kind === "interior"
      ? (norm.first.site.curveSplit?.t ?? 0)
      : 0
    const l = left as SimplePathPoint2DType[]
    for (const u of [0.1, 0.25, 0.45]) {
      const orig = evalAt(points[1], points[2], points[3], points[4], u)
      const h = evalAt(l[1], l[2], l[3], l[4], u / t0)
      expect(h.x).toBeCloseTo(orig.x, 4)
      expect(h.y).toBeCloseTo(orig.y, 4)
    }
  })

  test("both picks inside one curved sweep", () => {
    const a = pickAt(138, 44) // earlier on the curve
    const b = pickAt(162, 44) // later on the curve
    const norm = normalizeDeletePicks(points, b, a, SNAP) // reversed on purpose
    expect(norm.kind).toBe("ok")
    if (norm.kind !== "ok") return
    const { left, right, doomed } = buildSegmentDeletePieces(
      points,
      norm.first,
      norm.second
    )
    const l = left as SimplePathPoint2DType[]
    const r = right as SimplePathPoint2DType[]
    // Left: [v0, A0, l1, l2, P1]; Right: [P2, r1, r2, A1, v5].
    expect(l.map((p) => p.pointType)).toEqual([
      PathPointType.LINE,
      PathPointType.LINE,
      PathPointType.CURVE,
      PathPointType.CURVE,
      PathPointType.LINE
    ])
    expect(r.map((p) => p.pointType)).toEqual([
      PathPointType.LINE,
      PathPointType.CURVE,
      PathPointType.CURVE,
      PathPointType.LINE,
      PathPointType.LINE
    ])
    // Doomed: the pure sub-curve [P1, m1, m2, P2].
    expect(doomed.map((p) => p.pointType)).toEqual([
      PathPointType.LINE,
      PathPointType.CURVE,
      PathPointType.CURVE,
      PathPointType.LINE
    ])
    // All three pieces jointly trace the original cubic.
    const t1 = norm.first.kind === "interior"
      ? (norm.first.site.curveSplit?.t ?? 0)
      : 0
    const t2 = norm.second.kind === "interior"
      ? (norm.second.site.curveSplit?.t ?? 0)
      : 0
    let maxErr = 0
    for (const u of [0.1, 0.2, 0.35, 0.5, 0.65, 0.8, 0.95]) {
      const orig = evalAt(points[1], points[2], points[3], points[4], u)
      let h
      if (u <= t1) {
        h = evalAt(l[1], l[2], l[3], l[4], u / t1)
      } else if (u <= t2) {
        h = evalAt(doomed[0], doomed[1], doomed[2], doomed[3],
          (u - t1) / (t2 - t1))
      } else {
        h = evalAt(r[0], r[1], r[2], r[3], (u - t2) / (1 - t2))
      }
      maxErr = Math.max(maxErr,
        Math.hypot(h.x - orig.x, h.y - orig.y))
    }
    expect(maxErr).toBeLessThan(0.001)
    // Doomed endpoints are the resolved pick points.
    expect(doomed[0].x).toBeCloseTo(norm.first.kind === "interior"
      ? norm.first.site.point.x : NaN)
    expect(doomed[3].x).toBeCloseTo(norm.second.kind === "interior"
      ? norm.second.site.point.x : NaN)
  })

  test("end trim + curve pick", () => {
    const endPick: DeleteSitePick = { kind: "end", endpointIndex: 0 }
    const curvePick = pickAt(150, 52)
    const norm = normalizeDeletePicks(points, endPick, curvePick, SNAP)
    expect(norm.kind).toBe("ok")
    if (norm.kind !== "ok") return
    const { left, right, doomed } = buildSegmentDeletePieces(
      points,
      norm.first,
      norm.second
    )
    expect(left).toBeUndefined()
    const r = right as SimplePathPoint2DType[]
    // Survivor: [P, R1, R2, A1, v5].
    expect(r.map((p) => p.pointType)).toEqual([
      PathPointType.LINE,
      PathPointType.CURVE,
      PathPointType.CURVE,
      PathPointType.LINE,
      PathPointType.LINE
    ])
    // Doomed: [v0, A0, l1, l2, P].
    expect(doomed.map((p) => p.pointType)).toEqual([
      PathPointType.LINE,
      PathPointType.LINE,
      PathPointType.CURVE,
      PathPointType.CURVE,
      PathPointType.LINE
    ])
  })

  test("straight-only picks are untouched by normalization", () => {
    const a = interior(0, 40, 0)
    const b = interior(4, 260, 0)
    const { left, right, doomed } = buildSegmentDeletePieces(points, a, b)
    expect((left as SimplePathPoint2DType[]).length).toBe(2)
    expect((right as SimplePathPoint2DType[]).length).toBe(2)
    expect(doomed.length).toBe(6)
  })
})
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `npx jest app/test/drawable/polyline_cut_geometry.test.ts --env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js`
Expected: FAIL — curve picks are treated as plain interior picks on a control span (the cut point is inserted as a bare LINE vertex among control points; point-type sequences don't match).

- [ ] **Step 3: Implement the normalization**

In `app/src/drawable/2d/polyline_cut_geometry.ts`:

(a) Directly ABOVE the `buildSegmentDeletePieces` doc comment, add the two helpers:

```ts
/**
 * Split the point list at a mid-curve pick and rewrite the pick as a
 * vertex snap on the inserted on-curve point. The group's control pair is
 * replaced by [L1, L2, P, R1, R2] (net +3 points); indices after
 * groupStart shift by +3.
 *
 * @param points the polyline's stored vertices
 * @param site the pick's site (must carry curveSplit)
 */
function splitAtCurvePick(
  points: readonly SimplePathPoint2DType[],
  site: CutSite
): { points: SimplePathPoint2DType[]; pick: DeleteSitePick } {
  if (site.curveSplit === undefined) {
    throw new Error("not a curve pick")
  }
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
  const vertexIndex = groupStart + 3
  const nextPoints = [
    ...points.slice(0, groupStart + 1),
    curvePoint(split.left.c1),
    curvePoint(split.left.c2),
    { x: split.point.x, y: split.point.y, pointType: PathPointType.LINE },
    curvePoint(split.right.c1),
    curvePoint(split.right.c2),
    ...points.slice(groupStart + 3)
  ]
  return {
    points: nextPoints,
    pick: {
      kind: "interior",
      site: {
        segmentIndex: vertexIndex,
        point: { x: split.point.x, y: split.point.y },
        snappedVertexIndex: vertexIndex,
        distance: site.distance
      }
    }
  }
}

/**
 * Convert any mid-curve picks into vertex snaps on a bezier-split copy of
 * the points, so the slicing below stays curve-agnostic. Ordering
 * contract: `first`/`second` are already ordered along the line. The
 * later pick is split first (earlier indices stay valid); same-group
 * picks remap the earlier parameter to t1/t2; after splitting the earlier
 * pick, the later pick's indices shift by +3.
 *
 * @param points the polyline's stored vertices
 * @param first the earlier pick
 * @param second the later pick
 */
function normalizeCurvePicks(
  points: readonly SimplePathPoint2DType[],
  first: DeleteSitePick,
  second: DeleteSitePick
): {
  points: readonly SimplePathPoint2DType[]
  first: DeleteSitePick
  second: DeleteSitePick
} {
  const firstSplit =
    first.kind === "interior" ? first.site.curveSplit : undefined
  const secondSplit =
    second.kind === "interior" ? second.site.curveSplit : undefined
  if (firstSplit === undefined && secondSplit === undefined) {
    return { points, first, second }
  }
  let pts: readonly SimplePathPoint2DType[] = points
  let f = first
  let s = second
  if (secondSplit !== undefined && s.kind === "interior") {
    if (
      firstSplit !== undefined &&
      f.kind === "interior" &&
      firstSplit.groupStart === secondSplit.groupStart
    ) {
      // Same group: after splitting at t2, the left sub-curve occupies the
      // original group indices; the earlier pick lives on it at t1/t2.
      f = {
        kind: "interior",
        site: {
          ...f.site,
          curveSplit: {
            groupStart: firstSplit.groupStart,
            t: firstSplit.t / secondSplit.t
          }
        }
      }
    }
    const r = splitAtCurvePick(pts, s.site)
    pts = r.points
    s = r.pick
  }
  if (f.kind === "interior" && f.site.curveSplit !== undefined) {
    const g = f.site.curveSplit.groupStart
    const r = splitAtCurvePick(pts, f.site)
    pts = r.points
    f = r.pick
    // The earlier split inserted +3 points before the later pick.
    if (s.kind === "interior") {
      const site = { ...s.site }
      if (site.segmentIndex > g) {
        site.segmentIndex += 3
      }
      if (site.snappedVertexIndex !== null && site.snappedVertexIndex > g) {
        site.snappedVertexIndex += 3
      }
      s = { kind: "interior", site }
    }
  }
  return { points: pts, first: f, second: s }
}
```

(b) Rename the existing `buildSegmentDeletePieces` body: change its export line

```ts
export function buildSegmentDeletePieces(
  points: readonly SimplePathPoint2DType[],
  first: DeleteSitePick,
  second: DeleteSitePick
): SegmentDeletePieces {
```

to a private worker plus a normalizing public wrapper (the existing body moves verbatim into the worker):

```ts
export function buildSegmentDeletePieces(
  points: readonly SimplePathPoint2DType[],
  first: DeleteSitePick,
  second: DeleteSitePick
): SegmentDeletePieces {
  const n = normalizeCurvePicks(points, first, second)
  return buildPiecesFromSnappedPicks(n.points, n.first, n.second)
}

/**
 * Slice the pieces from picks that are already vertex snaps, plain
 * interior projections, or end trims (curve picks were normalized away by
 * the caller).
 *
 * @param points the (possibly bezier-split) vertices
 * @param first the earlier pick
 * @param second the later pick
 */
function buildPiecesFromSnappedPicks(
  points: readonly SimplePathPoint2DType[],
  first: DeleteSitePick,
  second: DeleteSitePick
): SegmentDeletePieces {
```

(The original function's doc comment stays on the public wrapper; the body — from `const copy = ...` to the final `return { left, right, doomed }` — is otherwise untouched inside the worker.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx jest app/test/drawable/polyline_cut_geometry.test.ts --env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js`
Expected: PASS — including every pre-existing delete-segment test (straight behavior byte-identical via the early return).

- [ ] **Step 5: Commit**

```bash
git add app/src/drawable/2d/polyline_cut_geometry.ts app/test/drawable/polyline_cut_geometry.test.ts
git commit -m "feat: buildSegmentDeletePieces normalizes mid-curve picks via bezier split

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `scanForPick` opts in to curve sites

**Files:**
- Modify: `app/src/drawable/2d/polyline_segment_delete.ts` (~line 168)

**Interfaces:**
- Consumes: `findCutSite`'s `FindCutSiteOptions` (already imported symbol set is unchanged — options are passed inline).
- Produces: the user-facing behavior; `handleSegmentDeletePick`/`commitPendingSegmentDelete` signatures unchanged.

- [ ] **Step 1: Pass the flag**

In `scanForPick`, change:

```ts
    const result = findCutSite(scanPoints, click, radius, snapRadius)
```

to:

```ts
    const result = findCutSite(scanPoints, click, radius, snapRadius, {
      splitCurves: true
    })
```

- [ ] **Step 2: Type-check, lint, and re-run the pure suite**

Run: `npx tsc --noEmit` — expected exit 0.
Run: `npx eslint app/src/drawable/2d/polyline_segment_delete.ts app/src/drawable/2d/polyline_cut_geometry.ts 2>&1 | grep -v "prettier/prettier"` — expected: no new non-prettier violations vs HEAD.
Run the pure-test command from Global Constraints — expected PASS.

- [ ] **Step 3: Commit**

```bash
git add app/src/drawable/2d/polyline_segment_delete.ts
git commit -m "feat: delete-segment picks resolve on curved spans

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Bezier-aware preview overlay

**Files:**
- Modify: `app/src/components/label2d_canvas.tsx` (import at line ~70; `drawSegmentDeleteOverlay` doomed-path loop at ~lines 1033-1039)

**Interfaces:**
- Consumes: `PathPointType` from `../types/state`; the doomed piece may now contain `[LINE, CURVE, CURVE, LINE]` runs (Task 2).
- Produces: visual only — no exported symbols.

- [ ] **Step 1: Extend the types import**

Change:

```ts
import { ImageViewerConfigType, State } from "../types/state"
```

to:

```ts
import { ImageViewerConfigType, PathPointType, State } from "../types/state"
```

- [ ] **Step 2: Replace the doomed-path walk**

In `drawSegmentDeleteOverlay`, find:

```ts
      context.moveTo(preview.doomed[0].x * ratio, preview.doomed[0].y * ratio)
      for (let i = 1; i < preview.doomed.length; i++) {
        context.lineTo(
          preview.doomed[i].x * ratio,
          preview.doomed[i].y * ratio
        )
      }
```

Replace with:

```ts
      context.moveTo(preview.doomed[0].x * ratio, preview.doomed[0].y * ratio)
      for (let i = 1; i < preview.doomed.length; i++) {
        const p = preview.doomed[i]
        // A control-control-anchor run renders as its bezier arc so a
        // doomed curve chunk previews as the true curve, not its control
        // polygon.
        if (
          p.pointType === PathPointType.CURVE &&
          i + 2 < preview.doomed.length &&
          preview.doomed[i + 1].pointType === PathPointType.CURVE &&
          preview.doomed[i + 2].pointType !== PathPointType.CURVE
        ) {
          const c2 = preview.doomed[i + 1]
          const a = preview.doomed[i + 2]
          context.bezierCurveTo(
            p.x * ratio,
            p.y * ratio,
            c2.x * ratio,
            c2.y * ratio,
            a.x * ratio,
            a.y * ratio
          )
          i += 2
        } else {
          context.lineTo(p.x * ratio, p.y * ratio)
        }
      }
```

- [ ] **Step 3: Type-check and lint**

Run: `npx tsc --noEmit` — expected exit 0.
Run: `npx eslint app/src/components/label2d_canvas.tsx 2>&1 | grep -v "prettier/prettier"` — expected: no new non-prettier violations vs HEAD.

- [ ] **Step 4: Commit**

```bash
git add app/src/components/label2d_canvas.tsx
git commit -m "feat: delete-segment preview renders doomed curve chunks as true arcs

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Feature-map doc update

**Files:**
- Modify: `docs/polyline-feature-map.md` (section 1: the `polyline_cut_geometry.ts` and `polyline_segment_delete.ts` entries)

**Interfaces:** none (documentation).

- [ ] **Step 1: Update the geometry entry**

Find:

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
```

Replace with:

```markdown
- `app/src/drawable/2d/polyline_cut_geometry.ts` — pure cut-site math:
  `findCutSite` (nearest-span projection, endpoint guards, vertex snap; the
  opt-in `splitCurves` option resolves curve clicks to bezier split sites
  via `nearestTOnCubic` + `curveGroupIndices`; malformed groups still
  reject as "curve"), `buildCutHalves` (assembles `splitCubicBezier`
  de Casteljau halves for curve sites). Also the delete-segment geometry:
  `DeleteSitePick`, `normalizeDeletePicks`, `buildSegmentDeletePieces`
  (survivors + doomed path; mid-curve picks are pre-split into vertex
  snaps on a bezier-split copy — later pick first, same-group t1/t2
  remap — so the slicing stays curve-agnostic).
  No Session/DOM imports — testable with the node-env recipe.
```

- [ ] **Step 2: Update the delete-segment entry**

Find:

```markdown
- `app/src/drawable/2d/polyline_segment_delete.ts` — delete-segment tool:
  `handleSegmentDeletePick` (two picks; end picks = trims),
  `commitPendingSegmentDelete` (atomic; records cut/edited/deleted per
  outcome — no new history kinds).
```

Replace with:

```markdown
- `app/src/drawable/2d/polyline_segment_delete.ts` — delete-segment tool:
  `handleSegmentDeletePick` (two picks; end picks = trims; picks resolve
  on curved spans via `splitCurves: true` — mid-curve picks split beziers,
  both-in-one-sweep supported),
  `commitPendingSegmentDelete` (atomic; records cut/edited/deleted per
  outcome — no new history kinds).
```

- [ ] **Step 3: Commit**

```bash
git add docs/polyline-feature-map.md
git commit -m "docs: feature map notes curve-aware delete-segment picks

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Runtime verification (acceptance gate)

**Files:** none modified. If a scenario fails, fix through the relevant earlier task and re-run.

**Interfaces:** consumes the rebuilt app (`npm run build` first — the server serves `app/dist` from disk).

- [ ] **Step 1: Rebuild and invoke the repo `verify` skill** (headless CDP; reuse the running dev server + redis; poll `/getExport` with the stability pattern — predicate true on two consecutive fetches; draw in unoccupied regions of the `test2` scratch image).

- [ ] **Step 2: Same-sweep chunk delete** — draw a 2-vertex line, curve it (C gesture), pick two mid-curve points ~60 px apart → screenshot the preview (dashed **arc** between halos — not a straight chord/zigzag) → after 3 s: two labels, both `LCCL`, jointly tracing the original cubic with the doomed chunk gone (sample vs the Bernstein oracle; max error < 1 image px).

- [ ] **Step 3: Curve pick + straight pick** — on a 3-vertex line with segment 1 curved: pick mid-curve, pick mid straight segment → survivors keep the curve lead (`...CC L`) and the straight tail; gap matches the picks.

- [ ] **Step 4: End trim on a fully-curved line** — pick near an end anchor (end pick) + a mid-curve pick → single survivor, same label id, types `LCCL`, starting at the mid-curve pick point.

- [ ] **Step 5: Undo** — after the same-sweep commit: one Ctrl+Z restores the original curved line exactly. (Redo is NOT asserted — known pre-existing `recordCut` redo bug, logged separately.)

- [ ] **Step 6: Regressions** — straight interior-interior delete with 3 s preview timing; Escape cancels a preview; too-close toast; cut tool still cuts curves; delete-segment/cut mutual exclusion.

- [ ] **Step 7: Final sweep** — `npx tsc --noEmit` (exit 0), pure jest suite (PASS), report pass/fail per scenario, kill only the `chrome-cdp-verify` browser.

---

## Self-Review (completed at write time)

- **Spec coverage:** ordering (Task 1); normalization incl. same-group double split, later-first rule, +3 shift (Task 2); scan opt-in (Task 3); bezier preview (Task 4); docs (Task 5); runtime matrix incl. preview-arc screenshot, end trim, undo, regressions (Task 6). Untouched list → Global Constraints; malformed fallback preserved by findCutSite (no task needed).
- **Placeholders:** none — all code steps carry complete code; run steps have commands + expected results.
- **Type consistency:** `splitAtCurvePick(points, site: CutSite)` and `normalizeCurvePicks(points, first, second)` used exactly as defined; `buildPiecesFromSnappedPicks` name consistent between wrapper and worker; test helpers (`curveInterior`, `pickAt`) defined before use; `evalAt`/`pt`/`interior` reuse the file's existing helpers.
