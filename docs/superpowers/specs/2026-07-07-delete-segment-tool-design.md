# Delete Segment Tool — Design

**Date:** 2026-07-07
**Status:** Approved (brainstorming complete)
**Builds on:** `2026-07-07-polyline-cut-tool-design.md` (the shipped scissor cut
tool — its geometry, state, visibility and undo machinery are reused heavily)

## Purpose

Annotators need to remove a portion of a polyline — a stretch of a lane line
that shouldn't be there — without deleting and redrawing the whole line. This
feature adds a **delete segment tool**: arm it, pick two points on a polyline
(the same forgiving picking as the cut tool), watch a green dashed 3-second
preview of the doomed piece, and it is deleted automatically. The remaining
piece(s) survive as independent polylines. One Ctrl+Z restores the original.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Entry points | **Toolbar button AND right-click context-menu item** ("Delete segment", under "✂ Cut polyline") |
| Preview | Green dashed marching-ants animation over the doomed piece for **3 seconds**, then auto-commit. **Escape cancels** (also item navigation); other clicks during preview are ignored |
| Scope | Middle deletes **and end trims**: a pick near the line's first/last vertex means "delete up to that end". Both-ends picks delete the whole line |
| Mechanics | **One atomic split-and-drop**: build the surviving piece(s) directly and commit in ONE `makeSequential` dispatch (like the cut). NOT literal two-cuts-then-delete — no intermediate states, no id churn |
| Undo | **Atomic, zero new history kinds**: two survivors → existing `"cut"` command; one survivor → `"edited"`; none → `"deleted"`. One press restores the original exactly |
| Tool exclusivity | Arming delete-segment disarms the cut tool and vice versa |

## UX flow

1. **Arm** via the new toolbar button (inline SVG glyph next to the scissor,
   tinted while armed — no new npm dependency) or the context menu's second
   item "Delete segment". Both are disabled/no-op while a polyline is mid-draw
   and in tracking mode (same guards as cut). The scissors cursor is reused
   while armed.
2. **Pick 1** — click on a polyline. Same rules as the cut click: ~20 screen-px
   search radius, straight (`LINE`-`LINE`) segments only, 8 px vertex snap,
   hidden labels excluded via the existing `CutVisibilityFilter`. One
   difference: a click within the endpoint-guard radius of the line's first or
   last vertex is a **valid trim pick** ("from that end"), not a rejection.
   A green halo marker (snap-indicator styling) stays on pick 1; during the
   preview both picked points carry the halo, on top of the dashed path.
3. **Pick 2** — must resolve on the **same polyline**:
   - different polyline → toast "Pick both points on the same polyline";
     still waiting
   - empty space → silently swallowed; still waiting
   - curved segment → existing curve toast; still waiting
   - resolves to (essentially) the same position as pick 1 — including both
     picks snapping to the same vertex or two trims at the same end → toast
     "Picked points are too close"; still waiting
4. **Preview** — the doomed piece (pick 1 → intermediate vertices → pick 2, or
   → the line end for trims) renders as a green dashed marching-ants overlay
   for 3 seconds, then the deletion **commits automatically** and the tool
   disarms (one-shot). All clicks during the preview are ignored.
5. **Cancel paths** — Escape while picking disarms; Escape during preview
   cancels the pending deletion and disarms; navigating to another item does
   the same. Nothing is deleted on any cancel path. (Redux is only touched at
   the final commit, so cancelling never needs a rollback.)
