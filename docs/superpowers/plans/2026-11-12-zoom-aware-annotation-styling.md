# Zoom-Aware Annotation Styling Regression Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make visible polyline/polygon edges, endpoint and vertex circles, highlighted circles, snap indicators, and 2D box handles become progressively thinner while zooming in and return exactly to their original appearance when zooming back to 1x, including when the canvas backing store is capped.

**Architecture:** Keep the existing 8192-pixel canvas backing-store limit and the existing `1 / sqrt(viewScale)` visual curve. Express visible annotation sizes in CSS-pixel terms, then convert them to backing-canvas pixels using the actual effective backing-to-CSS ratio returned by `updateCanvasScale`. Pass that ratio through the existing canvas -> label list -> drawable render path, and cover the conversion math with focused unit tests.

**Tech Stack:** TypeScript, React 17, HTML Canvas 2D, Jest/ts-jest, webpack.

**Spec:** `docs/superpowers/plans/2026-11-12-zoom-aware-annotation-styling.md#behavioral-specification`

## Global Constraints

- Keep `MAX_CANVAS_DIMENSION = 8192`; do not restore unbounded label canvases.
- Keep `MAX_SCALE = 60`; this fix must work at every currently supported zoom level.
- Keep the historical zoom curve `1 / sqrt(max(1, viewScale))`.
- Preserve the current 1x visual sizes exactly.
- Apply scaling only to visible `DrawMode.VIEW` styles; do not alter control-canvas color encoding.
- Do not change annotation coordinates, saved shapes, zoom focal-point math, pan behavior, or import/export data.
- Do not add a runtime dependency.
- Do not push or commit; leave all implementation changes local for user review.

## Review Focus

- A capped canvas with `effectiveUpResRatio < 1` must still produce progressively thinner visible styles; Task 1 pins the conversion and minimum-size math.
- Crossing the adaptive-resolution boundary from 3x to above 3x must not make lines or circles jump thicker; Tasks 1, 3, and 4 cover it.
- `lineWidthMultiplier` values from 0.5 through 4 must continue to affect polyline width without bypassing the minimum visible width; Task 3 covers it.
- Selected, highlighted, actively drawn, curved, and snap-preview polylines must use the same visible scaling; Task 3 and the UI matrix cover them.
- High-zoom hit-testing and endpoint snapping must continue to use the actual canvas ratio even though control-canvas colors are unchanged; Task 2 covers ratio propagation and snap-distance conversion.

---

## Behavioral specification

### Current failure

Zoom-aware styling was introduced by commit `a3eeb21d`. `Polygon2D.draw` and
`Box2D.draw` calculate:

```ts
const styleFactor = 1 / Math.sqrt(Math.max(1, viewScale))
```

Commit `7ad9118a` later allowed zoom up to 60x and capped every canvas backing
store at 8192 pixels. The CSS canvas continues growing while zooming, but the
backing canvas stops growing. Once capped, the browser stretches backing pixels
to CSS pixels.

The current code calculates only a backing-canvas size:

```text
current backing size = base size * zoom style factor
```

The browser displays it as:

```text
visible CSS size = current backing size / effectiveUpResRatio
```

When the cap makes `effectiveUpResRatio` smaller than `1`, dividing by that
ratio makes the line or circle larger again. At sufficiently high zoom the
canvas stretch cancels or reverses the intended thinning.

### Required behavior

1. At 1x zoom, annotations look exactly as they do before this fix.
2. At every zoom above 1x, visible line widths and visible circle radii follow
   the existing `1 / sqrt(viewScale)` curve until they reach their existing
   minimum visible size.
3. The visible result is independent of whether the canvas uses 2x backing
   resolution, 1x backing resolution, or a capped ratio below 1x.
4. Zooming out reverses the progression. Returning to 1x restores the exact
   original appearance without accumulating rounding error.
5. Active drawing previews and their temporary cursor point use the same sizes
   as committed annotations.
6. The line-thickness toolbar multiplier still applies to polyline edges.
7. Hidden control-canvas colors and annotation geometry are unchanged.

### Rendering invariant

The renderer must first determine the desired visible size and only then
convert it to backing pixels:

```text
reference CSS size = reference backing size / reference pixel ratio

desired CSS size = max(
  minimum reference backing size / reference pixel ratio,
  reference CSS size / sqrt(max(1, viewScale))
)

compensated backing size = desired CSS size * effectiveUpResRatio

actual backing size =
  compensated backing size                         when ratio >= 0.99
  max(minimum backing size, compensated backing size) when ratio < 0.99
```

The reference pixel ratio is `2`, matching the current low-zoom label canvas.
The `0.99` tolerance ignores harmless integer rounding around the normal `1x`
backing ratio. Below it, the backing-size floor is required because Canvas
rasterizes vectors before the browser stretches a capped canvas to its CSS
size; extremely small backing widths can disappear completely. Do not round
values above the floor.

Expected visible sizes for default polylines:

| Style | 1x | 4x | 9x | Minimum |
|---|---:|---:|---:|---:|
| Edge width (`8` reference backing px) | 4 CSS px | 2 CSS px | 1.33 CSS px | 1 CSS px |
| Vertex radius (`8`) | 4 CSS px | 2 CSS px | 1.33 CSS px | 1 CSS px |
| Highlighted radius (`12`) | 6 CSS px | 3 CSS px | 2 CSS px | 1.5 CSS px |

These CSS sizes stay the same while the backing grid can represent them. Under
severe capping, the original backing-pixel floors take precedence so the
annotation remains visible; it may appear thicker rather than disappear.

### Non-goals

- Changing how quickly zoom itself moves.
- Removing the backing-store cap or increasing GPU memory consumption.
- Redesigning the thickness toolbar.
- Changing the invisible control canvas to use thinner hit targets.
- Restructuring the canvases into viewport-sized tiles.
- Changing label tags, freeform-selection overlays, or unrelated tool UI.

## Approaches considered

### 1. Remove the canvas cap

This restores the old assumption that backing resolution never falls below CSS
resolution. It also restores the browser allocation failures and blank canvases
that caused the 8192-pixel cap to be added. Rejected.

### 2. Compensate styles using the effective backing-to-CSS ratio — selected

Keep the memory safety work and convert desired CSS sizes to actual backing
sizes at draw time. This is a focused change, preserves the zoom curve, and
works through both the 3x resolution transition and the 8192-pixel cap.

### 3. Replace whole-image canvases with viewport-sized overlay canvases

This would keep backing resolution near the viewport size and simplify some
high-zoom rendering behavior. It affects coordinate transforms, panning,
rotation, hit-testing, overlays, and every drawing tool. It is a reasonable
future architecture but too broad for this regression fix. Deferred.

---

## File structure

- **Create** `app/src/common/canvas_style_scale.ts` — pure conversion from
  reference backing sizes to zoom-aware sizes for the current backing ratio.
- **Create** `app/test/common/canvas_style_scale.test.ts` — focused regression
  tests for normal, adaptive, and capped canvas ratios.
- **Modify** `app/src/view_config/image.ts` — share the reference ratio constant
  and correct comments that currently claim label ratios are always at least 1.
- **Modify** `app/src/components/label2d_canvas.tsx` — pass `_upResRatio`, the
  actual effective ratio returned by `updateCanvasScale`, into label rendering.
- **Modify** `app/src/drawable/2d/label2d_list.ts` — retain and forward the
  effective ratio and use it for screen-space snap-distance conversion.
- **Modify** `app/src/drawable/2d/label2d.ts` — extend the drawing contract with
  an optional `effectiveUpResRatio` argument.
- **Modify** `app/src/drawable/2d/polygon2d.ts` — scale visible edge widths,
  normal/highlighted vertices, temporary drawing points, and snap indicators.
- **Modify** `app/src/drawable/2d/path_point2d.ts` — allow the visible point
  outline width to be supplied in compensated backing pixels.
- **Modify** `app/src/drawable/2d/box2d.ts` — apply the same conversion to 2D
  box borders and visible handles.

---

### Task 1: Define and test backing-to-CSS style conversion

**Files:**
- Create: `app/src/common/canvas_style_scale.ts`
- Create: `app/test/common/canvas_style_scale.test.ts`
- Modify: `app/src/view_config/image.ts:58-69`

**Interfaces:**
- Produces: `REFERENCE_CANVAS_PIXEL_RATIO: number` with value `2`.
- Produces: `zoomAwareBackingSize(baseSize: number, minimumSize: number, viewScale: number, effectiveUpResRatio: number): number`.
- Consumes: no application state or DOM APIs; this module must remain a pure numeric utility.

