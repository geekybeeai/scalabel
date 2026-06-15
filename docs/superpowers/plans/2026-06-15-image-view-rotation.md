# Display-Only 90° Image Rotation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two toolbar buttons rotate the 2D image view 90° left/right for easier annotation, while annotation coordinates are always stored/exported in the original (unrotated) image frame.

**Architecture:** Rotation is view state (`rotation` on the image viewer config). The canvas DOM stays axis-aligned (only its pixel dimensions swap for 90°/270°), so the existing cursor-zoom and pan are untouched. Drawing is rotated by a canvas-context `rotate` on the image, label, and control canvases; pointer input is un-rotated back to the original frame; hit-testing re-rotates. Pure `rotatePoint`/`unrotatePoint` helpers do the exact, lossless 90° coordinate math.

**Tech Stack:** TypeScript, React, Redux, Canvas 2D, Material-UI, Jest.

Reference spec: [docs/superpowers/specs/2026-06-15-image-view-rotation-design.md](../specs/2026-06-15-image-view-rotation-design.md)

**Environment note:** Jest *drawable/component* tests cannot run here (native `canvas`/`redis` missing). Task 1's helpers are **pure math** (only depend on `Vector2D`) and DO run. Tasks 2-5 are verified by `npx tsc --noEmit`, `npm run lint`, and **runtime in the editor** — call those out, don't claim jest passed.

Common commands (repo root `d:\Nikhil\Projects\GitHub\scalabel`):
- One test: `npx jest app/test/view_config/image_rotation.test.ts`
- Typecheck: `npx tsc --noEmit`
- Lint: `npm run lint`

---

## File Structure

| File | Responsibility |
|------|----------------|
| `app/src/types/state.ts` *(modify)* | Add `rotation?: number` to `ImageViewerConfigType`. |
| `app/src/view_config/image.ts` *(modify)* | `rotatePoint`/`unrotatePoint` helpers; rotation-aware `updateCanvasScale` (dim swap); `drawImageOnCanvas` context rotation. |
| `app/src/components/image_canvas.tsx` *(modify)* | Pass rotation into the image draw. |
| `app/src/components/label2d_canvas.tsx` *(modify)* | Rotate label+control contexts before redraw; un-rotate pointer in `getMousePos`; re-rotate in `fetchHandleId`. |
| `app/src/components/viewer2d.tsx` *(modify)* | Two rotate buttons. |
| `app/test/view_config/image_rotation.test.ts` *(new)* | Unit tests for the rotation helpers. |

---

### Task 1: Rotation state + pure coordinate helpers

**Files:**
- Modify: `app/src/types/state.ts` (add field after `lineWidthMultiplier`, ~line 224)
- Modify: `app/src/view_config/image.ts` (add two exported functions near the other coord helpers, after `toImageCoords` ~line 100)
- Test: `app/test/view_config/image_rotation.test.ts` (new)

- [ ] **Step 1: Add the `rotation` field**

In `app/src/types/state.ts`, inside `ImageViewerConfigType` (after the `lineWidthMultiplier?: number` line):

```typescript
  /** Display-only view rotation in degrees: 0 | 90 | 180 | 270. Never exported. */
  rotation?: number
```

- [ ] **Step 2: Write the failing test**

Create `app/test/view_config/image_rotation.test.ts`:

