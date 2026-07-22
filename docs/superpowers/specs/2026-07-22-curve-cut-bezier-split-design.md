# Curve Cutting for the Scissor Tool (Bezier Split) — Design

**Date:** 2026-07-22
**Status:** Approved (brainstorming complete)

## Purpose

The cut (scissor) tool rejects clicks on curved spans ("Cannot cut a curved
segment.") — a restriction the original cut spec
(`2026-07-07-polyline-cut-tool-design.md`) deferred as "de Casteljau
subdivision — future work". Long curved lane sweeps are exactly where
annotators need splits, so this feature makes curved (cubic bezier) spans
cuttable: the curve is split at the clicked point into two bezier halves that
together render pixel-identical to the original.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Scope | **Cut tool only.** Delete-segment picks keep rejecting curve spans with the current toast |
| Cut semantics on a curve | **True bezier split** (de Casteljau) at the point on the actual curve nearest the click — halves preserve the exact shape. Snap-to-anchor and straighten-at-cut alternatives rejected |
| Shared-geometry divergence | `findCutSite` gains an opt-in `options: { splitCurves?: boolean }` (default `false`); only `performCut` passes `true`, so delete-segment call sites are untouched without signature churn |
| Malformed curve data | Stray CURVE points not forming a well-formed group (per `curveGroupIndices`'s rules) keep the old `"curve"` rejection — defensive fallback, never guess |
| UI | **No changes**: same button, cursor, arming guards, one-shot lifetime, toasts (minus one now-unreachable case) |

## Behavior contract

With the scissors armed, a click whose nearest span belongs to a bezier group
(`A0 LINE → C1 CURVE → C2 CURVE → A1 LINE`):

1. **Mid-curve click** → split the cubic at parameter `t` nearest the click.
   The cut point `P` (on the curve) becomes a LINE anchor: half A ends
   `…, A0, L1, L2, P`; half B starts `P, R1, R2, A1, …`. `L1/L2/R1/R2` are
   CURVE points from de Casteljau subdivision, so A + B jointly trace the
   original curve exactly. All other cut mechanics unchanged (A keeps the
   label id, B gets a fresh id, both `manual: true`, one atomic undo step).
2. **Click within `snapRadius` (8 screen px, converted to image px) of a
   bezier anchor**, measured as distance to the anchor → snap the cut to that
   anchor, exactly like today's vertex snap (no new coordinates).
3. **Snapped anchor is the polyline's first/last vertex** → `near-endpoint`
   rejection, as today. A *mid-curve* cut on the line's first/last curve
   group is allowed — both halves still get ≥ 2 anchors.
4. **Miss radius** (20 screen px) measures true distance to the curve for
   curve spans — an accuracy improvement over the control-polygon distance.
5. **Delete-segment picks unchanged**: its `findCutSite` calls omit
   `splitCurves`, so curve picks still return `"curve"` and its toast still
   fires. The cut tool's own `"curve"` toast case becomes unreachable and is
   removed from the cut switch in `label2d_canvas.tsx` (the delete-segment
   switch keeps its case).
6. **Malformed groups** → `"curve"` rejection (fallback), even with
   `splitCurves` on.

## Architecture

All new math lives in `app/src/drawable/2d/polyline_cut_geometry.ts` (stays
pure — no Session/DOM imports — so the node-env jest recipe applies).

### New pure functions

- `splitCubicBezier(a0, c1, c2, a1, t)` →
  `{ left: { c1, c2 }, point, right: { c1, c2 } }` — textbook de Casteljau:
  three lerp rounds; `point` is the on-curve split; the lerp intermediates
  supply both halves' control pairs.
- `nearestTOnCubic(a0, c1, c2, a1, click)` → `{ t, point, distance }` —
  coarse sampling (~32 cubic evaluations) then a few ternary-search
  refinement steps around the best sample. Sub-pixel accurate at lane
  scales; no calculus, no dependencies.
- An internal span-index → enclosing-bezier-group resolver built on the
  existing `curveGroupIndices` (`app/src/drawable/2d/curve_groups.ts`),
  which is already pure, tested, and defensive about malformed tails.

### Changed interfaces

- `findCutSite(points, click, radius, snapRadius, options?)` with
  `options: { splitCurves?: boolean }` (default `false`). When the nearest
  span touches CURVE points and `splitCurves` is on: resolve the group, run
  `nearestTOnCubic`, apply radius / anchor-snap / endpoint guards, and
  return a normal `"site"`. `CutSite` gains one optional field:
  `curveSplit?: { groupStart: number, t: number }` (`groupStart` = index of
  `A0`; `segmentIndex` is set to `groupStart` for curve sites so the field
  is always defined). An anchor-snapped curve click returns a plain
  vertex-snap site (`snappedVertexIndex` set, **no** `curveSplit`) — it
  flows through the existing snap path end-to-end. No resolvable group →
  `"curve"`.
- `buildCutHalves` branches on `curveSplit` and assembles the halves via
  `splitCubicBezier`; straight-span and vertex-snap paths untouched. The
  return shape is unchanged (plain point lists), so `performCut`/`commitCut`
  need no structural changes.
- `performCut` (`app/src/drawable/2d/polyline_cut.ts`) passes
  `{ splitCurves: true }` — its entire diff.

### Untouched

Commit mechanics (`commitCut`), `drawHistory.recordCut` and undo/redo
(whole-line snapshots are curve-agnostic), arming guards (mid-draw /
tracking / curves-only), mutual exclusion with delete-segment, cursor,
export/import schema (halves are ordinary poly2d with CURVE pairs), all
delete-segment behavior.

## Edge cases

- **Back-to-back curve groups** (shared anchor): the span→group resolver
  picks the group containing the nearest span; a click near the shared
  anchor snaps to it (case 2).
- **t very near 0/1 but outside the anchor snap distance**: allowed — same
  sliver policy as straight spans, where the snap radius (a distance, not a
  parameter bound) is the single guard.
- **Attribution across polylines**: the nearest-span scan still uses
  control-polygon distance to pick the candidate span; the true-curve
  distance then validates the radius. A pathological click could attribute
  to a neighboring span first — pre-existing behavior class, accepted (lane
  control points hug the chord).
- **2-anchor fully-curved line** (`A0, C1, C2, A1`): mid-curve cut yields
  two 2-anchor curved halves — valid.
- **Zoom/pan/rotation**: radii are already converted display→image by the
  caller; all math stays in original-image coordinates (golden rule).
- **Closed polygons**: still excluded (unchanged "closed" rejection).

## Testing

- **Unit (node-env recipe, extending the existing geometry test files):**
  - `splitCubicBezier`: t=0.5 symmetry; endpoints exact; sampled points of
    both halves lie on the original cubic within epsilon.
  - `nearestTOnCubic`: click exactly on the curve; off-curve click;
    clicks beyond either end clamp to t≈0/1.
  - `findCutSite` with `splitCurves` on: mid-curve site (carries
    `curveSplit`), anchor snap, endpoint rejection, malformed-tail
    fallback; with the flag off/omitted: `"curve"` (delete-segment
    regression).
  - `buildCutHalves`: curve assembly — point types
    (`…A0, L1ᶜ, L2ᶜ, Pˡ | Pˡ, R1ᶜ, R2ᶜ, A1…`), counts, coincident `P` in
    both halves, straight-path behavior unchanged.
- `npx tsc --noEmit`; eslint on touched files vs HEAD baseline (ignore
  pre-existing CRLF prettier noise).
- **Runtime (`verify` skill, headless CDP):** draw a line, curve a segment
  (C gesture), arm cut, click mid-curve → two labels whose joint render
  matches the original; undo restores one line; redo re-splits; regression:
  straight-span cut, vertex snap, and delete-segment still rejecting curve
  picks with its toast.

## Docs

`docs/polyline-feature-map.md`: one-line updates to the
`polyline_cut_geometry.ts` entry (bezier split + `splitCurves` flag) and the
`polyline_cut.ts` entry (cuts curves via de Casteljau).

## Out of scope

- Curve cutting for delete-segment picks (explicit scope decision; the
  geometry layer's flag makes it a small follow-up).
- Opening/cutting closed polygons.
- Any UI/toast additions.
