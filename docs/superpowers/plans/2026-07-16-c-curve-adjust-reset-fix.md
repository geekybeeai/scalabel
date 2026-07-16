# C-Curve Adjust Reset Fix (Mouse) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clicking a cyan CURVE control point always drags it — never straightens the curve — by removing `lineToCurve`'s CURVE→straight toggle and gating conversion on MID handles only.

**Architecture:** Gesture-routing change confined to `Polygon2D.onMouseDown` plus deletion of the now-dead 600 ms burst-window module. No redux, geometry-math, export, or keyboard/pan state-module changes. Spec (read it first): `docs/superpowers/specs/2026-07-16-c-curve-adjust-reset-fix-design.md`.

**Tech Stack:** TypeScript React app under `app/`; verification via `npx tsc --noEmit`, eslint-vs-baseline, and the repo `verify` skill (headless Chrome CDP).

## Global Constraints

- **Untouchable files** (trackpad support must not regress): `app/src/common/keyboard_state.ts`, `app/src/common/pointer_pan_state.ts`. Also do not modify `deleteVertex`, the kept-`_highlightedHandle` rule in `onMouseUp`, or `Label2DHandler`.
- **C wins over D:** the delete branch must stay unreachable while `curveKey` is true. The `!curveKey &&` term in `deleteKey`'s definition is load-bearing — never remove it.
- **Arm consumption:** `consumeArmedKey(Key.C_*)` runs only when a conversion actually happens (clicked point is MID).
- **No new drawable jest tests:** `app/test/drawable/` suites cannot load in this environment (native `canvas` + redis). Do not create or run them; verification is tsc + lint + runtime CDP.
- **Lint baseline caveat:** `npm run lint` has pervasive pre-existing CRLF `prettier/prettier` noise on Windows checkouts. Only compare a changed file's **non-prettier** rule violations against HEAD.
- **Golden rule:** stored/exported coordinates stay in the original image pixel frame — this change is gesture routing only and must not touch coordinate math.
- Commit messages end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: Remove the CURVE→straight toggle; MID-gate the C conversion

**Files:**
- Modify: `app/src/drawable/2d/polygon2d.ts` (import block at top; `onMouseDown` curveKey/deleteKey block ~lines 644-693; `lineToCurve` ~lines 1347-1382)
- Delete: `app/src/common/curve_burst_state.ts`
- Delete: `app/test/common/curve_burst_state.test.ts`