```typescript
import { rotatePoint, unrotatePoint } from "../../src/view_config/image"
import { Vector2D } from "../../src/math/vector2d"

const W = 200
const H = 100
const POINTS: Array<[number, number]> = [
  [0, 0],
  [W, 0],
  [0, H],
  [W, H],
  [50, 30]
]

describe("image rotation helpers", () => {
  test("unrotatePoint inverts rotatePoint for every angle", () => {
    for (const rotation of [0, 90, 180, 270]) {
      for (const [x, y] of POINTS) {
        const back = unrotatePoint(
          rotatePoint(new Vector2D(x, y), rotation, W, H),
          rotation,
          W,
          H
        )
        expect(back.x).toBeCloseTo(x)
        expect(back.y).toBeCloseTo(y)
      }
    }
  })

  test("90 CW maps original corners into the H x W display frame", () => {
    // (0,0) -> (H,0); (0,H) -> (0,0); (W,H) -> (0,W)
    expect(rotatePoint(new Vector2D(0, 0), 90, W, H)).toEqual(new Vector2D(H, 0))
    expect(rotatePoint(new Vector2D(0, H), 90, W, H)).toEqual(new Vector2D(0, 0))
    expect(rotatePoint(new Vector2D(W, H), 90, W, H)).toEqual(new Vector2D(0, W))
  })

  test("rotation 0 is identity", () => {
    expect(rotatePoint(new Vector2D(12, 34), 0, W, H)).toEqual(
      new Vector2D(12, 34)
    )
  })
})
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `npx jest app/test/view_config/image_rotation.test.ts`
Expected: FAIL — `rotatePoint is not exported` / not a function.

- [ ] **Step 4: Implement the helpers**

In `app/src/view_config/image.ts`, add after `toImageCoords` (~line 100):

```typescript
/**
 * Rotate a point from the original image frame into the displayed (rotated)
 * frame. Exact, lossless 90° math. `rotation` is 0 | 90 | 180 | 270 (CW); `w`/`h`
 * are the ORIGINAL image width/height.
 *
 * @param p the point in original-image coordinates
 * @param rotation clockwise rotation in degrees (0/90/180/270)
 * @param w original image width
 * @param h original image height
 */
export function rotatePoint(
  p: Vector2D,
  rotation: number,
  w: number,
  h: number
): Vector2D {
  switch (rotation) {
    case 90:
      return new Vector2D(h - p.y, p.x)
    case 180:
      return new Vector2D(w - p.x, h - p.y)
    case 270:
      return new Vector2D(p.y, w - p.x)
    default:
      return new Vector2D(p.x, p.y)
  }
}

/**
 * Inverse of rotatePoint: map a point in the displayed (rotated) frame back to
 * the original image frame. `w`/`h` are the ORIGINAL image width/height.
 *
 * @param p the point in displayed (rotated) coordinates
 * @param rotation clockwise rotation in degrees (0/90/180/270)
 * @param w original image width
 * @param h original image height
 */
export function unrotatePoint(
  p: Vector2D,
  rotation: number,
  w: number,
  h: number
): Vector2D {
  switch (rotation) {
    case 90:
      return new Vector2D(p.y, h - p.x)
    case 180:
      return new Vector2D(w - p.x, h - p.y)
    case 270:
      return new Vector2D(w - p.y, p.x)
    default:
      return new Vector2D(p.x, p.y)
  }
}
```

Confirm `Vector2D` is already imported in `image.ts` (it is — used throughout).

- [ ] **Step 5: Run the test to verify it passes**

Run: `npx jest app/test/view_config/image_rotation.test.ts`
Expected: PASS (3 tests). If it fails to load due to a transitive `canvas` import, note it and confirm via `npx tsc --noEmit`; the math is still covered by review.

- [ ] **Step 6: Typecheck + commit**

Run: `npx tsc --noEmit` (exit 0)

```bash
git add app/src/types/state.ts app/src/view_config/image.ts app/test/view_config/image_rotation.test.ts
git commit -m "feat: add view rotation state + rotate/unrotate coordinate helpers"
```

---

### Task 2: Rotation-aware canvas sizing

**Files:** Modify `app/src/view_config/image.ts` (`updateCanvasScale`, ~line 318-333).

> No jest test (needs a real canvas/image). Verified by `tsc` + runtime. Behavior: when rotation is 90°/270°, the canvas is sized to the *swapped* image dimensions so letterboxing and `displayToImageRatio` are correct for the rotated aspect; 0°/180° are unchanged.

- [ ] **Step 1: Use rotated dimensions for sizing**

In `updateCanvasScale`, replace the block that reads the image size and computes the ratio (currently):

```typescript
  // Resize canvas
  const item = getCurrentItem(state)
  const image = Session.images[item.index][config.sensor]
  const ratio = image.width / image.height
  let canvasHeight
  let canvasWidth
  let displayToImageRatio
  if (displayRect.width / displayRect.height > ratio) {
    canvasHeight = displayRect.height * config.viewScale
    canvasWidth = canvasHeight * ratio
    displayToImageRatio = canvasHeight / image.height
  } else {
    canvasWidth = displayRect.width * config.viewScale
    canvasHeight = canvasWidth / ratio
    displayToImageRatio = canvasWidth / image.width
  }
