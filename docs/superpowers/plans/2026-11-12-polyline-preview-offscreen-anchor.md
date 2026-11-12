# Off-Screen Polyline Preview Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the live polyline/polygon preview segment visible while drawing at high zoom, even when the last committed anchor is outside the viewport.

**Architecture:** Preserve the existing high-zoom viewport-culling pipeline, but make `Polygon2D.bounds()` describe everything `Polygon2D.draw()` can paint. While the drawable is in `Polygon2DState.DRAW`, its bounds will include the transient cursor endpoint stored in `_mouseCoord`; finished shapes will continue to use committed points only. The canvas can then clip the off-screen part normally without a second preview renderer or a global culling bypass.

**Tech Stack:** TypeScript 4.2, React 17 class components, HTML Canvas 2D, Jest 26 with ts-jest, and the existing `Label2DList`/`Polygon2D` drawable architecture.

**Spec:** The user-reported behavior and the “Behavioral contract” section in this self-contained plan; no separate design document was requested.

## Global Constraints

- Do not change stored annotation coordinates, Redux label state, export/import data, or the click-to-add-vertex behavior.
- Do not disable viewport culling globally and do not exempt every selected or edited label from culling.
- Keep the existing culling threshold (`viewScale > 2`) and the existing `50 / ratio` viewport margin unchanged.
- Keep the current rotation rule unchanged: `Label2dCanvas` only supplies culling bounds when `viewRotation === 0`.
- `Polygon2D` implements both `POLYLINE_2D` and `POLYGON_2D`; the fix must behave correctly for both tools.
- `bounds()` must still return `null` before the first vertex is placed, preserving the existing “draw labels without bounds” behavior.
- Once drawing finishes, the stale cursor position must no longer enlarge the drawable bounds.
- Make no unrelated cleanup or refactoring in `label2d_canvas.tsx`, `label2d_list.ts`, or `polygon2d.ts`.
- Preserve unrelated local work. Do not commit or push unless the user later asks for it explicitly.

## Review Focus

- Drawing from lower-right to upper-left must produce the same normalized bounds as drawing in the opposite direction; Task 1 includes a reverse-direction bounds case.
- A preview segment whose two endpoints are outside the viewport but whose line crosses the viewport must not be culled; Task 1 includes a high-zoom culling-spy case.
- A finished polyline wholly outside the viewport must remain culled at high zoom; Task 1 includes a committed-label regression case.
- At `viewScale <= 2`, the existing no-culling behavior must remain unchanged; Task 1 verifies the same off-screen committed label is still passed to `draw()` at scale 2.
- Rotated views must remain usable and unchanged because culling is currently disabled for non-zero rotations; the manual UI matrix verifies 90° rotation as a regression check.

---

## Problem statement

When a user starts drawing a polyline, the application displays a live “rubber-band” segment from the last committed point to the cursor. If the user zooms or pans until the committed point is outside the viewport, that live segment disappears. Clicking the next point makes the segment appear again.

This is a rendering/culling defect, not annotation-data loss:

1. The first point remains in the in-progress drawable.
2. Mouse movement continues to update the cursor endpoint.
3. The second click still records the next point.
4. Only the transient preview is removed from the paint pass.

The expected behavior is that the visible portion of the preview remains painted anywhere the in-progress segment intersects the viewport. The browser canvas should clip the off-screen portion at the viewport edge.

## Current rendering path and confirmed root cause

### 1. Mouse movement updates a transient endpoint

`Label2dCanvas.onMouseMove` forwards the image-space cursor coordinate to `Label2DHandler.onMouseMove`:

- `app/src/components/label2d_canvas.tsx:981-1040`
- `app/src/drawable/2d/label2d_handler.ts:240-271`

For an actively drawn `Polygon2D`, `onMouseMove` stores that position in `_mouseCoord`:

- `app/src/drawable/2d/polygon2d.ts:764-774`

The cursor endpoint is intentionally transient; it is not yet a path point and is not written to Redux.

### 2. The draw method knows about the transient endpoint

`Polygon2D.draw()` builds a canvas path from `_points`, then appends a final `lineTo` using `_mouseCoord` while the state is `DRAW`:

- committed path: `app/src/drawable/2d/polygon2d.ts:500-524`
- live cursor segment: `app/src/drawable/2d/polygon2d.ts:526-529`
- live cursor handle: `app/src/drawable/2d/polygon2d.ts:573-586`

Therefore, the preview would render correctly if `draw()` were called.

### 3. High-zoom culling runs before `draw()`

At zoom levels above 2, with no display rotation, `Label2dCanvas.redraw()` calculates the visible image-space rectangle and passes it to `Label2DList.redraw()`:

- viewport calculation: `app/src/components/label2d_canvas.tsx:599-622`
- redraw call: `app/src/components/label2d_canvas.tsx:632-645`

`Label2DList.redraw()` filters labels by `label.bounds()` before invoking their `draw()` method:

- `app/src/drawable/2d/label2d_list.ts:296-318`

### 4. `Polygon2D.bounds()` omits what `draw()` paints

The current `Polygon2D.bounds()` scans only `_points`:

- `app/src/drawable/2d/polygon2d.ts:362-382`

After the first click, `_points` contains only the committed starting point. `_mouseCoord` may be inside the current viewport, and the segment between the two may cross the viewport, but neither fact is represented by `bounds()`.

Once the starting point falls outside the culling rectangle plus margin, `Label2DList` rejects the drawable. `Polygon2D.draw()` is never reached, so both the rubber-band segment and cursor handle disappear.

On the second click, the cursor position becomes a committed point in `_points`. The committed bounds now span both points, intersect the viewport, and pass the culling filter. This explains why the segment becomes visible immediately after the click.

### Root-cause statement

`Polygon2D.bounds()` violates the culling contract while drawing: it describes only committed geometry, whereas `Polygon2D.draw()` paints committed geometry plus the transient `_mouseCoord` endpoint. The culling decision is therefore made from incomplete geometry.

## Behavioral contract

The implementation is complete only when all of the following are true:

1. With at least one committed point and `Polygon2DState.DRAW`, `bounds()` encloses both every `_points` entry and `_mouseCoord`.
2. The bounds are normalized as `[minX, minY, width, height]` regardless of drawing direction.
3. Before any point is committed, `bounds()` remains `null`.
4. In `FINISHED`, `RESHAPE`, and `MOVE`, `bounds()` is based only on actual path points.
5. At high zoom, an in-progress segment that enters or crosses the viewport reaches `draw()`.
6. At high zoom, a finished shape wholly outside the viewport is still culled.
7. Clicking the next point commits exactly the same coordinates as before; the fix changes visibility only.
8. Polyline and polygon drawing share the corrected behavior.

## Approaches considered

### A. Include the transient cursor endpoint in `Polygon2D.bounds()` — recommended

Update the existing bounds calculation so `_mouseCoord` participates only while the state is `DRAW` and at least one point exists.

Advantages:

- Fixes the contract at its source: bounds match painted geometry.
- Preserves culling for committed and unrelated labels.
- Requires one small production-code change.
- Automatically supports both polylines and polygons.
- Lets the canvas perform its normal clipping; no line-clipping implementation is required.

Trade-off:

- Axis-aligned bounds may retain a long diagonal preview whose bounding box intersects the viewport even when the exact segment does not. That is a safe false positive affecting only one active drawable, not a correctness problem.

### B. Exempt `label.editing` from viewport culling

Add `if (label.editing) return true` in `Label2DList.redraw()`.

Why it is not selected:

- It is broader than the reported defect.
- It disables culling for any actively edited label, including potentially large committed polylines during move/reshape operations.
- It hides the mismatch between `bounds()` and `draw()` instead of correcting it.

### C. Draw the live segment as a separate `Label2dCanvas` overlay

Move or duplicate the preview rendering outside `Polygon2D.draw()`, similar to specialized arc or stamp overlays.

Why it is not selected:

- It duplicates color, line width, zoom scaling, cursor-handle, and control-canvas behavior.
- It creates two sources of truth for ordinary polygon/polyline drawing.
- It is significantly larger than the bounds correction needed for this bug.

### D. Add exact segment-versus-viewport clipping