- [ ] **Step 1: Write the failing unit tests**

Create `app/test/common/canvas_style_scale.test.ts` with these cases:

```ts
import {
  REFERENCE_CANVAS_PIXEL_RATIO,
  zoomAwareBackingSize
} from "../../src/common/canvas_style_scale"

describe("zoomAwareBackingSize", () => {
  test("preserves the reference backing size at 1x", () => {
    expect(REFERENCE_CANVAS_PIXEL_RATIO).toBe(2)
    expect(zoomAwareBackingSize(6, 1, 1, 2)).toBeCloseTo(6)
    expect(zoomAwareBackingSize(8, 2, 1, 2)).toBeCloseTo(8)
  })

  test("uses the existing square-root zoom curve", () => {
    expect(zoomAwareBackingSize(6, 1, 4, 2)).toBeCloseTo(3)
    expect(zoomAwareBackingSize(8, 2, 4, 2)).toBeCloseTo(4)
  })

  test("keeps the visible CSS size independent of backing ratio", () => {
    const cssAt2x = zoomAwareBackingSize(6, 1, 4, 2) / 2
    const cssAt1x = zoomAwareBackingSize(6, 1, 4, 1) / 1
    const cssWhenCapped = zoomAwareBackingSize(6, 1, 4, 0.5) / 0.5
    expect(cssAt2x).toBeCloseTo(1.5)
    expect(cssAt1x).toBeCloseTo(cssAt2x)
    expect(cssWhenCapped).toBeCloseTo(cssAt2x)
  })

  test("applies the minimum in CSS pixels after the canvas is capped", () => {
    const visible = zoomAwareBackingSize(6, 1, 100, 0.25) / 0.25
    expect(visible).toBeCloseTo(0.5)
  })

  test("treats zoom below 1 as 1x", () => {
    expect(zoomAwareBackingSize(8, 2, 0.5, 2)).toBeCloseTo(8)
  })
})
```

- [ ] **Step 2: Run the test and verify the red state**

Run:

```powershell
npx jest app/test/common/canvas_style_scale.test.ts --runInBand
```

Expected: FAIL because `canvas_style_scale` does not exist. If the local Jest
global setup stops with `spawn redis-server ENOENT`, record that environment
limitation and run the test in CI or with the project's Redis prerequisite;
do not interpret the setup failure as a passing test.

- [ ] **Step 3: Implement the pure conversion utility**

Add the exact public interface shown above. Its body must:

1. Clamp zoom with `Math.max(1, viewScale)`.
2. Divide `baseSize` and `minimumSize` by
   `REFERENCE_CANVAS_PIXEL_RATIO` to obtain CSS sizes.
3. Apply `1 / Math.sqrt(zoomScale)` to the base CSS size.
4. Apply the minimum in CSS units.
5. Multiply the result by `effectiveUpResRatio` without rounding.
6. Clamp the result to `minimumSize` backing pixels so Canvas rasterization
   cannot drop the annotation before CSS stretching.

The caller always supplies the real positive effective ratio. Do not silently
replace a capped ratio below `1` with `1`.

- [ ] **Step 4: Make `image.ts` use the shared reference ratio**

Import `REFERENCE_CANVAS_PIXEL_RATIO` and change `getUpResRatio` to return it
for zoom levels at or below 3. Keep the above-3x value `1` unchanged:

```ts
return viewScale > 3 ? 1 : REFERENCE_CANVAS_PIXEL_RATIO
```

Update the `effectiveUpResRatio` comments around `updateCanvasScale` to state
that the ratio may be below `1` after `MAX_CANVAS_DIMENSION` caps the backing
store, and that visible vector styles compensate for it.

- [ ] **Step 5: Run focused verification**

Run:

```powershell
npx jest app/test/common/canvas_style_scale.test.ts --runInBand
npx tsc --noEmit -p tsconfig.json
```

Expected: all five Jest tests PASS and TypeScript exits `0`.

- [ ] **Step 6: Inspect the local diff without committing**

Run:

```powershell
git diff -- app/src/common/canvas_style_scale.ts app/test/common/canvas_style_scale.test.ts app/src/view_config/image.ts
git status --short
```

Expected: only the planned local files are listed; do not commit or push.

---

### Task 2: Carry the actual effective ratio through the drawing pipeline