```

with (introduces `imgW`/`imgH` that swap for 90°/270°):

```typescript
  // Resize canvas
  const item = getCurrentItem(state)
  const image = Session.images[item.index][config.sensor]
  // For a 90°/270° view rotation the displayed image is the original turned on
  // its side, so the canvas is sized to the swapped dimensions. The canvas DOM
  // itself stays axis-aligned (the content is rotated via the drawing context).
  const rotated = config.rotation === 90 || config.rotation === 270
  const imgW = rotated ? image.height : image.width
  const imgH = rotated ? image.width : image.height
  const ratio = imgW / imgH
  let canvasHeight
  let canvasWidth
  let displayToImageRatio
  if (displayRect.width / displayRect.height > ratio) {
    canvasHeight = displayRect.height * config.viewScale
    canvasWidth = canvasHeight * ratio
    displayToImageRatio = canvasHeight / imgH
  } else {
    canvasWidth = displayRect.width * config.viewScale
    canvasHeight = canvasWidth / ratio
    displayToImageRatio = canvasWidth / imgW
  }
```

- [ ] **Step 2: Typecheck + lint**

Run: `npx tsc --noEmit` (exit 0)
Run: `npm run lint`

- [ ] **Step 3: Commit**

```bash
git add app/src/view_config/image.ts
git commit -m "feat: size the image canvas to the rotated dimensions"
```

---

### Task 3: Rotate the image bitmap when drawing

**Files:** Modify `app/src/view_config/image.ts` (`drawImageOnCanvas`, ~line 148) and `app/src/components/image_canvas.tsx` (the `drawImageOnCanvas` call, ~line 156).

> No jest test (canvas draw). Verified by `tsc` + runtime. Behavior: the image paints rotated to fill the (already swapped-size) canvas.

- [ ] **Step 1: Accept a rotation and apply a context transform in `drawImageOnCanvas`**

Read the current `drawImageOnCanvas` (it does `clearCanvas`, sets smoothing, then draws the bitmap/image to `canvas.width × canvas.height`). Add a `rotation` parameter and wrap the draw in a save/rotate/restore. Change the signature and the draw:

Signature — add `rotation: number = 0` as the last parameter:

```typescript
export function drawImageOnCanvas(
  canvas: HTMLCanvasElement,
  context: CanvasRenderingContext2D,
  image: HTMLImageElement,
  itemIndex?: number,
  sensorId?: number,
  rotation: number = 0
): void {
```

Immediately after `clearCanvas(canvas, context)` at the top of the function, apply the rotation transform, and add a matching `context.restore()` at the very end of the function (after the existing draw). Insert after `clearCanvas`:

```typescript
  // Rotate the drawing context so the bitmap paints turned. The canvas is
  // already sized to the rotated dimensions (see updateCanvasScale), so for
  // 90°/270° we translate by the swapped axis before rotating.
  context.save()
  if (rotation === 90) {
    context.translate(canvas.width, 0)
    context.rotate(Math.PI / 2)
  } else if (rotation === 180) {
    context.translate(canvas.width, canvas.height)
    context.rotate(Math.PI)
  } else if (rotation === 270) {
    context.translate(0, canvas.height)
    context.rotate(-Math.PI / 2)
  }
```

Then **the existing draw calls must draw into the un-rotated content box.** For 90°/270° the content box is `canvas.height × canvas.width` (swapped). Replace the draw target the function uses (wherever it does `drawImage(..., canvas.width, canvas.height)`) with swapped dimensions when rotated:

```typescript
  const drawW = rotation === 90 || rotation === 270 ? canvas.height : canvas.width
  const drawH = rotation === 90 || rotation === 270 ? canvas.width : canvas.height
```

and use `drawW`/`drawH` as the destination width/height in the `drawImage(...)` / `drawImageBitmap` call(s) instead of `canvas.width`/`canvas.height`. Finally, at the very end of the function add:

```typescript
  context.restore()
```

(If the function returns early in an async ImageBitmap branch, ensure that branch also performs the save/transform/draw/restore symmetrically — wrap the actual `drawImage` of both the bitmap path and the fallback `image` path with the same `drawW`/`drawH` + the single save/restore.)

- [ ] **Step 2: Pass the rotation from `image_canvas.tsx`**

In `app/src/components/image_canvas.tsx`, the redraw reads the viewer config. At the `drawImageOnCanvas(...)` call (~line 156), pass the rotation. Just before the call, get the config rotation and pass it:

```typescript
        const image = Session.images[item][sensor]
        const imgConfig = getCurrentViewerConfig(
          this.state,
          this.props.id
        ) as ImageViewerConfigType
        drawImageOnCanvas(
          this.imageCanvas,
          this.imageContext,
          image,
          item,
          sensor,
          imgConfig.rotation ?? 0
        )
```

Confirm `getCurrentViewerConfig` and `ImageViewerConfigType` are imported in `image_canvas.tsx` (they are — used in `updateScale`).

- [ ] **Step 3: Typecheck + lint**

Run: `npx tsc --noEmit` (exit 0)
Run: `npm run lint`

- [ ] **Step 4: Runtime check**

Start the app, set `rotation` temporarily (e.g. hardcode `90` in `image_canvas` or wait for Task 5's button). Confirm the image paints rotated 90° and fills the viewer without distortion. Revert any temporary hardcode.

- [ ] **Step 5: Commit**

```bash
git add app/src/view_config/image.ts app/src/components/image_canvas.tsx
git commit -m "feat: render the image rotated by the view rotation"
```

---

### Task 4: Rotate labels + fix pointer/hit-testing

**Files:** Modify `app/src/components/label2d_canvas.tsx` — the redraw (where it draws labels, ~line 290-310), `getMousePos` (~line 528), and `fetchHandleId` (~line 549).

> No jest test (canvas). Verified by `tsc` + runtime. Behavior: drawn lines rotate with the image; clicks are stored in the original frame; selecting/dragging works in the rotated view.

- [ ] **Step 1: Rotate the label + control contexts before drawing**

Read the `redraw()` method. It draws via `this._labelList.redraw(labelContext, controlContext, ratio, ...)` (~line 300-310). Wrap that draw so both contexts are rotated by the view rotation. Capture the rotation and the canvas refs, then `save → translate/rotate → redraw → restore` on BOTH contexts. Add, just before the `this._labelList.redraw(...)` call, a helper to apply the transform, and restore after.

First add a private method to the class:

```typescript
  /**
   * Apply the view rotation to a drawing context so labels paint turned. The
   * canvas is sized to the rotated dimensions, so for 90°/270° we translate by
   * the swapped axis before rotating. Caller must context.restore() afterwards.
   *
   * @param ctx the 2d context to transform
   * @param canvas the canvas owning the context (for its backing dimensions)
   * @param rotation 0 | 90 | 180 | 270
   */
  private applyRotation(
    ctx: CanvasRenderingContext2D,
    canvas: HTMLCanvasElement,
    rotation: number
  ): void {
    if (rotation === 90) {
      ctx.translate(canvas.width, 0)
      ctx.rotate(Math.PI / 2)
    } else if (rotation === 180) {
      ctx.translate(canvas.width, canvas.height)
      ctx.rotate(Math.PI)
    } else if (rotation === 270) {
      ctx.translate(0, canvas.height)
      ctx.rotate(-Math.PI / 2)
    }
  }
```

Then in `redraw()`, around the `this._labelList.redraw(...)` call, get the rotation from the viewer config and wrap:

```typescript
    const imgConfig = getCurrentViewerConfig(
      this.state,
      this.props.id
    ) as ImageViewerConfigType
    const rotation = imgConfig.rotation ?? 0
    this.labelContext.save()
    this.controlContext.save()
    this.applyRotation(this.labelContext, this.labelCanvas, rotation)
    this.applyRotation(this.controlContext, this.controlCanvas, rotation)
    // ... existing this._labelList.redraw(this.labelContext, this.controlContext, ratio, ...) ...
    this.labelContext.restore()
    this.controlContext.restore()
```

(Use the actual context/canvas field names present in the file — confirm them when reading `redraw()`; they are the label and control canvas/context members. `getCurrentViewerConfig`/`ImageViewerConfigType` are already imported.)

- [ ] **Step 2: Un-rotate the pointer in `getMousePos`**

`getMousePos` returns image coordinates via `normalizeMouseCoordinates(...)` — but with a rotated canvas those are in the *rotated/display* frame. Map them back to the original frame so everything stored stays original. Replace the body:

```typescript
  private getMousePos(e: React.MouseEvent<HTMLCanvasElement>): Vector2D {
    if (this.display !== null && this.labelCanvas !== null) {
      const displayCoord = normalizeMouseCoordinates(
        this.labelCanvas,
        this.canvasWidth,
        this.canvasHeight,
        this.displayToImageRatio,
        e.clientX,
        e.clientY
      )
      const imgConfig = getCurrentViewerConfig(
        this.state,
        this.props.id
      ) as ImageViewerConfigType
      const rotation = imgConfig.rotation ?? 0
      if (rotation === 0) {
        return displayCoord
      }
      const [w, h] = getCurrentImageSize(this.state, this.props.id)
      return unrotatePoint(displayCoord, rotation, w, h)
    }
    return new Vector2D(0, 0)
  }
```

Add `unrotatePoint` to the import from `../view_config/image` (alongside `toCanvasCoords`, `normalizeMouseCoordinates`, `getCurrentImageSize`). `getCurrentImageSize` is already imported.

- [ ] **Step 3: Re-rotate in `fetchHandleId` (hit-testing)**

`fetchHandleId` reads the control canvas pixel under the mouse via `toCanvasCoords(mousePos, ...)`. `getImageData` ignores the context transform and reads raw backing pixels, so the read position must be the *rotated* backing coordinate. Rotate the (now original-frame) `mousePos` into the display frame before `toCanvasCoords`:

```typescript
  private fetchHandleId(mousePos: Vector2D): number[] {
    if (this.controlContext !== null) {
      const imgConfig = getCurrentViewerConfig(
        this.state,
        this.props.id
      ) as ImageViewerConfigType
      const rotation = imgConfig.rotation ?? 0
      let probe = mousePos
      if (rotation !== 0) {
        const [w, h] = getCurrentImageSize(this.state, this.props.id)
        probe = rotatePoint(mousePos, rotation, w, h)
      }
      const [x, y] = toCanvasCoords(
        probe,
        true,
        this.displayToImageRatio,
        this._upResRatio
      )
      const data = this.controlContext.getImageData(x, y, 4, 4).data
      return imageDataToHandleId(data)
    } else {
      return [-1, 0]
    }
  }
```

Add `rotatePoint` to the `../view_config/image` import.

- [ ] **Step 4: Typecheck + lint**

Run: `npx tsc --noEmit` (exit 0)
Run: `npm run lint`

- [ ] **Step 5: Runtime check**

With a temporary `rotation: 90` (or after Task 5): draw a polyline — it should follow the cursor on the rotated image and land where clicked. Select an existing line and drag a vertex — the handle should pick up under the cursor (hit-testing aligned). Finish a line, then look at it at rotation 0 — geometry intact.

- [ ] **Step 6: Commit**

```bash
git add app/src/components/label2d_canvas.tsx
git commit -m "feat: rotate label canvas + map pointer/hit-test through the rotation"
```

---

### Task 5: Two rotate buttons in the 2D toolbar

**Files:** Modify `app/src/components/viewer2d.tsx` (`getMenuComponents` / `getHistoryButtons` area; imports near top).

> No jest test (matches the existing zoom/undo buttons). Verified by `tsc` + lint + runtime.

- [ ] **Step 1: Import the icons**

In `app/src/components/viewer2d.tsx`, add alongside the other `@material-ui/icons/*` imports (keep alphabetical so lint passes):

```typescript
import RotateLeftIcon from "@material-ui/icons/RotateLeft"
import RotateRightIcon from "@material-ui/icons/RotateRight"
```

- [ ] **Step 2: Add a rotate helper on the class**

Add a method (near `changeLineWidth`):

```typescript
  /**
   * Rotate the image view by ±90° (display only; never affects the JSON).
   *
   * @param delta +90 (clockwise / right) or -90 (counter-clockwise / left)
   */
  private rotateView(delta: number): void {
    const config = this._viewerConfig as ImageViewerConfigType
    const current = config.rotation ?? 0
    const rotation = (((current + delta) % 360) + 360) % 360
    Session.dispatch(
      changeViewerConfig(this._viewerId, { ...config, rotation })
    )
  }
```

- [ ] **Step 3: Add the two buttons**

In the method that builds the button array (the one returning the zoom/width buttons — `getMenuComponents`, or add to `getHistoryButtons`'s return as a sibling group), define two buttons mirroring the existing Tooltip+IconButton pattern, and include them in the returned array:

```typescript
      const rotateLeftButton = (
        <Tooltip
          key={`rotateLeft2dButton${this.props.id}`}
          title="Rotate left 90°"
          enterDelay={500}
          TransitionComponent={Fade}
          TransitionProps={{ timeout: 600 }}
          arrow
        >
          <IconButton
            onClick={() => this.rotateView(-90)}
            className={this.props.classes.viewer_button}
            edge={"start"}
          >
            <RotateLeftIcon />
          </IconButton>
        </Tooltip>
      )
      const rotateRightButton = (
        <Tooltip
          key={`rotateRight2dButton${this.props.id}`}
          title="Rotate right 90°"
          enterDelay={500}
          TransitionComponent={Fade}
          TransitionProps={{ timeout: 600 }}
          arrow
        >
          <IconButton
            onClick={() => this.rotateView(90)}
            className={this.props.classes.viewer_button}
            edge={"start"}
          >
            <RotateRightIcon />
          </IconButton>
        </Tooltip>
      )
```

Add `rotateLeftButton, rotateRightButton` to the array `getMenuComponents` returns (the `return [ ... ]` listing zoom/width/history buttons). If that pushes `getMenuComponents` over the `max-lines-per-function` lint limit, extract these two into a `getRotationButtons(): JSX.Element[]` helper (mirroring `getHistoryButtons`) and spread it: `...this.getRotationButtons()`.

- [ ] **Step 4: Typecheck + lint**

Run: `npx tsc --noEmit` (exit 0)
Run: `npm run lint`

- [ ] **Step 5: Runtime check (full feature)**

In a 2D polyline task:
1. Click rotate-right → image turns 90° CW; rotate-right ×4 returns upright. Rotate-left turns the other way.
2. Draw a polyline on the rotated view → it follows the cursor and lands correctly.
3. Zoom (wheel) and pan while rotated → both still work.
4. Rotate back to 0° → previously drawn lines sit correctly on the upright image.
5. **Export (`/getExport`) → vertex coordinates are in the original, unrotated frame** (identical to drawing without rotating).
6. Reload the page → view is upright (rotation reset).

- [ ] **Step 6: Commit**

```bash
git add app/src/components/viewer2d.tsx
git commit -m "feat: add rotate-left/right buttons to the 2D viewer"
```

---

## Final Verification

- `npx jest app/test/view_config/image_rotation.test.ts` (if runnable) → green.
- `npx tsc --noEmit` and `npm run lint` clean across all changed files.
- Full runtime pass (Task 5 Step 5), especially the **export check**: the JSON must be byte-identical whether or not you rotated while drawing.

## Self-Review Notes

- **Spec coverage:** rotation state (T1) · rotate/unrotate math (T1) · canvas dim swap (T2) · image draw rotation (T3) · label draw + pointer un-rotate + hit-test re-rotate (T4) · two buttons + reset-on-reload via view state (T5) · JSON untouched (drawables/export never see rotation). All present.
- **Type consistency:** `rotation?: number` on `ImageViewerConfigType`; `rotatePoint(p, rotation, w, h)` / `unrotatePoint(p, rotation, w, h)`; `drawImageOnCanvas(..., rotation)`; `rotateView(delta)`. Names consistent across tasks.
- **No placeholders:** every step has concrete code/commands. The only "confirm field names when reading" notes are for existing members the implementer will see in context (label/control canvas refs).