Use a geometric intersection algorithm before drawing the preview.

Why it is not selected:

- Canvas 2D already clips paint outside its bitmap/clip region.
- The current culling system is deliberately based on inexpensive axis-aligned bounds.
- Exact clipping adds complexity without changing the visible result.

## File map

| Path | Action | Responsibility |
|---|---|---|
| `app/src/drawable/2d/polygon2d.ts` | Modify | Extend `bounds()` with the live cursor endpoint only during `DRAW`. |
| `app/test/drawable/polygon_viewport_bounds.test.ts` | Create | Pin bounds normalization, high-zoom preview retention, committed-label culling, and the scale threshold. |
| `app/src/drawable/2d/label2d_list.ts` | Read only | Existing viewport filter; do not add an editing exemption. |
| `app/src/components/label2d_canvas.tsx` | Read only | Existing viewport calculation and redraw path; no change required. |

---

### Task 1: Correct in-progress bounds and pin viewport-culling behavior

**Files:**

- Create: `app/test/drawable/polygon_viewport_bounds.test.ts`
- Modify: `app/src/drawable/2d/polygon2d.ts:362-382`

**Interfaces:**

- Consumes: `Polygon2DState.DRAW`, existing private `_points: PathPoint2D[]`, and existing private `_mouseCoord: Vector2D`.
- Preserves: `public bounds(): [number, number, number, number] | null`.
- Produces: bounds that cover all geometry painted by the ordinary draw path, including the live cursor endpoint only while drawing.
- Does not change: `Label2DList.redraw(...)`, `Polygon2D.draw(...)`, mouse-event routing, or persistent label shapes.

- [ ] **Step 1: Create the focused regression-test file**

Create `app/test/drawable/polygon_viewport_bounds.test.ts` using the existing drawable-test helpers:

```typescript
import * as action from "../../src/action/common"
import Session, { dispatch } from "../../src/common/session"
import { Key } from "../../src/const/common"
import { Context2D } from "../../src/drawable/util"
import { Size2D } from "../../src/math/size2d"
import {
  initializeTestingObjects,
  keyClick,
  mouseMove,
  mouseMoveClick
} from "./util"

const CANVAS_SIZE = new Size2D(1000, 1000)
const VIEW_CONTEXT = {} as Context2D
const CONTROL_CONTEXT = {} as Context2D
```

Keep each test independent by calling `initializeTestingObjects()` at its start. Select `labelType: 3` for a polyline and `labelType: 1` for a polygon, matching the existing cases in `polygon.test.ts`.

- [ ] **Step 2: Add a table-driven bounds test for both drawing directions and both tools**

Add a table with these exact cases:

| Tool | `labelType` | First point | Cursor | Expected bounds |
|---|---:|---:|---:|---:|
| polyline | 3 | `(100, 120)` | `(700, 620)` | `[100, 120, 600, 500]` |
| polyline | 3 | `(700, 620)` | `(100, 120)` | `[100, 120, 600, 500]` |
| polygon | 1 | `(100, 120)` | `(700, 620)` | `[100, 120, 600, 500]` |

For every case:

1. Initialize the test store and handler.
2. Dispatch `action.changeSelect({ labelType })`.
3. Place the first point with `mouseMoveClick(...)`.
4. Move without clicking to the cursor coordinate using `mouseMove(...)`.
5. Read `Session.label2dList.selectedLabels[0]`.
6. Assert that `isDrawingInProgress()` is `true`.
7. Assert that `bounds()` equals the expected tuple.

Current expected failure before the production fix:

```text
Expected: [100, 120, 600, 500]
Received: [100, 120, 0, 0]
```

- [ ] **Step 3: Add a culling test where both preview endpoints are outside but the segment crosses the viewport**

Use a polyline with:

- committed point `(100, 500)`;
- live cursor `(900, 500)`;
- high zoom `viewScale = 3`;
- viewport `[450, 450, 100, 100]`;
- ratio `1`;
- `drawControl = false`.

Spy on the active drawable's `draw` method and replace its implementation with a no-op so the test does not need a real canvas context:

```typescript
const active = Session.label2dList.selectedLabels[0]
const drawSpy = jest.spyOn(active, "draw").mockImplementation(() => undefined)

Session.label2dList.redraw(
  VIEW_CONTEXT,
  CONTROL_CONTEXT,
  1,
  false,
  false,
  undefined,
  3,
  [450, 450, 100, 100],
  undefined,
  undefined,
  false
)

expect(drawSpy).toHaveBeenCalledTimes(1)
```

This test fails before the fix because the current point-only bounds are `[100, 500, 0, 0]`, which the high-zoom filter removes. It passes after the preview endpoint expands the bounds to `[100, 500, 800, 0]`.

- [ ] **Step 4: Add a committed-label culling and scale-threshold regression test**

In a fresh test:

1. Select the polyline tool.
2. Click `(100, 100)` and `(200, 200)`.
3. Finish using `keyClick(label2dHandler, Key.ENTER)`.
4. Obtain the finished drawable from `Session.label2dList.labelList[0]`.
5. Spy on `draw()` with a no-op implementation.
6. Call `redraw()` with `viewScale = 3` and viewport `[800, 800, 100, 100]`; assert zero calls.
7. Clear the spy.
8. Call `redraw()` with the same viewport but `viewScale = 2`; assert one call with `drawControl = false`.

This protects both the performance optimization and its existing activation threshold.

- [ ] **Step 5: Run the new test and confirm the red state**

Preferred environment command:

```powershell
npx jest app/test/drawable/polygon_viewport_bounds.test.ts --runInBand --coverage=false
```

Expected before the fix:

- the bounds cases fail because `_mouseCoord` is missing;
- the high-zoom crossing-preview case fails because `draw()` is not called;
- the committed-label and scale-threshold assertions pass.

Environment note: the repository's default Jest setup starts `redis-server`, and jsdom loads the native `canvas` binding. On a machine without those dependencies, the suite can fail before collecting tests with `spawn redis-server ENOENT` or `Cannot find module '../build/Release/canvas.node'`. That is an environment failure, not the expected red test. Run the drawable suite in the normal project container/CI environment or install the repository's Redis and native canvas prerequisites before evaluating test results.

- [ ] **Step 6: Extend `Polygon2D.bounds()` with the transient endpoint**

Modify `app/src/drawable/2d/polygon2d.ts` without changing the public signature.

Required algorithm:

1. Retain the early `null` return when `_points.length === 0`.
2. Initialize min/max values as today.
3. Include every coordinate in `_points` as today.
4. If and only if `_state === Polygon2DState.DRAW`, also fold `_mouseCoord.x` and `_mouseCoord.y` into the same min/max values.
5. Return the same normalized `[minX, minY, maxX - minX, maxY - minY]` tuple.

Update the method comment so it states that drawing bounds include the live cursor endpoint because viewport culling must cover the preview segment.

Do not:

- push `_mouseCoord` into `_points`;
- add a temporary midpoint;
- alter `draw()`;
- alter `Label2DList.redraw()`;
- use `editing` as the condition, because `editing` also covers reshape/move states whose geometry is already represented by `_points`.

- [ ] **Step 7: Re-run the focused test and confirm the green state**

```powershell
npx jest app/test/drawable/polygon_viewport_bounds.test.ts --runInBand --coverage=false
```

Expected:

```text
PASS app/test/drawable/polygon_viewport_bounds.test.ts
```

- [ ] **Step 8: Run the existing polygon/drawable regression suite**

```powershell
npx jest app/test/drawable/polygon.test.ts app/test/drawable/handler.test.ts --runInBand --coverage=false
```

Expected: both suites pass in an environment with Redis and the native canvas binding.

- [ ] **Step 9: Run static verification**

```powershell
npx tsc --noEmit --pretty false
npx eslint -c .eslintrc.json app/src/drawable/2d/polygon2d.ts app/test/drawable/polygon_viewport_bounds.test.ts
git diff --check
```

Expected:

- TypeScript exits 0.
- ESLint introduces no new non-`prettier/prettier` violations. If the Windows checkout reports pre-existing CRLF formatting warnings, compare the changed files against the HEAD baseline rather than reformatting unrelated code.
- `git diff --check` exits 0.

