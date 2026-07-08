# Curves-Only Display Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "Curves only" sidebar checkbox that makes the 2D canvas render only the bezier stretches of every line — straight spans and fully-straight lines disappear — with everything visible staying fully interactive.

**Architecture:** A new optional viewer-config flag `showCurvesOnly` (toggled from the sidebar like Show Tags) flows through `Label2dCanvas.redraw` → `Label2DList.redraw` (skips labels with no curves) → `Polygon2D.draw` (a curves-only branch strokes each bezier group separately and filters vertex handles), identically on the VIEW and hit-test CONTROL canvases so visibility ≡ interactivity. A pure `curveGroupIndices` helper drives both the path and the handle filter. The cut/delete-segment tools refuse to arm while the flag is on.

**Tech Stack:** TypeScript + React 16 class components, redux (custom store), Material-UI v4, jest 26 (ts-jest).

**Spec:** `docs/superpowers/specs/2026-07-08-curves-only-display-design.md` — read it first.

## Global Constraints

- **No new npm dependencies.**
- **View-only feature:** stored/exported coordinates, undo/redo, and save are untouched. The flag lives in the per-viewer config and defaults to off.
- **Visibility ≡ interactivity:** the curves-only branch must run for BOTH `DrawMode.VIEW` and `DrawMode.CONTROL` — never let hidden geometry stay clickable.
- **Editing exemption is `editing`, not `selected`** (matches the hiddenCategories filter in `label2d_list.ts`).
- **Code style:** no semicolons, double quotes, JSDoc with `@param` lines on every exported/public symbol; nested `@param x.y` lines only for inline-object-typed params (none in this plan).
- **Lint bar:** `npx eslint -c .eslintrc.json --ext .ts,.tsx <changed files>` — no NEW non-prettier errors vs HEAD (pre-existing: pervasive CRLF `prettier/prettier` noise + 2 `dot-notation` errors in `label2d_canvas.tsx`).
- **Tests:** pure suites run locally via `--env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js`. Component behavior is verified via tsc/eslint + the repo verify skill (`.claude/skills/verify/SKILL.md`) at the end.
- Work on branch `feature-show-curves`. Commit after every task with trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`; commit ONLY the files each task names.

---

### Task 1: Pure helper — `curveGroupIndices`

**Files:**
- Create: `app/src/drawable/2d/curve_groups.ts`
- Test: `app/test/drawable/curve_groups.test.ts`

**Interfaces:**
- Consumes: `PathPointType` from `app/src/types/state` (values: `UNKNOWN`, `LINE`, `CURVE`, `MID`).
- Produces (Task 3 relies on this exact signature):

```ts
export function curveGroupIndices(
  types: readonly PathPointType[],
  closed?: boolean
): number[][]
// returns [anchorA, c1, c2, anchorB] index quadruples, one per bezier group
```

Background: a drawable's `_points` list contains LINE anchors, MID midpoints
(on straight spans only), and CURVE control points in consecutive pairs
bounded by LINE anchors: `..., A(LINE), C1(CURVE), C2(CURVE), B(LINE), ...`.
Closed shapes may have a group that wraps around the array end.

- [ ] **Step 1: Write the failing tests**

Create `app/test/drawable/curve_groups.test.ts`:

```ts
import { curveGroupIndices } from "../../src/drawable/2d/curve_groups"
import { PathPointType } from "../../src/types/state"

const L = PathPointType.LINE
const C = PathPointType.CURVE
const M = PathPointType.MID

