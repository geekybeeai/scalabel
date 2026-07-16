# C-Curve Adjust Reset Fix (Mouse) — Design

**Date:** 2026-07-16
**Status:** Approved (brainstorming complete)

## Bug

Mouse workflow: draw a polyline → hover the segment's MID handle → press **C**
and click the handle → two cyan control points appear → drag them to shape the
curve → click a cyan point again to fine-tune → **the curve resets to a
straight segment and the cyan points disappear.**

Reproduced both with C still physically held and with C released moments
before the destructive click.

## Root cause

The only interactive path that removes control points is `lineToCurve()`'s
`CURVE → straight` toggle case (`app/src/drawable/2d/polygon2d.ts`); a plain
click on a CURVE point can only drag it. So on the destructive click the code
believed C was active. Two stale-C mechanisms make that happen:

1. **C held:** every mousedown with C held calls `lineToCurve()`. The 600 ms
   curve-burst exception (`app/src/common/curve_burst_state.ts`) only protects
   the presses of a single click burst; any adjust click later than 600 ms
   after the conversion toggles the segment straight again.
2. **C released:** the document keydown listener
   (`app/src/components/label2d_canvas.tsx`) arms the key on **every**
   keydown, including auto-repeats — deliberately, because trackpad palm
   rejection requires the press-release-click flow ("3 s since last keydown"
   ≈ "3 s since release"). The one-shot arm consumed by the conversion click
   is therefore instantly re-armed while C is held, survives the release by
   3 s, and a hands-off-keyboard click on a cyan point inside that window
   still counts as C+click → same toggle.

Upstream Scalabel did not have the arm window, so the original workflow —
convert with C+click, release C, plain-click-drag the cyan points — worked.
This fork's trackpad accommodation broke it for mouse users.

## Decision (from brainstorming)

Restore the upstream workflow by **removing the CURVE → straight toggle
entirely** rather than adding more timing state:

| Question | Decision |
|---|---|
| C+click on a cyan CURVE point | **Always drags the control point** — identical to a plain click, regardless of C held/armed/stale |
| Conversion trigger | Unchanged: click-time only (C held or armed at mousedown **on a MID handle**); pressing C alone never converts |
| Straighten-a-curve gesture | **None.** Recovery for an unwanted curve is Ctrl+Z (draw history), deleting an adjacent LINE vertex with D (which already sweeps up its control points), or deleting the line. A D+click-straightens alternative was considered and rejected (YAGNI — undo suffices) |
| 600 ms burst window (`curve_burst_state.ts`) | **Deleted** — its only purpose was distinguishing "continue the gesture" from "intentional straighten"; with the toggle gone the distinction is meaningless |
| 3 s arm window + repeat-refresh (`keyboard_state.ts`) | **Untouched** — required by trackpad palm rejection; must not regress the deferred trackpad work |
| Arm consumption | Consumed **only when a conversion actually happens** (click landed on a MID). A C-armed click on a CURVE point just drags and leaves a pending arm intact — adjusting is not the armed gesture |

### Gesture contract after the fix

Mousedown on a highlighted point of a selected, FINISHED line (polyline or
closed polygon — shared code path):

| Point under cursor | Plain click | C held or armed | D held or armed |
|---|---|---|---|
| LINE vertex | drag | drag | delete vertex (unchanged) |
| MID handle | promote to vertex + drag | **convert to curve**, drag shapes it | no-op (unchanged) |
| CURVE (cyan) | drag control point | **drag control point** | no-op (unchanged) |

Only the CURVE × C cell changes behavior. DRAW-state keys (D deletes last
vertex, Enter finishes) are untouched.

### Accepted residuals (conscious trade-offs)

- Within 3 s of a plain C press, clicking a MID handle converts it even if
  the user only meant to drag it (pre-existing, required for trackpads). Now
  non-destructive by construction: immediately visible, one Ctrl+Z away.
- **Arm-pending widening (introduced by this fix):** with consumption moved
  to conversion-time, a C-armed click on a CURVE or LINE point no longer
  spends the arm — so "press C, adjust a cyan point, then click a MID 2 s
  later" now converts that MID where today it would promote-and-drag it.
  Same non-destructive residual class as above; accepted.
- Once curved, a segment cannot be surgically straightened later without
  undo/vertex-delete/line-delete. Explicitly accepted by the product owner.
  Stated outright for the modal lane case: on a **2-vertex polyline**,
  D-recovery is unavailable (`deleteVertex` requires ≥ 3 LINE vertices on
  open lines, ≥ 4 closed), and draw history is cleared on item change
  (`label2d_handler.ts updateState` → `drawHistory.reset()`), so once
  Ctrl+Z is gone the only recovery for an unwanted curve on a minimal line
  is deleting and redrawing that line.

### Supersedes

`2026-07-08-curves-only-display-design.md` lists "reverting a visible curve
with C" as the curves-only cleanup workflow. That workflow is retired by this
design; curve removal in curves-only mode uses the same recovery paths as
everywhere else (undo, vertex delete, line delete).

## Implementation

**`app/src/drawable/2d/polygon2d.ts`** — all inside the existing
`onMouseDown` RESHAPE block and `lineToCurve`:

- In the `curveKey` branch: perform the conversion **only when the clicked
  point's type is MID** (then consume the arm and let the drag shape the
  curve). For any other point type, fall through to the plain-drag path
  (`toCache()` + drag) without consuming the arm. The clicked point's type
  (today's `preType`) survives as the gate — it just no longer feeds a burst
  check. **Constraint:** the delete branch must remain unreachable while
  `curveKey` is true (today via the `!curveKey &&` gate) — a C+D+click on a
  LINE vertex must drag, never delete. Do not restructure the branch in a
  way that lets a curveKey-true non-MID click reach the delete path.
- The conversion path deliberately does not call `toCache()` (it never has;
  `_startingPoints` stays stale during a conversion-drag). Harmless — the
  invalid-revert in `onMouseUp` cannot fire for curve edits, since validity
  is computed from LINE vertices which conversion and control-point drags
  never move — but add a one-line comment there so the asymmetry with the
  fall-through path doesn't read as a bug.
- Remove the `isRecentCurveConversion` check and `markCurveConversion` call;
  drop the `curve_burst_state` import.
- Remove `lineToCurve`'s now-unreachable `PathPointType.CURVE` case. The
  function becomes MID→curve only and keeps its name (still accurate — it
  turns a straight line segment into a curve); its doc comment drops the
  "and vice-versa".
- Housekeeping in the same functions: delete the leftover
  `console.log("[DEBUG] Polygon2D.onMouseUp completed…")` in `onMouseUp`;
  correct the stale "Disable deletion for now" comment in the D branch.

**Deleted:** `app/src/common/curve_burst_state.ts` and its test
`app/test/common/curve_burst_state.test.ts` (the module's only consumer is
`polygon2d.ts`, verified by grep).

**Docs:** update `docs/polyline-feature-map.md` — the `polygon2d.ts` entry's
key list (C is a click-time conversion on MID handles only, no toggle) and
the final "held-C double-clicks" gotcha (rewrite: toggle removed, burst state
gone; held-C double-click works because press 2 lands on a CURVE point and
drags it).

**Untouched:** `keyboard_state.ts`, `pointer_pan_state.ts` (400 ms pan window
and `shouldDeferPointerDown`), `deleteVertex`, kept-highlighted-handle rule in
`onMouseUp`, redux actions/reducers, draw history, export/import, toolbar
legend (C's "control curve" entry still describes C's one remaining job).

## Data flow / undo

Unchanged. Conversion and adjustment both commit through the existing
RESHAPE → `onMouseUp` → `UpdateLabelShapes` → `commit2DLabels` funnel, which
records to `DrawHistory` — so Ctrl+Z reverts an unwanted conversion, which is
the designated recovery path.

## Edge cases

- **Held-C double-click on a MID** (the mouse "continue gesture"): press 1
  converts — `lineToCurve` writes control point 1 into the former MID's
  **array slot**, though spatially it sits at the segment's 1/3 mark, out
  from under the cursor. The gesture still works because `_highlightedHandle`
  is unchanged, `onMouseUp` preserves it, and nothing on the mousedown path
  re-runs the hit test (only `Label2DHandler.onMouseMove` reassigns the
  highlight) — so press 2 targets a CURVE-typed point at the kept handle and
  drags it. Same behavior as the old burst window provided, but structural
  and permanent instead of 600 ms.
- **C+click on a LINE vertex:** plain drag (the MID gate makes today's
  implicit no-case explicit).
- **C and D both active:** C wins (existing precedence, unchanged).
- **Closed polygons:** same fix via the shared path; polygon validity checks
  (`minVertexNumber`, self-intersection) unaffected — conversion/adjust do
  not change LINE-vertex counts.
- **Stored coordinates:** untouched; this is gesture routing only (golden
  rule respected).

## Verification

- `npx tsc --noEmit`; eslint on touched files vs HEAD baseline (ignore the
  repo's pre-existing CRLF prettier noise on Windows checkouts).
- **Runtime (repo `verify` skill, headless Chrome CDP), mouse scenarios:**
  1. Convert → release C → within 3 s click-drag a cyan point → must adjust,
     never reset (the reported bug).
  2. Convert → keep C held > 600 ms → click-drag a cyan point → must adjust.
  3. Convert → wait > 3 s → click-drag a cyan point → must adjust.
  4. Held-C double-click-drag on a MID → curve created and shaped (continue
     gesture intact). **Constraint: do not synthesize a mousemove between
     the two mousedowns** — a move re-runs the hit test at the former
     midpoint location, lands on the edge (handle 0), and press 2 enters the
     MOVE branch instead of the point gesture.
  5. Regression: D+click deletes a LINE vertex; plain drag of LINE/MID
     unchanged; Ctrl+Z after a conversion restores the straight segment.
  6. C+click on a LINE vertex → plain drag (the new MID gate's genuinely new
     conditional; today this click reaches `lineToCurve`'s empty fall-through
     without caching, so behavior visibly changes to a proper cached drag).
  7. Closed polygon: convert a MID and adjust its control points (confirms
     the shared-path claim).
  8. Curves-only mode ON: drag a cyan point of a visible curve group
     (confirms adjustment works under the display filter now that this spec
     retires that feature's C-revert cleanup workflow).
- Drawable jest suites (`app/test/drawable/`) cannot load in this
  environment (native canvas + redis) — do not add suites there; geometry
  logic is unchanged and `curve_groups`/cut tests keep covering CURVE-typed
  fixtures.

## Out of scope

- The second reported bug (trackpad) — deferred by the user; this design
  deliberately leaves every trackpad accommodation (arm window,
  repeat-refresh, pan-window bypass, kept-highlighted-handle) untouched.
- Any straighten/revert gesture or toolbar affordance.
- Arm-window duration tuning.