**Interfaces:**
- Consumes: `isKeyHeld`/`isKeyArmed`/`consumeArmedKey` from `../../common/keyboard_state` (already imported and unchanged); `PathPointType` from `../../types/state`.
- Produces: the new gesture contract — `lineToCurve()` is MID→curve only (throws on non-MID, matching `midToVertex`'s guard style); `onMouseDown` routes curveKey+non-MID clicks to the plain-drag path. Tasks 3–4 rely on this behavior existing.

- [ ] **Step 1: Remove the curve_burst_state import**

In `app/src/drawable/2d/polygon2d.ts`, delete these lines (currently lines 3-6, directly below `import _ from "lodash"`):

```ts
import {
  isRecentCurveConversion,
  markCurveConversion
} from "../../common/curve_burst_state"
```

- [ ] **Step 2: Replace the curveKey/deleteKey branch block in `onMouseDown`**

Find this exact block (inside the `_highlightedHandle > 0` RESHAPE case; keep the multi-line comment about module-level keyboard state that sits above it):

```ts
        const nowMs = Date.now()
        const curveKey =
          isKeyHeld(Key.C_UP) ||
          isKeyHeld(Key.C_LOW) ||
          isKeyArmed(Key.C_UP, nowMs) ||
          isKeyArmed(Key.C_LOW, nowMs)
        const deleteKey =
          !curveKey &&
          (isKeyHeld(Key.D_UP) ||
            isKeyHeld(Key.D_LOW) ||
            isKeyArmed(Key.D_UP, nowMs) ||
            isKeyArmed(Key.D_LOW, nowMs))
        if (curveKey) {
          consumeArmedKey(Key.C_UP)
          consumeArmedKey(Key.C_LOW)
          const preType = this._points[this._highlightedHandle - 1].type
          if (
            preType === PathPointType.CURVE &&
            isRecentCurveConversion(this.labelId, nowMs)
          ) {
            // Same click burst (the 2nd/3rd press of a double-click-drag
            // with C held): the point was JUST converted — don't toggle it
            // back to straight, drag the control point instead. This is what
            // lets hold-C + double-click + drag work on trackpads, where the
            // gesture arrives as separate presses.
            this.toCache()
          } else {
            // Convert line to bezier curve; the drag that follows shapes it
            this.lineToCurve()
            if (preType === PathPointType.MID) {
              markCurveConversion(this.labelId, nowMs)
            }
          }
        } else if (deleteKey) {
          // Delete vertex
          // Disable deletion for now
          consumeArmedKey(Key.D_UP)
          consumeArmedKey(Key.D_LOW)
          this.toCache()
          this.deleteVertex()
        } else {
          // Drag vertex or midpoint
          this.toCache()
          if (
            this._points[this._highlightedHandle - 1].type === PathPointType.MID
          ) {
            // Drag midpoint: convert midpoint to vertex first
            this.midToVertex()
          }
        }
        this._labelList.addUpdatedLabel(this)
        return true
```

Replace it with:

```ts
        const nowMs = Date.now()
        const curveKey =
          isKeyHeld(Key.C_UP) ||
          isKeyHeld(Key.C_LOW) ||
          isKeyArmed(Key.C_UP, nowMs) ||
          isKeyArmed(Key.C_LOW, nowMs)
        // deleteKey's !curveKey gate is load-bearing: the delete branch must
        // stay unreachable while C is held/armed (C wins over D), so a
        // C+D+click on a LINE vertex drags — it must never delete.
        const deleteKey =
          !curveKey &&
          (isKeyHeld(Key.D_UP) ||
            isKeyHeld(Key.D_LOW) ||
            isKeyArmed(Key.D_UP, nowMs) ||
            isKeyArmed(Key.D_LOW, nowMs))
        const pointType = this._points[this._highlightedHandle - 1].type
        if (curveKey && pointType === PathPointType.MID) {
          // Convert the segment to a bezier curve; the drag that follows
          // shapes it. Conversion is the ONLY thing C does: clicking a cyan
          // CURVE control point falls through to the plain drag below, so an
          // adjust click can never straighten the curve (the old toggle
          // fired on stale held/armed C and destroyed curves mid-adjustment).
          // Unwanted curves are removed via undo, deleting an adjacent
          // vertex, or deleting the line. The arm is spent only when a
          // conversion actually happens.
          consumeArmedKey(Key.C_UP)
          consumeArmedKey(Key.C_LOW)
          // No toCache() here (never was): harmless, because onMouseUp's
          // invalid-revert can't fire for curve edits — validity is computed
          // from LINE vertices, which conversion and control-point drags
          // never move.
          this.lineToCurve()
        } else if (deleteKey) {
          // Delete the clicked LINE vertex (deleteVertex is a no-op on MID
          // and CURVE points — it gates on LINE type).
          consumeArmedKey(Key.D_UP)
          consumeArmedKey(Key.D_LOW)
          this.toCache()
          this.deleteVertex()
        } else {
          // Drag vertex, midpoint, or curve control point
          this.toCache()
          if (pointType === PathPointType.MID) {
            // Drag midpoint: convert midpoint to vertex first
            this.midToVertex()
          }
        }
        this._labelList.addUpdatedLabel(this)
        return true
```

- [ ] **Step 3: Replace `lineToCurve` with the MID-only version**

Find this exact method (~line 1347):

```ts
  /**
   * convert a line to a curve and vice-versa
   */
  private lineToCurve(): void {
    const selectedLabelIndex = this._highlightedHandle - 1
    const point = this._points[selectedLabelIndex]
    const highlightedHandleIndex = this._highlightedHandle - 1
    switch (point.type) {
      case PathPointType.MID: {
        // From midpoint to curve
        const prevPoint =
          this._points[this.getPreviousIndex(highlightedHandleIndex)]
        const nextPoint =
          this._points[this.getNextIndex(highlightedHandleIndex)]
        const controlPoints = this.getCurvePoints(
          prevPoint.vector(),
          nextPoint.vector()
        )
        this._points[highlightedHandleIndex] = controlPoints[0]
        this._points.splice(highlightedHandleIndex + 1, 0, controlPoints[1])
        break
      }
      case PathPointType.CURVE: {
        // From curve to midpoint
        const newMidPointIndex =
          this._points[highlightedHandleIndex - 1].type === PathPointType.CURVE
            ? this.getPreviousIndex(highlightedHandleIndex)
            : highlightedHandleIndex
        this._points.splice(highlightedHandleIndex, 1)
        this._points[newMidPointIndex] = this.getMidpoint(
          this._points[this.getNextIndex(newMidPointIndex)],
          this._points[this.getPreviousIndex(newMidPointIndex)]
        )
      }
    }
  }
```

Replace it with:

```ts
  /**
   * convert a straight segment's midpoint to a bezier curve (one-way; curves
   * are removed via undo, vertex delete, or line delete — there is no
   * straighten gesture)
   */
  private lineToCurve(): void {
    const highlightedHandleIndex = this._highlightedHandle - 1
    const point = this._points[highlightedHandleIndex]
    if (point.type !== PathPointType.MID) {
      throw new Error(`not a midpoint`)
    }
    const prevPoint =
      this._points[this.getPreviousIndex(highlightedHandleIndex)]
    const nextPoint = this._points[this.getNextIndex(highlightedHandleIndex)]
    const controlPoints = this.getCurvePoints(
      prevPoint.vector(),
      nextPoint.vector()
    )
    this._points[highlightedHandleIndex] = controlPoints[0]
    this._points.splice(highlightedHandleIndex + 1, 0, controlPoints[1])
  }
```

- [ ] **Step 4: Delete the dead module and its test**

```bash
git rm app/src/common/curve_burst_state.ts app/test/common/curve_burst_state.test.ts
```

- [ ] **Step 5: Verify zero references remain**

Run (from repo root):

```bash
grep -rn "curve_burst" app/src app/test
```

Expected: no output (exit code 1). Any hit means an import or call survived — fix before proceeding.

- [ ] **Step 6: Type-check**

Run: `npx tsc --noEmit`
Expected: exit 0, no errors. (Failure modes to expect if steps were incomplete: unused-import error for the deleted module, or unresolved `isRecentCurveConversion`/`markCurveConversion`.)

- [ ] **Step 7: Lint the changed file vs baseline**

```bash
npx eslint app/src/drawable/2d/polygon2d.ts 2>&1 | grep -v "prettier/prettier" | grep -v "^$"
```

Expected: only the file-path header line(s) and pre-existing non-prettier violations that also exist on HEAD (check with `git stash && npx eslint app/src/drawable/2d/polygon2d.ts 2>&1 | grep -v "prettier/prettier"; git stash pop` if unsure). No **new** rule violations.

- [ ] **Step 8: Commit**

```bash
git add app/src/drawable/2d/polygon2d.ts
git commit -m "fix: C never straightens a curve - adjust clicks can't reset it

lineToCurve is now one-way (MID->curve); clicking a cyan control point
always drags it regardless of C's held/armed state. The old
CURVE->straight toggle fired on stale C signals (held C past the 600ms
burst window, or the 3s arm re-armed by auto-repeat until keyup) and
destroyed curves during normal adjust clicks. curve_burst_state.ts and
its test die with the toggle. Arm consumption moves to conversion-time
only. Spec: docs/superpowers/specs/2026-07-16-c-curve-adjust-reset-fix-design.md

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

(The `git rm` in Step 4 already staged the deletions.)

---

### Task 2: Remove the leftover debug log in `onMouseUp`

**Files:**
- Modify: `app/src/drawable/2d/polygon2d.ts` (~lines 804-812, near the end of `onMouseUp`)

**Interfaces:**
- Consumes: nothing from other tasks (independent housekeeping approved in the spec).
- Produces: nothing relied on downstream.

- [ ] **Step 1: Delete the console.log block**

Find this exact code at the end of `onMouseUp`:

```ts
    this.UpdateLabelShapes()
    console.log("[DEBUG] Polygon2D.onMouseUp completed:", {
      editing: this.editing,
      state: this._state,
      highlightedHandle: this._highlightedHandle,
      snapTargetPolyline: this._snapTargetPolyline ? { index: this._snapTargetPolyline.index } : null,
      snapTargetPointIndex: this._snapTargetPointIndex
    })
    return true
```

Replace with:

```ts
    this.UpdateLabelShapes()
    return true
```

- [ ] **Step 2: Type-check**

Run: `npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add app/src/drawable/2d/polygon2d.ts
git commit -m "chore: drop leftover [DEBUG] log in Polygon2D.onMouseUp

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Update the feature-map doc to the new truth

**Files:**
- Modify: `docs/polyline-feature-map.md` (the `polygon2d.ts` entry in section 1, and the final "Held-C double-clicks" gotcha)

**Interfaces:**
- Consumes: the behavior implemented in Task 1 (describe it, don't invent).
- Produces: nothing code-visible; keeps the "read these first" guide truthful for the next agent.

- [ ] **Step 1: Update the polygon2d.ts key-symbol line**

Find:

```markdown
- `app/src/drawable/2d/polygon2d.ts` — **`Polygon2D`** (the line/polygon). Key symbols:
  `_points` (PathPoint2D[]), `Polygon2DState` (FREE/DRAW/FINISHED/RESHAPE/MOVE),
  `isDrawing`, `onMouseDown/Move/Up`, `onKeyDown` (**D**=delete vertex, **Enter**=finish,
  **C**=curve), `addVertex`, `deleteVertex`, `shapes()` (**skips MID points**),
```

Replace with:

```markdown
- `app/src/drawable/2d/polygon2d.ts` — **`Polygon2D`** (the line/polygon). Key symbols:
  `_points` (PathPoint2D[]), `Polygon2DState` (FREE/DRAW/FINISHED/RESHAPE/MOVE),
  `isDrawing`, `onMouseDown/Move/Up` (**C** held/armed + click a MID handle =
  convert segment to curve; one-way — clicking a CURVE point always drags),
  `onKeyDown` (**D**=delete vertex, **Enter**=finish),
  `addVertex`, `deleteVertex`, `shapes()` (**skips MID points**),
```

- [ ] **Step 2: Rewrite the final gotcha**

Find:

```markdown
- **Held-C double-clicks must not re-toggle the conversion.** `lineToCurve` is a
  toggle (MID→curve, CURVE→mid), and every mousedown with C held/armed invokes
  it — so the 2nd/3rd press of a hold-C double-click-drag used to straighten
  the segment right back. `common/curve_burst_state.ts` records the last
  MID→curve conversion per label; a C+press on a CURVE point within
  `CURVE_BURST_WINDOW_MS` (600 ms) continues the gesture (drags the control
  point) instead of toggling. Outside the window C+click on a control point
  still straightens. The unified gesture on BOTH devices: hover the point,
  hold C (or press+release within the 3 s arm window), click or double-click,
  drag.
```

Replace with:

```markdown
- **C never straightens a curve.** `lineToCurve` is one-way (MID→curve only);
  clicking a cyan CURVE control point ALWAYS drags it, whatever C's held/armed
  state. The old CURVE→straight toggle fired on stale C signals (held C past
  the 600 ms burst window, or the 3 s arm re-armed by auto-repeat until keyup)
  and destroyed curves during normal adjust clicks — see
  `docs/superpowers/specs/2026-07-16-c-curve-adjust-reset-fix-design.md`.
  Held-C double-click still "continues the gesture" structurally: press 1
  converts (control point 1 takes the former MID's array slot; spatially it
  sits at the 1/3 mark), and the kept `_highlightedHandle` + no hit-test on
  mousedown means press 2 targets that CURVE point and drags it. Unwanted
  curves are removed via undo, deleting an adjacent LINE vertex (its control
  points are swept up), or deleting the line — on a 2-vertex line after
  history is gone, delete-and-redraw is the only recovery. The unified
  gesture on BOTH devices: hover the MID, hold C (or press+release within
  the 3 s arm window), click or double-click, drag.
```

- [ ] **Step 3: Commit**

```bash
git add docs/polyline-feature-map.md
git commit -m "docs: feature map reflects one-way C conversion

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Runtime verification (acceptance gate)

**Files:**
- None modified. This task drives the app and reports results. If a scenario fails, fix via the relevant earlier task's file and re-run — do not patch ad hoc here.

**Interfaces:**
- Consumes: the built app with Tasks 1-3 applied.
- Produces: pass/fail evidence per spec scenario, reported to the user.

- [ ] **Step 1: Invoke the repo `verify` skill** (headless Chrome CDP against the label page) with the scenario list below. The skill owns launching/driving the app; feed it these scripts and expected outcomes.

- [ ] **Step 2: Scenario 1 — the reported bug (arm window):** draw a 2-vertex polyline; press and release C; within 3 s click the MID handle (converts — two cyan points appear); release; within 3 s of the original C release, click-drag a cyan point.
Expected: the control point moves; the curve stays a curve. FAIL if the cyan points vanish or the segment straightens.

- [ ] **Step 3: Scenario 2 — held C past 600 ms:** convert a MID with C held; keep C held > 600 ms; click-drag a cyan point.
Expected: control point drags; curve intact.

- [ ] **Step 4: Scenario 3 — cold click:** convert; wait > 3 s with no key presses; click-drag a cyan point.
Expected: control point drags; curve intact.

- [ ] **Step 5: Scenario 4 — held-C double-click-drag on a MID:** with C held, send mousedown/mouseup/mousedown at the MID's coordinates then drag, **without synthesizing any mousemove between the two mousedowns** (a move re-runs the hit test at the former midpoint, lands on the edge handle 0, and press 2 would enter the MOVE branch instead).
Expected: segment converts on press 1; press 2 + drag shapes the curve (control point 1 moves).

- [ ] **Step 6: Scenario 5 — regressions:** (a) D+click a LINE vertex on a ≥3-vertex line → vertex deleted; (b) plain drag of a LINE vertex and of a MID handle → unchanged promote/drag behavior; (c) Ctrl+Z after a conversion → straight segment restored (MID handle back).

- [ ] **Step 7: Scenario 6 — C+click on a LINE vertex:** with C held/armed, click-drag a LINE vertex.
Expected: plain drag (vertex follows cursor; no conversion anywhere; on release the shape commits normally).

- [ ] **Step 8: Scenario 7 — closed polygon:** on a closed polygon, C+click a MID → converts; drag its cyan points → adjusts; polygon stays valid.

- [ ] **Step 9: Scenario 8 — curves-only mode:** convert a segment, enable the "Curves only" sidebar checkbox, click-drag a visible cyan point.
Expected: adjustment works under the display filter.

- [ ] **Step 10: Final sweep**

Run: `npx tsc --noEmit` (expected: exit 0) and the Task 1 Step 7 lint command (expected: no new non-prettier violations).
Report all scenario outcomes to the user with pass/fail per scenario.

---

## Self-Review (completed at write time)

- **Spec coverage:** import removal + branch MID-gate + arm-consumption move + fall-through constraint (Task 1 Steps 1-2); lineToCurve CURVE-case removal + doc comment (Step 3); module + test deletion (Step 4); toCache-asymmetry comment (Step 2 new code); stale D-branch comment fix (Step 2 new code); debug log (Task 2); feature-map updates (Task 3); all 8 spec verification scenarios incl. the no-mousemove constraint (Task 4). Untouched-files list → Global Constraints.
- **Placeholders:** none — every code step shows exact before/after code.
- **Type consistency:** `pointType` defined and used only within the replaced block; `lineToCurve` signature unchanged (`private lineToCurve(): void`); no cross-task symbol drift.