describe("curveGroupIndices", () => {
  test("no curves -> no groups", () => {
    expect(curveGroupIndices([L, M, L, M, L])).toEqual([])
    expect(curveGroupIndices([])).toEqual([])
    expect(curveGroupIndices([L, L])).toEqual([])
  })

  test("one group", () => {
    expect(curveGroupIndices([L, C, C, L])).toEqual([[0, 1, 2, 3]])
  })

  test("group surrounded by straight spans with MID points", () => {
    expect(curveGroupIndices([L, M, L, C, C, L, M, L])).toEqual([
      [2, 3, 4, 5]
    ])
  })

  test("two groups sharing an anchor", () => {
    expect(curveGroupIndices([L, C, C, L, C, C, L])).toEqual([
      [0, 1, 2, 3],
      [3, 4, 5, 6]
    ])
  })

  test("groups at the start and end of an open line", () => {
    expect(curveGroupIndices([L, C, C, L, M, L, C, C, L])).toEqual([
      [0, 1, 2, 3],
      [5, 6, 7, 8]
    ])
  })

  test("stray single CURVE point is ignored (defensive)", () => {
    expect(curveGroupIndices([L, C, L, M, L])).toEqual([])
  })

  test("closed shape: group wrapping the array end", () => {
    // C2 at 0 and C1 at 4 belong to a group anchored at 3 (A) and 1 (B).
    expect(curveGroupIndices([C, L, M, L, C], true)).toEqual([[3, 4, 0, 1]])
  })

  test("open line never wraps", () => {
    expect(curveGroupIndices([C, L, M, L, C], false)).toEqual([])
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
npx jest app/test/drawable/curve_groups.test.ts --env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js --coverage=false
```
Expected: FAIL — `Cannot find module '../../src/drawable/2d/curve_groups'`.

- [ ] **Step 3: Implement**

Create `app/src/drawable/2d/curve_groups.ts`:

```ts
import { PathPointType } from "../../types/state"

/**
 * Find the bezier groups in a drawable point-type sequence.
 *
 * A group is the quadruple [anchorA, c1, c2, anchorB]: two consecutive
 * CURVE control points bounded by LINE anchors. MID points only occur on
 * straight spans and never neighbor a control point. Closed shapes may
 * contain one group that wraps the array end (returned with modular
 * indices); open lines never wrap. Malformed data (a stray single CURVE,
 * or a control pair without LINE anchors) yields no group — defensive,
 * never throws.
 *
 * @param types the point types of a drawable's points, in order
 * @param closed whether the shape is closed (allows wraparound)
 */
export function curveGroupIndices(
  types: readonly PathPointType[],
  closed: boolean = false
): number[][] {
  const n = types.length
  const groups: number[][] = []
  if (n < 4) {
    return groups
  }
  let i = 0
  while (i < n) {
    if (types[i] === PathPointType.CURVE) {
      const inBounds = closed || (i - 1 >= 0 && i + 2 < n)
      const a = (i - 1 + n) % n
      const c2 = (i + 1) % n
      const b = (i + 2) % n
      if (
        inBounds &&
        types[c2] === PathPointType.CURVE &&
        types[a] === PathPointType.LINE &&
        types[b] === PathPointType.LINE
      ) {
        groups.push([a, i, c2, b])
        i += 3
        continue
      }
    }
    i++
  }
  return groups
}
```

- [ ] **Step 4: Run tests to verify they pass**

Same command as Step 2. Expected: PASS (8 tests).

- [ ] **Step 5: Lint, typecheck, commit**

Run: `npx tsc --noEmit` (exit 0) and `npx eslint -c .eslintrc.json --ext .ts app/src/drawable/2d/curve_groups.ts app/test/drawable/curve_groups.test.ts` (zero errors).

```bash
git add app/src/drawable/2d/curve_groups.ts app/test/drawable/curve_groups.test.ts
git commit -m "feat: curveGroupIndices finds bezier groups in a point-type sequence

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Viewer-config flag + sidebar checkbox

**Files:**
- Modify: `app/src/types/state.ts` (ImageViewerConfigType)
- Modify: `app/src/components/toolbar.tsx` (toggle handler + prop wiring, ~line 280)
- Modify: `app/src/components/toolbar_category.tsx` (checkbox in the header row, ~line 312)

**Interfaces:**
- Consumes: `setCutMode` from `app/src/common/cut_state`; `resetSegmentDelete` from `app/src/common/segment_delete_state`; existing `changeViewerConfig`, `this.activeViewerConfig`, `this.safeActiveViewerId` in toolbar.tsx.
- Produces: `ImageViewerConfigType.showCurvesOnly?: boolean` — Tasks 3–4 read it.

No local runtime test (component); per-step check is tsc + eslint. Behavior verified in Task 5.

- [ ] **Step 1: Add the config field**

In `app/src/types/state.ts`, inside `ImageViewerConfigType`, directly after the `hiddenCategories` field, add:

```ts
  /** display only the curved parts of polylines (view-only filter) */
  showCurvesOnly?: boolean
```

(If the interface lists `hiddenCategories?: number[]` with a doc comment, mirror that style exactly.)

- [ ] **Step 2: Toolbar toggle handler**

In `app/src/components/toolbar.tsx`:

2a. Add imports next to the existing common imports:

```ts
import { setCutMode } from "../common/cut_state"
import { resetSegmentDelete } from "../common/segment_delete_state"
```

2b. In the `<ToolbarCategory ... />` element (where `showTags` / `onToggleTags` are passed, ~line 284), add two props after `onToggleTags`:

```tsx
            showCurvesOnly={activeConfig.showCurvesOnly ?? false}
            onToggleCurvesOnly={() => {
              const config = { ...activeConfig }
              config.showCurvesOnly = !(config.showCurvesOnly ?? false)
              if (config.showCurvesOnly) {
                // Tools must never act on hidden geometry.
                setCutMode(false)
                resetSegmentDelete()
              }
              Session.dispatch(
                changeViewerConfig(this.safeActiveViewerId, config)
              )
            }}
```

(`activeConfig` is the same object used by the surrounding props; if its type is not `ImageViewerConfigType`, spread-copy still carries the optional field — do not add casts unless tsc demands one, in which case cast the copy as `ImageViewerConfigType`.)

- [ ] **Step 3: Sidebar checkbox**

In `app/src/components/toolbar_category.tsx`:

3a. Add to the `Props` interface after `onToggleTags`:

```ts
  /** whether only curved parts of lines are displayed on the canvas */
  showCurvesOnly?: boolean
  /** toggle curves-only display */
  onToggleCurvesOnly?: () => void
```

3b. In the header row (inside the `{this.props.onToggleTags !== undefined && (...)}` fragment that renders the "Show Tags" checkbox+span, ~line 312), append a third checkbox AFTER the "Show Tags" span, inside the same fragment:

```tsx
                  {this.props.onToggleCurvesOnly !== undefined && (
                    <>
                      <Checkbox
                        size="small"
                        checked={this.props.showCurvesOnly ?? false}
                        onChange={() => this.props.onToggleCurvesOnly?.()}
                        title="Show only the curved parts of lines"
                        style={{ padding: 2, color: "inherit", marginLeft: 8 }}
                      />
                      <span style={{ fontSize: 12, opacity: 0.75 }}>
                        Curves only
                      </span>
                    </>
                  )}
```

- [ ] **Step 4: Typecheck + lint**

`npx tsc --noEmit` → exit 0. `npx eslint -c .eslintrc.json --ext .ts,.tsx app/src/types/state.ts app/src/components/toolbar.tsx app/src/components/toolbar_category.tsx` → no NEW non-prettier errors vs HEAD.

- [ ] **Step 5: Commit**

```bash
git add app/src/types/state.ts app/src/components/toolbar.tsx app/src/components/toolbar_category.tsx
git commit -m "feat: Curves only sidebar checkbox and showCurvesOnly viewer flag

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Render filtering — draw only bezier groups

**Files:**
- Modify: `app/src/drawable/2d/label2d.ts` (abstract `draw` signature)
- Modify: `app/src/drawable/2d/polygon2d.ts` (`draw`, new `hasCurves` getter)
- Modify: `app/src/drawable/2d/label2d_list.ts` (`redraw` filter + pass-through)
- Modify: `app/src/components/label2d_canvas.tsx` (`redraw` reads the flag)

**Interfaces:**
- Consumes: `curveGroupIndices` (Task 1); `showCurvesOnly` config field (Task 2).
- Produces: `Label2DList.redraw(..., lineWidthMultiplier, showCurvesOnly?: boolean)` trailing param; `Polygon2D.hasCurves: boolean` getter; `draw(..., curvesOnly?: boolean)` trailing param.

Notes for the implementer:
- TypeScript accepts subclass overrides with FEWER parameters, so adding an
  optional trailing param to the abstract `Label2D.draw` requires changes
  only in `label2d.ts` and `polygon2d.ts` — other drawables (box2d, etc.)
  stay untouched.
- The curves-only branch must apply to BOTH draw modes (VIEW and CONTROL);
  the label being edited (`this.editing`) renders in full.

- [ ] **Step 1: Abstract signature**

In `app/src/drawable/2d/label2d.ts`, find the abstract `draw` declaration (it ends with `lineWidthMultiplier` or `viewScale` params, mirroring Polygon2D's implementation) and append a final optional parameter:

```ts
    curvesOnly?: boolean
```

with a doc line `@param curvesOnly draw only bezier groups (curves-only display)` in the JSDoc.

- [ ] **Step 2: Polygon2D — getter and draw branch**

In `app/src/drawable/2d/polygon2d.ts`:

2a. Add the import:

```ts
import { curveGroupIndices } from "./curve_groups"
```

2b. Add a getter near the other getters (after `get closed()`):

```ts
  /**
   * Whether this shape contains at least one bezier control point.
   */
  public get hasCurves(): boolean {
    return this._points.some((p) => p.type === PathPointType.CURVE)
  }
```

2c. Extend the `draw` signature (currently ends `lineWidthMultiplier: number = 1`):

```ts
    lineWidthMultiplier: number = 1,
    curvesOnly: boolean = false
```

2d. At the top of `draw`, after `const numPoints = this._points.length`, add:

```ts
    // Curves-only display: stroke only bezier groups; the label being
    // actively edited is exempt and renders in full.
    const curvesOnlyActive = curvesOnly && !this.editing
    const curveGroups = curvesOnlyActive
      ? curveGroupIndices(
          this._points.map((p) => p.type),
          this._closed
        )
      : null
    if (curveGroups !== null && curveGroups.length === 0) {
      return
    }
```

2e. Wrap the path-building block. The existing code (after `context.beginPath()`) is:

```ts
    const begin = this._points[0].vector().scale(ratio)
    context.moveTo(begin.x, begin.y)
    for (let i = 1; i < numPoints; ++i) { ... }
    if (this._state === Polygon2DState.DRAW) { ... }
    if (this._closed) { ...fill... }
```

Replace with a branch — when `curveGroups !== null`, stroke each group and skip the mouse-line and closed-fill logic entirely; otherwise keep the existing code verbatim:

```ts
    if (curveGroups !== null) {
      for (const [a, c1, c2, b] of curveGroups) {
        const pa = this._points[a].vector().scale(ratio)
        const p1 = this._points[c1].vector().scale(ratio)
        const p2 = this._points[c2].vector().scale(ratio)
        const pb = this._points[b].vector().scale(ratio)
        context.moveTo(pa.x, pa.y)
        context.bezierCurveTo(p1.x, p1.y, p2.x, p2.y, pb.x, pb.y)
      }
    } else {
      const begin = this._points[0].vector().scale(ratio)
      context.moveTo(begin.x, begin.y)
      // ... existing for-loop, DRAW-state mouse line, and closed-fill
      // blocks move here UNCHANGED ...
    }
    context.stroke()
    context.restore()
```

(Note: `begin` is only referenced inside the else-branch — the closed-fill `lineTo(begin...)` moves with it.)

2f. Filter the vertex handles. In the FINISHED/RESHAPE/MOVE points loop (`for (let i = 0; i < numPoints; ++i) { ... point.draw(...) }`), add a membership skip at the top of the loop body:

```ts
          if (
            curveGroups !== null &&
            !curveGroups.some((g) => g.includes(i))
          ) {
            continue
          }
```

(The DRAW-state branch needs no change — a label mid-draw is `editing`, so `curveGroups` is null there.)

- [ ] **Step 3: Label2DList.redraw filter + pass-through**

In `app/src/drawable/2d/label2d_list.ts`:

3a. Extend the `redraw` signature (currently ends `lineWidthMultiplier: number = 1`):

```ts
    lineWidthMultiplier: number = 1,
    showCurvesOnly: boolean = false
```

with a JSDoc line `@param showCurvesOnly display only curved parts of lines`.

3b. After the hiddenCategories filter block (~line 260), add:

```ts
    // Curves-only display: keep only shapes that contain a bezier group.
    // The label being actively drawn/edited is exempt (same rule as the
    // category filter). Non-polygon label types are hidden while active.
    if (showCurvesOnly) {
      labelsToDraw = labelsToDraw.filter(
        (label) =>
          label.editing ||
          (label instanceof Polygon2D && label.hasCurves)
      )
    }
```

(`Polygon2D` is already imported in this file.)

3c. In the `labelsToDraw.forEach` draw call, append the new argument after `lineWidthMultiplier`:

```ts
        v.draw(
          ctx,
          ratio,
          mode,
          isTrackLinking,
          hideLabelTags ?? false,
          sessionMode,
          viewScale,
          lineWidthMultiplier,
          showCurvesOnly
        )
```

- [ ] **Step 4: Canvas pass-through**

In `app/src/components/label2d_canvas.tsx` `redraw()`, next to the existing `lineWidthMultiplier` extraction, add:

```ts
      const showCurvesOnly: boolean =
        "showCurvesOnly" in config &&
        (config as unknown as { showCurvesOnly?: boolean }).showCurvesOnly ===
          true
```

and append `showCurvesOnly` as the final argument of the `this._labelList.redraw(...)` call (after `lineWidthMultiplier`).

- [ ] **Step 5: Typecheck + lint**

`npx tsc --noEmit` → exit 0. `npx eslint -c .eslintrc.json --ext .ts,.tsx app/src/drawable/2d/label2d.ts app/src/drawable/2d/polygon2d.ts app/src/drawable/2d/label2d_list.ts app/src/components/label2d_canvas.tsx` → no NEW non-prettier errors vs HEAD. Also re-run the pure suites (they touch neighboring code):
```bash
npx jest app/test/drawable/curve_groups.test.ts app/test/drawable/polyline_cut_geometry.test.ts --env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js --coverage=false
```
Expected: PASS (35 tests).

- [ ] **Step 6: Commit**

```bash
git add app/src/drawable/2d/label2d.ts app/src/drawable/2d/polygon2d.ts app/src/drawable/2d/label2d_list.ts app/src/components/label2d_canvas.tsx
git commit -m "feat: curves-only rendering on view and control canvases

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Cut / delete-segment guards

**Files:**
- Modify: `app/src/components/viewer2d.tsx` (both toolbar button guards)
- Modify: `app/src/components/label2d_canvas.tsx` (both context-menu item guards)

**Interfaces:**
- Consumes: `showCurvesOnly` config field (Task 2).
- Produces: nothing new.

- [ ] **Step 1: Toolbar button guards**

In `app/src/components/viewer2d.tsx`, both `getCutButton` and `getDeleteSegmentButton` currently arm with:

```ts
            } else if (
              !Session.label2dList.isDrawingInProgress() &&
              !this.state.task.config.tracking
            ) {
```

Change BOTH to:

```ts
            } else if (
              !Session.label2dList.isDrawingInProgress() &&
              !this.state.task.config.tracking &&
              (this._viewerConfig as ImageViewerConfigType)?.showCurvesOnly !==
                true
            ) {
```

(`ImageViewerConfigType` is already imported in this file; `this._viewerConfig` is the base-class field the zoom code already casts the same way.)

- [ ] **Step 2: Context-menu item guards**

In `app/src/components/label2d_canvas.tsx` `render()`, both `MenuItem`s ("Cut polyline" and "Delete segment") currently use:

```ts
          disabled={
            Session.label2dList.isDrawingInProgress() ||
            this.state.task.config.tracking
          }
```

Change BOTH to:

```ts
          disabled={
            Session.label2dList.isDrawingInProgress() ||
            this.state.task.config.tracking ||
            (
              this.state.user.viewerConfigs[this.props.id] as unknown as {
                showCurvesOnly?: boolean
              }
            ).showCurvesOnly === true
          }
```

- [ ] **Step 3: Typecheck + lint**

`npx tsc --noEmit` → exit 0. `npx eslint -c .eslintrc.json --ext .tsx app/src/components/viewer2d.tsx app/src/components/label2d_canvas.tsx` → no NEW non-prettier errors vs HEAD.

- [ ] **Step 4: Commit**

```bash
git add app/src/components/viewer2d.tsx app/src/components/label2d_canvas.tsx
git commit -m "feat: cut tools refuse to arm while curves-only display is on

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Verification + docs

**Files:**
- Modify: `docs/polyline-feature-map.md`

- [ ] **Step 1: Full local verification**

```bash
npx tsc --noEmit
npx jest app/test/common/ app/test/drawable/curve_groups.test.ts app/test/drawable/polyline_cut_geometry.test.ts --env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js --coverage=false
```
Expected: tsc exit 0; jest — cut_state (2) + segment_delete_state (3) + curve_groups (8) + geometry (27) = 40 passing (plus the two known env-blocked synchronizer suites failing to LOAD if the whole common dir is swept — only the counts above matter).

- [ ] **Step 2: Runtime verification (repo verify skill)**

Follow `.claude/skills/verify/SKILL.md`: `npm run build`, use the running server (or start one), drive `http://localhost:8686/label?project_name=test2&task_index=0` in headless Chrome via CDP:
1. Draw a 3-vertex polyline; select it, hover a midpoint and press C to convert one segment to a curve (or reuse a task that already has curves).
2. Screenshot with "Curves only" OFF (full line visible).
3. Click the "Curves only" checkbox in the sidebar; screenshot — ONLY the curved stretch should render; the straight stretch and any fully-straight lines vanish.
4. Verify the cut and delete-segment toolbar buttons do NOT tint when clicked while the box is checked, and the context-menu items render disabled.
5. Uncheck — full lines return.

- [ ] **Step 3: Manual QA checklist (for the human)**

1. Mixed line shows only its curved stretch(es); fully-straight lines vanish; straight-LOOKING bezier segments appear.
2. Curve handles (anchors + control points) are visible and draggable; dragging shows the full line while editing, filter reapplies on release.
3. C-revert on a visible curve makes it disappear immediately.
4. Drawing a new line while ON works and renders fully until Enter.
5. Category checkboxes compose (hide a category → its curves hide too).
6. Cut/delete-segment buttons and menu items refuse to arm while ON; checking the box disarms an armed tool.
7. Zoom/pan with the filter on — curves track correctly.

- [ ] **Step 4: Update the feature map**

In `docs/polyline-feature-map.md` §7, add after the segment-delete bullet:

```
- "Curves only" sidebar checkbox — `showCurvesOnly` viewer-config flag
  (`toolbar.tsx` toggle → `label2d_canvas.tsx redraw` →
  `label2d_list.ts redraw` filter → `polygon2d.ts draw` curves-only branch,
  both canvases). Pure group finder: `app/src/drawable/2d/curve_groups.ts`
  (`curveGroupIndices`). Cut/delete-segment arming is guarded while on.
```

- [ ] **Step 5: Commit**

```bash
git add docs/polyline-feature-map.md
git commit -m "docs: map entry for the curves-only display toggle

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
