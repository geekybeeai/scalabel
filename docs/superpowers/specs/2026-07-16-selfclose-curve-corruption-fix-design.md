# Self-Close Merge Curve Corruption Fix — Design

**Date:** 2026-07-16
**Status:** Approved (root cause confirmed by code trace + CDP repro)

## Bug

Draw a polyline with one or more curved segments → drag one endpoint onto the
other endpoint (snap-close into a polygon). If the segment adjacent to the
**dragged** endpoint is curved and the dragged endpoint is the line's **start**
vertex, the closed shape is corrupt: the outline passes through the cyan CURVE
control points ("curve points take the place of the main vertices"), a stray
pale MID handle appears off the shape, and the fill is distorted.

Reproduced via CDP (scratch project `test2`): curve the first segment of a
3-vertex polyline, drag START onto END → corrupt blob (`m2_A_closed.png`);
drag END onto START instead → renders correctly (`m4_B_closed.png`). The
asymmetry matches the code exactly.

## Root cause

All point-array consumers — `draw()`'s path builder (`moveTo(points[0])`,
bezier stepping), `updateShapes`' closing-MID insertion, and
`curveGroupIndices` — assume the ring **starts with a LINE anchor**.
`deleteVertex` explicitly restores this invariant by rotating the array
(`while points[0].type !== LINE: shift→push`). `mergeWith` violates it:

- `getVertices()` returns LINE anchors **and** CURVE control points (it only
  filters MIDs), but the self-closing branch of `mergeWith` dedupes the
  now-coincident endpoints with a blind `shift()` (dragged start) / `pop()`
  (dragged end).
- `shift()` on a ring whose first segment is curved removes the leading anchor
  and leaves the array **beginning with the two control points**. The path
  builder then anchors the path at control point 1 and every bezier is offset
  by two points; `updateShapes` adds a closing MID between the last anchor and
  `points[0]` — now a **control point** — producing the bogus pale MID handle
  inside the curve group.
- `pop()` happens to leave a legal wrap-around curve group, which is why the
  bug appears intermittent (depends on which endpoint was dragged).

The two-polyline merge cases (A.end→B.start etc.) are structurally safe: the
removed duplicate anchor always sits at the seam where the other line's anchor
replaces it, and every merged result starts with a LINE endpoint of a
well-formed open polyline. They need no change (open paths must NOT be
rotated — order is meaning). Their output can later hit the self-close bug,
which this fix removes.

## Decision

In `mergeWith`'s self-closing branch, after removing the duplicate endpoint
and before `updateShapes`, **rotate the vertices ring until it starts with a
LINE vertex** — the same normalization `deleteVertex` uses. Guarded by a
`some(type === LINE)` check so a (theoretical) all-CURVE array cannot loop
forever.

Post-rotation the stranded control points become a legal wrap-around curve
group anchored at the surviving endpoint — geometrically exactly what the user
drew. Worked example (repro shape `[V0, c1, c2, V1, M, V2]`, drag V0 onto V2):
`shift` → `[c1, c2, V1, V2]` → rotate → `[V1, V2, c1, c2]` → `updateShapes`
yields `[V1, M(V1,V2), V2, c1, c2]` closed, wrap group `[V2, c1, c2, V1]`.
Same rendering as the already-correct pop case, mirrored.

| Question | Decision |
|---|---|
| Rotate non-self merge results too? | **No** — they are open polylines; rotation would reorder the path. Analysis shows they always start with a LINE anchor already. |
| Enforce min 3 anchors when self-closing? | Out of scope — pre-existing behavior (closing a 2-vertex curved line yields a legal degenerate lens; unchanged). |
| Repair already-corrupted saved labels? | Out of scope — recovery is delete/redraw or undo, as today. |
| New jest tests? | No — `mergeWith` is private on a canvas-bound drawable; drawable suites can't load here. Runtime CDP is the gate. |

## Implementation

**`app/src/drawable/2d/polygon2d.ts`** — self-closing branch of `mergeWith`
only (after the `shift()`/`pop()`, before `vertices.map((v) => v.shape())`):

```ts
// The ring must start with a LINE anchor: draw()'s path builder,
// updateShapes' closing-MID insertion, and curveGroupIndices all assume
// points[0] is a vertex. Removing the dragged duplicate endpoint above can
// strand its neighbours — two CURVE control points — at the array head, so
// rotate until an anchor leads (same normalization as deleteVertex).
if (vertices.some((v) => v.type === PathPointType.LINE)) {
  while (vertices[0].type !== PathPointType.LINE) {
    const p = vertices.shift()
    if (p !== undefined) {
      vertices.push(p)
    }
  }
}
```

**Docs:** add a gotcha to `docs/polyline-feature-map.md` (the points[0]-is-LINE
ring invariant; deleteVertex and mergeWith both restore it by rotating).

**Untouched:** `getVertices`, `updateShapes`, `deleteVertex`, the four
non-self merge cases, snap detection (`findNearestEndpoint`), draw path
builder, redux/actions, export/import, and everything from the C-curve fix.

## Verification

- `npx tsc --noEmit`; eslint on touched file vs HEAD baseline (ignore CRLF
  prettier noise).
- **Runtime (repo `verify` skill, headless CDP, project `test2`):**
  1. **The bug:** 3-vertex polyline, curve FIRST segment, drag START onto END
     → closes; cyan count preserved; **no pale MID handle at
     midpoint(dragged-end-anchor, control-point-1)** (the corruption
     signature); outline no longer passes through control point 1.
  2. **Control (unchanged):** same shape, drag END onto START → still renders
     the correct lens.
  3. **Curve at the far end:** curve the LAST segment, drag START onto END →
     closes cleanly (rotation no-ops when the head is already a LINE anchor;
     with the head anchor removed the ring starts at c1? — no: shift removes
     V0 whose segment is straight → ring starts with V1; rotation no-ops).
  4. **Regression, two-line merge:** two polylines, curve near the seam,
     drag A.end onto B.start → merged into one line, curve intact, then
     self-close the merged line → clean polygon.
  5. **Post-close editing:** drag a cyan point of the closed ring → adjusts
     (C-curve fix behavior holds on merged rings).