6. **Outcomes**
   - interior + interior → middle piece vanishes; **left** piece keeps the
     original label id/category/attributes, **right** piece is a new polyline
     (a cut's result shape, with a gap)
   - interior + endpoint → line trimmed; the single survivor keeps the
     original id
   - endpoint + endpoint (opposite ends) → whole line deleted
   - every survivor is marked `manual: true`
7. **Undo** — one Ctrl+Z / toolbar Undo restores the original line exactly;
   redo re-applies the deletion.

## Architecture

### New files

- **`app/src/common/segment_delete_state.ts`** — the tool's state machine,
  following the `cut_state.ts` pattern with a listener set:
  `inactive → awaitFirst → awaitSecond { labelId, site1 } →
  preview { labelId, site1, site2 }`, with transitions
  (`arm()`, `reset()`, advance functions) and
  `onSegmentDeleteChange(listener)`. Mutual exclusion lives here and in
  `cut_state.ts`: arming either tool resets the other.
- **`app/src/drawable/2d/polyline_segment_delete.ts`** — mirror of
  `polyline_cut.ts`:
  - `pickSegmentDeleteSite(click, radius, snapRadius, visibility,
    requiredLabelId?)` — scans via `findCutSite` like `performCut`; for pick 2
    the scan is constrained to pick 1's polyline; `"near-endpoint"` results
    are returned as valid trim picks (with which end) instead of rejections.
  - `commitSegmentDelete(itemIndex, labelId, siteA, siteB)` — re-validates the
    label still exists with unchanged shapes (guards against Ctrl+Z or edits
    between pick 1 and commit; cancels with a toast otherwise), builds the
    surviving pieces, dispatches ONE
    `makeSequential([deleteLabel(original), addLabel(left, same id)?,
    addLabel(right, new uid())?])`, and records history per outcome (below).

### Modified files

- **`app/src/drawable/2d/polyline_cut_geometry.ts`** — two additive changes:
  1. the `"near-endpoint"` result gains `endpointIndex` (0 or last vertex
     index) so callers know which end;
  2. new pure `buildSegmentDeletePieces(points, siteA, siteB)` returning
     `{ left?, right? }` after normalizing the two sites by position along
     the line. Stays Session/DOM-free → node-env unit tests.
- **`app/src/components/label2d_canvas.tsx`** —
  1. `onMouseDown` gains the delete-segment branch (routes the click per state
     machine phase; sits alongside the cut branch — mutually exclusive by
     construction);
  2. `redraw()` gains a post-pass drawing the pick-1 halo and, during preview,
     the doomed piece as green dashed marching-ants (a rAF loop advances
     `lineDashOffset` only while the preview is live; a 3 s timer fires the
     commit; the timer is cleared on Escape, item navigation, and unmount);
  3. Escape handling extended to the new mode;
  4. the context menu gains the "Delete segment" `MenuItem`.
- **`app/src/components/viewer2d.tsx`** — second toolbar button (same
  subscribe-for-tint pattern; one shared listener re-render).
- **`app/src/components/cut_icon.tsx`** — one more inline SVG path/component
  for the delete-segment glyph.

### Data flow

Arm → `awaitFirst` → valid click 1 → `awaitSecond` (halo drawn from state) →
valid click 2 → `preview` (overlay animates, timer armed) → timer fires →
`commitSegmentDelete` → one sequential dispatch → drawables rebuild →
history recorded → `inactive`. Picks and preview live entirely in module
state; redux is untouched until the commit.

## Geometry

**Site normalization.** Each pick resolves to a position key along the
polyline: `(segmentIndex, t)` for a projection, the vertex index for a snapped
pick, `0`/`last` for trim picks. If pick 2 sorts before pick 1 they swap — the
user may pick in either order. Keys coinciding within the snap radius →
"too close" rejection.

**Piece construction** (`buildSegmentDeletePieces`; original-image
coordinates; fresh copies; inputs never mutated). With picks on segments
`i ≤ j`, cut points `P1`, `P2`:

- middle delete: **left** = `V[0..i] + P1`, **right** = `P2 + V[j+1..end]` —
  both ≥ 2 vertices by construction (would-be stubs are trim picks instead).
  Both picks on the SAME segment works: right = `P2 + V[i+1..end]`.
- start trim: only **right** = `P2 + V[j+1..end]`; end trim: only **left** =
  `V[0..i] + P1`. The survivor keeps the original label id.
- both ends: no pieces (whole-line delete).
- vertex-snapped picks introduce no duplicate coordinate within a piece (same
  rule as the cut).
- curve spans strictly between the picks vanish with the doomed piece; curve
  spans in survivors are preserved verbatim. Picks are only valid on straight
  segments, so a bezier group can never be split mid-curve.

## Undo mapping (zero new history machinery)

| Outcome | Recorded as | Undo (one press) |
|---|---|---|
| two survivors | `recordCut(itemIndex, labelId, before, afterLeft, newRight)` | remove right, restore original |
| one survivor | `recordEdit(itemIndex, labelId, before, after)` | restore original geometry |
| no survivor | `recordDeletedLine(itemIndex, labelId, snapshot)` | re-add the original |

## Edge cases

- 2-point line → any middle pick pair lands on its one segment; works.
- Pick 1's line deleted or changed mid-flow (e.g. Ctrl+Z while waiting for
  pick 2 or during preview) → commit re-validation fails → toast, cancel,
  disarm. Nothing dispatched.
- Zoom/pan/rotate during picks/preview → fine; sites are stored in image
  coordinates and the overlay is drawn through the same canvas transform as
  labels.
- The 3 s timer and rAF loop are cleared on Escape, item navigation, and
  component unmount — no stray commits after cancel.
- Visibility: pick 1 respects the `CutVisibilityFilter` (hidden labels are
  never candidates); pick 2 is pinned to pick 1's (visible) line.
- Tool exclusivity: arming delete-segment while cut is armed (or vice versa)
  silently disarms the other.

## Error handling

`pickSegmentDeleteSite` returns a discriminated result; the canvas maps it to
the toasts listed in the UX flow via the existing `alert(Severity.WARNING,
...)`. `commitSegmentDelete` dispatches only after re-validation; no partial
state is ever committed.

## Testing

- **Pure node-env tests** (existing noop-globalSetup recipe): site
  normalization (ordering, either-order picks, same-segment, coincident
  rejection) and `buildSegmentDeletePieces` (middle, same-segment,
  vertex-snapped, start/end trim, both-ends, curve preservation,
  non-mutation); `endpointIndex` on near-endpoint results.
- **CI-only harness tests** (native canvas absent locally): the three undo
  mappings through `commitSegmentDelete`, and the re-validation guard.
- `npx tsc --noEmit`; eslint on touched files vs baseline (ignore the
  repo's pre-existing CRLF prettier noise).
- **Manual QA**: full flow on a real line; both trims; both-ends delete;
  every rejection toast; Escape at each phase; undo/redo for each outcome;
  marching-ants rendering under zoom/pan; mutual exclusion with the cut tool.

## Out of scope (v1)

- Deleting across curved pick points (picks stay straight-segment-only).
- Multi-segment marquee/box selection of the doomed region.
- Configurable preview duration (fixed 3 s).
- A confirm-early click during the preview.