**Files:**
- Modify: `app/src/components/label2d_canvas.tsx:632-645`
- Modify: `app/src/drawable/2d/label2d_list.ts:85-99,242-258,320-338,440-447`
- Modify: `app/src/drawable/2d/label2d.ts:295-318`

**Interfaces:**
- Consumes: `Label2dCanvas._upResRatio`, already populated from
  `updateCanvasScale(...)[4]`.
- Produces: trailing `effectiveUpResRatio?: number` on `Label2D.draw`.
- Produces: trailing `effectiveUpResRatio: number = REFERENCE_CANVAS_PIXEL_RATIO`
  on `Label2DList.redraw`.

- [ ] **Step 1: Extend the abstract drawable contract**

Append `effectiveUpResRatio?: number` after `curvesOnly?: boolean` in
`Label2D.draw`. Document it as actual backing pixels per CSS pixel, including
values below `1` when the backing store is capped.

Appending rather than inserting avoids changing the meaning of existing
positional arguments.

- [ ] **Step 2: Extend `Label2DList.redraw`**

Append:

```ts
effectiveUpResRatio: number = REFERENCE_CANVAS_PIXEL_RATIO
```

Store it in a new field:

```ts
private _lastEffectiveUpResRatio: number
```

Initialize the field to `REFERENCE_CANVAS_PIXEL_RATIO`, update it on every
`redraw`, and pass the value as the final argument of every `v.draw(...)` call.

- [ ] **Step 3: Pass the ratio from `Label2dCanvas`**

At the existing `_labelList.redraw(...)` call, append `this._upResRatio` after
`showCurvesOnly`. Do not recompute the ratio from `viewScale`; the stored value
already includes the adaptive ratio and the 8192-pixel cap.

- [ ] **Step 4: Correct screen-space endpoint snap conversion**

In `Label2DList.findNearestEndpoint`, replace the guessed ratio:

```ts
const upResRatio = this._lastViewScale > 3 ? 1 : 2
const displayToImageRatio = this._lastRatio / upResRatio
```

with:

```ts
const displayToImageRatio =
  this._lastRatio / this._lastEffectiveUpResRatio
```

This keeps the 15-screen-pixel snap radius correct on capped canvases and
removes duplicated knowledge of the 3x resolution boundary.

- [ ] **Step 5: Type-check the interface propagation**

Run:

```powershell
npx tsc --noEmit -p tsconfig.json
```

Expected: exit `0`; drawables that do not use the optional trailing argument
remain source-compatible.

- [ ] **Step 6: Inspect the local diff without committing**

Run `git diff --check` and `git status --short`. Do not commit or push.

---

### Task 3: Apply compensated sizes to visible polylines and polygons

**Files:**
- Modify: `app/src/drawable/2d/polygon2d.ts:200-240,385-447,573-628,655-657`
- Modify: `app/src/drawable/2d/path_point2d.ts:8-15,182-205`
- Test: `app/test/common/canvas_style_scale.test.ts`

**Interfaces:**
- Consumes: `zoomAwareBackingSize(...)` from Task 1.
- Consumes: final `effectiveUpResRatio` draw argument from Task 2.
- Produces: optional `strokeWidth?: number` on `PathPoint2DStyle`.

- [ ] **Step 1: Add multiplier coverage to the helper test**

Add a test proving that the polyline multiplier is applied to the base width
while the visible minimum remains stable:

```ts
test("supports a line-width multiplier without changing the visible floor", () => {
  const thin = zoomAwareBackingSize(6 * 0.5, 1, 4, 1)
  const thick = zoomAwareBackingSize(6 * 2, 1, 4, 1)
  const floored = zoomAwareBackingSize(6 * 0.5, 1, 1000, 0.25) / 0.25
  expect(thin).toBeCloseTo(0.75)
  expect(thick).toBeCloseTo(3)
  expect(floored).toBeCloseTo(0.5)
})
```

Run the focused test. It should pass using Task 1's generic helper.

- [ ] **Step 2: Replace polyline backing-only scaling**

Append the effective-ratio argument to `Polygon2D.draw`, defaulting it to
`REFERENCE_CANVAS_PIXEL_RATIO` for direct/test callers.

Inside `DrawMode.VIEW`, replace the three current `Math.max(... *
styleFactor)` expressions with `zoomAwareBackingSize` calls:

- normal point: base radius `8`, minimum reference radius `2`;
- highlighted point: base radius `12`, minimum reference radius `3`;
- edge: base width `6 * lineWidthMultiplier`, minimum reference width `1`.

Read the base values from the assigned style objects rather than duplicating
`8`, `12`, and `6` where practical. Apply the multiplier before calling the
helper. Leave `DrawMode.CONTROL` styles unchanged.

- [ ] **Step 3: Compensate visible point outlines**

Add optional `strokeWidth?: number` to `PathPoint2DStyle`. In
`PathPoint2D.draw`, prefer `style.strokeWidth` when supplied; otherwise retain
the existing radius-derived fallback so other callers do not change.

In the polygon VIEW styles, calculate outline widths with the same helper:

- normal outline: base `1`, minimum `0.75` reference backing pixels;
- highlighted outline: base `1.5`, minimum `0.75` reference backing pixels.

This prevents the existing fixed `0.75` backing-pixel floor from becoming a
thick black ring when a capped canvas is stretched.

- [ ] **Step 4: Compensate snap-indicator geometry**

Change `drawSnapIndicator` to receive the effective ratio, not only a raw
`styleFactor`. Convert all visible sizes through `zoomAwareBackingSize`:

- halo radius: base `16`, minimum `4`;
- endpoint radius: base `8`, minimum `2`;
- center radius: base `3`, minimum `1`;
- stroke width: base `2`, minimum `1`;
- dash length: base `4`, minimum `2`.

Pass `viewScale` and `effectiveUpResRatio` from `draw`. The snap target's
coordinates and merge/connect behavior must not change.

- [ ] **Step 5: Confirm active drawing uses the same styles**

Trace the `Polygon2DState.DRAW` branch and verify that the temporary cursor
point and every placed vertex reuse the compensated `pointStyle` and
`highPointStyle`. Do not add a parallel drawing-only formula.

- [ ] **Step 6: Run verification**

Run:

```powershell
npx jest app/test/common/canvas_style_scale.test.ts --runInBand
npx tsc --noEmit -p tsconfig.json
```

Expected: helper tests PASS and TypeScript exits `0`.

- [ ] **Step 7: Inspect the local diff without committing**

Run `git diff --check` and `git status --short`. Do not commit or push.

---

### Task 4: Apply the same invariant to visible 2D boxes

**Files:**
- Modify: `app/src/drawable/2d/box2d.ts:110-147`

**Interfaces:**
- Consumes: `zoomAwareBackingSize(...)` from Task 1.
- Consumes: final `effectiveUpResRatio` draw argument from Task 2.

- [ ] **Step 1: Accept the trailing draw arguments**

Extend `Box2D.draw` through the complete `Label2D.draw` signature so it can
receive the final effective ratio. Name unused positional parameters with an
underscore. Default the effective ratio to `REFERENCE_CANVAS_PIXEL_RATIO`.

- [ ] **Step 2: Replace VIEW-mode backing-only scaling**

Use `zoomAwareBackingSize` for:

- normal handle radius: base `8`, minimum `2`;
- highlighted handle radius: base `12`, minimum `3`;
- rectangle border width: base `4`, minimum `1`.

Leave `DrawMode.CONTROL` styles and selecting-mode color encoding unchanged.

- [ ] **Step 3: Type-check**

Run:

```powershell
npx tsc --noEmit -p tsconfig.json
```

Expected: exit `0`.

- [ ] **Step 4: Inspect the local diff without committing**

Run `git diff --check` and `git status --short`. Do not commit or push.

---

### Task 5: Build and validate the complete behavior in the UI

**Files:**
- Verify all files modified in Tasks 1-4.
- Do not create permanent screenshot or diagnostic files.

**Interfaces:**
- Consumes: completed rendering pipeline and compensated styles.
- Produces: verification evidence only; no additional feature scope.

- [ ] **Step 1: Run the focused automated test**

```powershell
npx jest app/test/common/canvas_style_scale.test.ts --runInBand
```

Expected: all tests PASS. If local Redis setup prevents Jest from starting,
run this command in the repository's normal CI/test environment before calling
the implementation complete.

- [ ] **Step 2: Run static verification**

```powershell
npx tsc --noEmit -p tsconfig.json
npm run lint -- --quiet
```

Expected: both commands exit `0` with no new warning or error from modified
files.

- [ ] **Step 3: Run the production build**

```powershell
npm run build
```