- [ ] **Step 10: Build the application**

```powershell
npm run build
```

Expected: webpack exits 0 and emits the existing application bundles without a new dependency or configuration change.

- [ ] **Step 11: Inspect the focused diff and leave it local**

```powershell
git diff -- app/src/drawable/2d/polygon2d.ts app/test/drawable/polygon_viewport_bounds.test.ts
git status --short
```

Expected: only the intended source/test files plus any pre-existing user changes are present. Do not commit or push.

---

## Manual UI verification

Automated tests prove the bounds and culling decision, but this issue is user-visible and must also be checked in the running annotation UI.

### Primary reproduction and fix check

1. Start the application using the project's normal development configuration and open an imported image project.
2. Select the 2D polyline tool.
3. Zoom beyond 2× so viewport culling is active. Use 4× or greater to make the check unambiguous.
4. Click once near the center of the image to place the first point.
5. Move the pointer and confirm the live segment follows it.
6. Pan or zoom toward a distant location until the first point is completely outside the visible viewport.
7. Move the pointer within the new viewport.
8. Confirm the visible part of the live segment enters from the viewport edge and continues to the cursor; it must not disappear.
9. Click to place the second point.
10. Confirm the preview becomes the committed segment without jumping, changing color, or changing endpoint coordinates.
11. Press Enter to finish the polyline and confirm the stored line remains correct after navigating away and back.

### Direction matrix

Repeat the primary check with the first point leaving through each side:

| First point moves off | Cursor remains | Expected preview |
|---|---|---|
| left | right/center | enters from left edge |
| right | left/center | enters from right edge |
| top | lower/center | enters from top edge |
| bottom | upper/center | enters from bottom edge |

Also draw from lower-right toward upper-left to verify min/max normalization is direction-independent.

### Crossing-viewport case

1. Place the first point.
2. Move/pan far enough that the first point is outside one side of the viewport.
3. Continue moving toward the opposite side so the cursor endpoint also becomes outside while the segment crosses the visible image.
4. Confirm the portion crossing the viewport remains visible.

This scenario verifies the implementation uses the full preview bounds, not only “cursor is inside” special-casing.

### Regression matrix

- At 1× and 2× zoom, ordinary polyline drawing remains unchanged.
- At greater than 2× zoom, finished off-screen annotations still cull normally; pan back and confirm they reappear.
- Use the polygon tool and repeat the off-screen-first-point check; the closing preview must remain visible as well.
- Press Escape/undo during an off-screen preview and confirm the in-progress drawing is cancelled as before.
- Add several points, move the latest point offscreen, and confirm the preview starts from the last committed point rather than the first point.
- Rotate the view 90° and repeat a short draw/zoom/pan interaction; behavior must remain unchanged and no console error should occur.
- Toggle category/type visibility while drawing and confirm the actively drawn label follows the existing visibility rules.
- Watch the browser console for exceptions and confirm pointer movement remains smooth at high zoom.

## Acceptance criteria

- The focused Jest regression test passes in the supported test environment.
- Existing polygon and handler suites pass.
- TypeScript, targeted lint, `git diff --check`, and production build pass or any unrelated baseline failure is explicitly recorded.
- The live segment remains visible when its committed anchor is offscreen and any part of the segment intersects the viewport.
- The same behavior works for both polyline and polygon drawing.
- Finished labels remain subject to high-zoom viewport culling.
- No annotation schema, persisted coordinates, mouse gesture, culling threshold, or rotation behavior changes.
- The final diff is limited to `polygon2d.ts` and the focused regression test, aside from pre-existing local changes.
- No commit and no push are performed.

## Risk and rollback

The production change is intentionally local. The main risk is over-expanding bounds after drawing ends, which would reduce culling effectiveness; the state guard and committed-label regression test prevent that. A second risk is incorrect min/max handling when drawing toward smaller coordinates; the reverse-direction test covers it.

If the change must be rolled back, restore the original `Polygon2D.bounds()` implementation and remove the focused test file. No data migration, configuration rollback, or annotation repair is required because the fix never changes persisted data.
