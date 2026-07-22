# Curve-Aware Delete-Segment Tool (Bezier Split Picks) — Design

**Date:** 2026-07-22
**Status:** Approved (brainstorming complete)

## Purpose

The delete-segment tool rejects picks on curved spans ("Cannot cut a curved
segment.") — the scope deliberately deferred by
`2026-07-22-curve-cut-bezier-split-design.md`, whose geometry layer was built
to make this follow-up small. This feature lets both picks land on curved
(cubic bezier) spans, including **both picks inside one long curved sweep**
(the lane case), with survivors that keep the exact curve shape.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Pick semantics on curves | **Mirror the curve cut exactly**: nearest on-curve point within the 20 px radius (true-curve distance); 8 px anchor snap; snap onto the line's first/last vertex stays an **end-trim** pick |
| Both picks in one bezier group | **Supported** — double de Casteljau split; the chunk between the two on-curve points is deleted |
| Where the bezier handling lives | **Pre-split normalization** inside `buildSegmentDeletePieces`: curve picks are converted to vertex-snapped picks on a bezier-split working copy of the points; the existing slicing logic runs unmodified. Per-branch inline handling and split-the-real-label-at-pick-time were rejected |
| Preview fidelity | The dashed marching-ants doomed piece renders **true bezier arcs** (the overlay's `lineTo` walk becomes bezier-aware) |
| Malformed curve data | Keeps the `"curve"` rejection and its toast — same fallback rule as the cut |
| UI | No new affordances: same trash button, phases, 3 s preview, Escape, toasts |

## Behavior contract

With delete-segment armed, a pick whose nearest span belongs to a bezier
group (`A0 LINE → C1 CURVE → C2 CURVE → A1 LINE`):

1. **Mid-curve pick** → valid interior pick at the nearest point on the
   actual curve (radius/snap guards measured against the true curve —
   inherited from `findCutSite`'s `splitCurves` path). The pick halo sits on
   the on-curve point.
2. **Pick within `snapRadius` of a bezier anchor** → snaps to that anchor
   (an ordinary vertex-snap pick). Snapping onto the polyline's first/last
   vertex → an **end-trim** pick, exactly as on straight lines today.
3. **Both picks in the same group** → the sub-curve between the two split
   parameters is the doomed piece; both survivors stay curved.
4. **Shape fidelity**: survivors plus the doomed piece jointly trace the
   original beziers exactly (de Casteljau guarantee, same as the cut).
5. **Everything else unchanged**: two-pick phase machine, 3 s preview +
   auto-commit, Escape cancel, staleness guard, too-close check (distance
   between resolved on-curve pick points), wrong-line/closed rejections,
   commit recording (two survivors → `recordCut`, one → `recordEdit`, none
   → `recordDeletedLine`), arming guards and cut-tool mutual exclusion.
6. `"Cannot cut a curved segment."` remains only as the malformed-group
   fallback (stray control points that form no well-formed group).

Known inherited caveat (out of scope): redo of the two-survivor outcome
inherits the pre-existing `recordCut` redo bug (first half lost on redo,
already reproduced with plain straight cuts and logged for a separate fix).

## Architecture

### `app/src/drawable/2d/polyline_cut_geometry.ts` (stays pure)

- `buildSegmentDeletePieces(points, first, second)` gains an **entry
  normalization step** (signature unchanged; `first`/`second` must already
  be ordered by `normalizeDeletePicks`, as today):
  - A pick whose site carries `curveSplit` is resolved by splitting the
    working copy of the points with the existing `splitCubicBezier` —
    inserting `[…, L1ᶜ, L2ᶜ, Pˡ, R1ᶜ, R2ᶜ, …]` in place of the group's
    control pair — and rewriting the pick as a vertex-snap on the inserted
    `P`'s index.
  - **Order: split the later pick first** (indices before it stay valid).
    When both picks share a group, after splitting at `t2` the earlier
    pick's parameter is remapped onto the left sub-curve as `t1' = t1/t2`
    (guarded against `t2 = 0`, which the too-close check precludes anyway).
  - The existing end/snapped/interior slicing then produces
    `{ left?, right?, doomed }` unmodified.
- `sitePositionKey` gains the `curveSplit` case: position =
  `groupStart + 3 · t` (monotonic along the index axis; anchors sit at
  `groupStart` and `groupStart + 3`, so curve picks order correctly against
  every other pick kind).
- `resolvePickPoint` already returns `site.point` (the on-curve point) —
  the too-close check needs no change.

### `app/src/drawable/2d/polyline_segment_delete.ts`

- `scanForPick` passes `{ splitCurves: true }` to `findCutSite` — its
  entire diff. `near-endpoint` results keep mapping to end-trim picks;
  `"curve"` results (now malformed-only) keep mapping to the toast.

### `app/src/components/label2d_canvas.tsx`

- `drawSegmentDeleteOverlay`'s doomed-path loop (`moveTo` + `lineTo` per
  point) is replaced by a bezier-aware walk: an
  `anchor, CURVE, CURVE, anchor` run emits `bezierCurveTo`, anything else
  emits `lineTo`. Dash style, marching-ants offset, and pick halos are
  unchanged.

### Untouched

`segment_delete_state.ts` (phases/payloads), `commitPendingSegmentDelete`
(the pieces it consumes simply may contain CURVE points, which
`materialize` already copies verbatim), the cut tool, toasts, arming
guards, export/import, undo recording.

## Edge cases

- **Curve pick + straight pick**, **curve pick + end trim**, and
  **anchor-snapped curve picks** all reduce to existing slicing cases after
  normalization.
- **Both picks in one group**: doomed = `[P1, M1ᶜ, M2ᶜ, P2]` (a pure
  sub-curve); survivors keep `A0…P1` and `P2…A1` with their new control
  pairs.
- **Whole-line delete on a fully-curved line** (end trim at both ends):
  end picks carry no `curveSplit`; the doomed piece is the whole point
  list — unchanged behavior.
- **Preview → commit consistency**: the preview's doomed piece and the
  committed survivors both come from the same `buildSegmentDeletePieces`
  call shape, so what marches is exactly what dies.
- **Malformed groups** (per `curveGroupIndices`'s rules): pick rejected
  with the toast; the tool stays in its current phase.
- Golden rule respected: all math in original-image coordinates; radii
  converted by the caller exactly as today.

## Testing

- **Unit (node-env recipe, extending `polyline_cut_geometry.test.ts`):**
  - `sitePositionKey`: a curve pick orders between its group's anchors;
    two same-group picks order by `t`; curve pick vs end picks.
  - `buildSegmentDeletePieces`: curve interior × straight interior; curve
    interior × end trim; **both picks in one group** (sample survivors and
    doomed chunk against the original cubic with the Bernstein oracle;
    doomed endpoints equal the resolved pick points); anchor-snapped curve
    pick (no `curveSplit`, existing path); straight-only regression cases
    already in the suite stay green.
- `npx tsc --noEmit`; eslint on touched files vs HEAD baseline (ignore the
  pre-existing CRLF prettier noise).
- **Runtime (`verify` skill, headless CDP):** curved line → two mid-curve
  picks in one sweep → preview screenshot (dashed **arc**, not a control
  polygon zigzag) → 3 s commit → two curved survivors tracing the original
  (export sampling); curve pick + straight pick across mixed geometry; end
  trim on a fully-curved 2-anchor line; Ctrl+Z restores; Escape cancels;
  regressions — straight interior-interior delete, too-close toast, and
  the cut tool's curve behavior unchanged.

## Docs

`docs/polyline-feature-map.md`: update the `polyline_cut_geometry.ts` entry
(the "without it curve spans reject, which is what delete-segment relies
on" clause is retired; note the pick normalization), and the
`polyline_segment_delete.ts` entry (picks work on curves via
`splitCurves: true`).

## Out of scope

- Fixing the pre-existing `recordCut` redo bug (separate effort; affects
  cut and segment-delete equally).
- Opening/cutting closed polygons.
- Any new UI affordances or toasts.