Expected: webpack reports `compiled successfully` for the application and
server bundles.

- [ ] **Step 4: Reset display-only line width before manual validation**

Open a project containing an editable polyline, use the toolbar's reset-line-
width action so `lineWidthMultiplier` is `1`, select the polyline, and start
from 1x zoom.

- [ ] **Step 5: Verify progressive thinning at ordinary ratios**

At 1x, 2x, 3x, and 4x, inspect or capture the same line and vertex. Confirm:

- edge width decreases progressively;
- endpoint, vertex, midpoint, and highlighted circles decrease progressively;
- there is no thicker jump when crossing from 3x to above 3x;
- the active cursor-following line and temporary point match committed labels.

- [ ] **Step 6: Verify the capped-canvas regression case**

Use a large frame and zoom until the visible label canvas reports an effective
ratio below `1`:

```js
const canvas = document.querySelector("canvas[class*='label2d_canvas']")
canvas.width / canvas.getBoundingClientRect().width
```

Continue zooming at several levels up to the useful maximum. The line and
circles must continue becoming thinner or remain at their documented minimum;
they must never become thicker because the backing canvas is stretched.

- [ ] **Step 7: Verify zoom-out restoration**

Zoom back through the same levels to 1x. Confirm the progression reverses and
the 1x edge and circles match their initial appearance exactly.

- [ ] **Step 8: Verify related modes**

Check each of the following at low and high zoom:

- open polyline while drawing;
- committed straight polyline;
- curved polyline and its cyan curve controls;
- closed polygon;
- selected and highlighted vertices;
- endpoint snap indicator;
- 2D box border and handles;
- line-width multiplier at `0.5`, `1`, and `2`;
- hover, click selection, vertex dragging, and endpoint snapping.

Expected: visible styles scale; geometry and interaction remain correct.

- [ ] **Step 9: Review the final local diff**

```powershell
git diff --check
git diff --stat
git status --short --branch
```

Expected: no whitespace errors, only planned files are modified/created, and
there are no generated screenshots or temporary scripts. Do not commit or
push.

---

## Acceptance criteria

- The manual reproduction reported on `dev` no longer occurs.
- Line and circle sizes visibly decrease during zoom-in and restore during
  zoom-out.
- The behavior remains monotonic across the 3x backing-resolution switch.
- The behavior remains correct when `effectiveUpResRatio < 1` because of the
  8192-pixel cap.
- 1x appearance is unchanged.
- Minimum visible sizes are stable in CSS pixels while the backing grid can
  represent them; under severe capping, backing-pixel safety floors prevent
  annotations from disappearing.
- Active, selected, highlighted, straight, and curved polylines behave alike.
- 2D boxes use the same invariant.
- Thickness controls, hit-testing, dragging, and endpoint snapping still work.
- Focused tests, TypeScript, lint, and webpack verification pass.
- No commit or push is performed.

## Implementation cautions

- Do not derive effective ratio from `viewScale`; use the value returned by
  `updateCanvasScale`, because the 8192-pixel cap depends on canvas dimensions.
- Do not clamp `effectiveUpResRatio` to `1`; ratios below `1` are the exact case
  this fix must handle.
- Do not round compensated widths or radii above the backing-pixel floor.
- Apply the CSS minimum before multiplying, then apply the existing backing-
  pixel minimum afterward so severely capped canvases degrade to thicker but
  visible annotations.
- Do not scale annotation coordinates with the style helper. Coordinates must
  continue using `displayToImageRatio * effectiveUpResRatio`.
- Keep the control canvas encoded with exact colors. Visible outlines must
  never be painted into it.
- Do not use `window.devicePixelRatio` as a substitute for the effective ratio;
  the application has its own adaptive and capped backing-store calculation.

## Self-review

- The root cause is addressed at the backing-to-CSS boundary rather than by
  removing the safety cap.
- The plan covers the complete ratio flow from `updateCanvasScale` to both
  drawable types.
- Tests pin 1x preservation, square-root scaling, ratios `2`, `1`, and below
  `1`, CSS-space minimums, and the line-width multiplier.
- UI validation covers the original reproduction, the 3x transition, the
  capped backing store, active drawing, zoom-out restoration, and interactions.
- Parameter additions are trailing and optional/defaulted, preserving existing
  drawable callers.
- The plan explicitly keeps all changes local and includes no commit step.
