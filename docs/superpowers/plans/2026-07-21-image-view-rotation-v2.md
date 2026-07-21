# Display-Only 90° Image Rotation (v2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the annotator rotate the image view in 90° steps (toolbar buttons + R/Shift+R) and annotate rotated, while every stored/exported coordinate stays in the original image frame.

**Architecture:** Rotation is a display-only `rotation` field on `ImageViewerConfigType`. The canvas DOM stays axis-aligned; `updateCanvasScale` swaps dimensions for 90°/270°, the image and label/control canvases paint through a `translate + rotate` context transform, and pointer input is mapped back through `unrotatePoint` in the single `getMousePos` funnel. All tools (cut, delete-segment, batch select, snap-merge) work rotated for free because they consume that funnel and their overlays are drawn inside the same transform.

**Tech Stack:** TypeScript + React (class components), HTML canvas 2D, Redux (viewer config), Jest (node-env recipe for pure tests), Material-UI v4 icons.

**Spec:** `docs/superpowers/specs/2026-07-21-image-view-rotation-v2-design.md`. Reference implementation fragments exist in git commit `c6b604cf` (the June `rotate-canvas` stash index — read with `git diff c6b604cf^ c6b604cf`); do NOT pop the stash.

## Global Constraints

- Coordinates are ALWAYS stored/exported in the original image frame — no change to drawables, `commit2DLabels`, `draw_history.ts`, tool logic, export/import, or the backend.
- `rotation` is one of `0 | 90 | 180 | 270` (clockwise), optional, default 0, never serialized into annotation JSON.
- Windows checkout: `npm run lint` has pervasive pre-existing CRLF `prettier/prettier` noise — compare a changed file's *non-prettier* violations to HEAD before assuming you introduced them.
- Drawable/component Jest suites need native canvas + redis and fail to load here. Only the pure-logic test (Task 1) runs, via: `npx jest <file> --env=node --globalSetup=app/test/setup/noop.js --globalTeardown=app/test/setup/noop.js`.
- Commit after every task. Commit messages end with `Co-Authored-By:` per repo convention.

---

### Task 1: Rotation math helpers + viewer-config field

**Files:**
- Modify: `app/src/view_config/image.ts` (add two pure functions after `toImageCoords`, ~line 100)
- Modify: `app/src/types/state.ts` (add field to `ImageViewerConfigType`, ~line 222)
- Test: `app/test/view_config/image_rotation.test.ts` (new)

**Interfaces:**
- Consumes: `Vector2D` from `app/src/math/vector2d` (already imported in `image.ts`).
- Produces: `rotatePoint(p: Vector2D, rotation: number, w: number, h: number): Vector2D` (original→display frame) and `unrotatePoint(p: Vector2D, rotation: number, w: number, h: number): Vector2D` (display→original), both exported from `app/src/view_config/image.ts`; `rotation?: number` on `ImageViewerConfigType`. Tasks 2–4 rely on these exact names.

- [ ] **Step 1: Write the failing test**

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
    expect(rotatePoint(new Vector2D(0, 0), 90, W, H)).toEqual(
      new Vector2D(H, 0)
    )
    expect(rotatePoint(new Vector2D(0, H), 90, W, H)).toEqual(
      new Vector2D(0, 0)
    )
    expect(rotatePoint(new Vector2D(W, H), 90, W, H)).toEqual(
      new Vector2D(0, W)
    )
  })

  test("rotation 0 is identity", () => {
    expect(rotatePoint(new Vector2D(12, 34), 0, W, H)).toEqual(
      new Vector2D(12, 34)
    )
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (from repo root):
```
npx jest app/test/view_config/image_rotation.test.ts --env=node --globalSetup=app/test/setup/noop.js --globalTeardown=app/test/setup/noop.js
```
Expected: FAIL — `image.ts` has no exported member `rotatePoint` (TS2305 from ts-jest/babel).

- [ ] **Step 3: Implement the helpers and the state field**

In `app/src/view_config/image.ts`, directly after the `toImageCoords` function (ends ~line 100), insert:

```typescript
/**
 * Rotate a point from the original image frame into the displayed (rotated)
 * frame. Exact, lossless 90° math. `rotation` is 0 | 90 | 180 | 270
 * (clockwise); `w`/`h` are the ORIGINAL image width/height.
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
 * Inverse of rotatePoint: map a point in the displayed (rotated) frame back
 * to the original image frame. `w`/`h` are the ORIGINAL image width/height.
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

In `app/src/types/state.ts`, inside `interface ImageViewerConfigType` (after the `lineWidthMultiplier?: number` member, ~line 222), add:

```typescript
  /** Display-only view rotation in degrees: 0 | 90 | 180 | 270. Never exported. */
  rotation?: number
```

- [ ] **Step 4: Run test to verify it passes**

Run the same jest command as Step 2. Expected: PASS (3 tests).
Also run: `npx tsc --noEmit` — expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add app/src/view_config/image.ts app/src/types/state.ts app/test/view_config/image_rotation.test.ts
git commit -m "feat: rotation math helpers and viewer-config rotation field

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Rotated canvas sizing and image drawing

**Files:**
- Modify: `app/src/view_config/image.ts` — `drawImageOnCanvas` (~line 148) and `updateCanvasScale` (~line 302)
- Modify: `app/src/components/image_canvas.tsx` — `redraw()` call site (~line 164)

**Interfaces:**
- Consumes: `rotation?: number` on `ImageViewerConfigType` (Task 1).
- Produces: `drawImageOnCanvas(canvas, context, image, itemIndex?, sensorId?, rotation: number = 0)` — new trailing optional param; `updateCanvasScale` reads `config.rotation` internally (signature unchanged). Task 3 relies on the canvas being sized to swapped dimensions for 90/270.

- [ ] **Step 1: Dimension swap in `updateCanvasScale`**

In `app/src/view_config/image.ts` `updateCanvasScale`, replace:

```typescript
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

with:

```typescript
  const item = getCurrentItem(state)
  const image = Session.images[item.index][config.sensor]
  // For a 90°/270° view rotation the displayed image is the original turned
  // on its side, so the canvas is sized to the swapped dimensions. The canvas
  // DOM element stays axis-aligned (content is rotated via the draw context).
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

- [ ] **Step 2: Context rotation in `drawImageOnCanvas`**

Change the signature to add a trailing param and update the doc comment param list:

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

After the `imageSmoothingQuality` line and before the ImageBitmap block, insert:

```typescript
  // Rotate the drawing context so the bitmap paints turned. The canvas is
  // already sized to the rotated dimensions (updateCanvasScale), so for
  // 90°/270° the un-rotated content box uses the swapped width/height.
  const swap = rotation === 90 || rotation === 270
  const drawW = swap ? canvas.height : canvas.width
  const drawH = swap ? canvas.width : canvas.height
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

In the cached-ImageBitmap branch, change the destination size args from `canvas.width, canvas.height` to `drawW, drawH`, and add `context.restore()` immediately before its `return`. Change the fallback draw's destination the same way and add `context.restore()` after it (last line of the function):

```typescript
      context.drawImage(
        cached,
        0,
        0,
        image.width,
        image.height,
        0,
        0,
        drawW,
        drawH
      )
      context.restore()
      return
```

```typescript
  // Fallback to standard HTMLImageElement draw
  context.drawImage(image, 0, 0, image.width, image.height, 0, 0, drawW, drawH)
  context.restore()
}
```

- [ ] **Step 3: Pass the rotation from `image_canvas.tsx`**

In `app/src/components/image_canvas.tsx` `redraw()` (~line 164), replace:

```typescript
        const image = Session.images[item][sensor]
        drawImageOnCanvas(this.imageCanvas, this.imageContext, image, item, sensor)
```

with:

```typescript
        const image = Session.images[item][sensor]
        drawImageOnCanvas(
          this.imageCanvas,
          this.imageContext,
          image,
          item,
          sensor,
          (config as ImageViewerConfigType).rotation ?? 0
        )
```

(`config` is already in scope in `redraw()`; `ImageViewerConfigType` is already imported at the top of the file.)

- [ ] **Step 4: Verify compile + tests still pass**

Run: `npx tsc --noEmit` — expected: no errors.
Run the Task 1 jest command — expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/view_config/image.ts app/src/components/image_canvas.tsx
git commit -m "feat: rotated canvas sizing and image draw for 90-degree view rotation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Label/control canvas rotation + pointer funnel

**Files:**
- Modify: `app/src/components/label2d_canvas.tsx` — imports, `redraw()` (~line 402), `getMousePos` (~line 1176), `fetchHandleId` (~line 1197), plus two new private members.

**Interfaces:**
- Consumes: `rotatePoint`, `unrotatePoint`, `getCurrentImageSize` from `../view_config/image` (Task 1; `getCurrentImageSize(state, viewerId): Size2D` already exists); swapped canvas sizing (Task 2).
- Produces: `private get viewRotation(): number` and `private applyRotation(ctx, canvas, rotation): void` on `Label2dCanvas`; `getMousePos` returns ORIGINAL-frame coordinates at any rotation (every existing consumer — drawing, cut, delete-segment, lasso — is downstream of this).

- [ ] **Step 1: Imports**

In the `../view_config/image` import block of `app/src/components/label2d_canvas.tsx`, ensure these names are present (add the missing ones): `getCurrentImageSize`, `rotatePoint`, `unrotatePoint` (alongside the existing `normalizeMouseCoordinates`, `toCanvasCoords`, `updateCanvasScale`, ...). Ensure `ImageViewerConfigType` is included in the `../types/state` import.

- [ ] **Step 2: Add the rotation accessors**

Add these two members near `setDefaultCursor` (~line 275):

```typescript
  /** Current display-only view rotation (0/90/180/270) for this viewer. */
  private get viewRotation(): number {
    const config = this.state.user.viewerConfigs[
      this.props.id
    ] as ImageViewerConfigType
    return config.rotation ?? 0
  }

  /**
   * Apply the view rotation to a drawing context so content paints turned.
   * The canvas is sized to the rotated dimensions, so for 90°/270° we
   * translate by the swapped axis before rotating. Caller must
   * context.restore() afterwards.
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

- [ ] **Step 3: Wrap ALL painting in the rotation transform**

In `redraw()`:

(a) Add a rotation guard to the viewport-culling condition (bounds are computed in the unrotated frame — skip the optimization while rotated):

```typescript
      let viewportBounds: [number, number, number, number] | undefined
      if (
        viewScale > 2 &&
        this.viewRotation === 0 &&
        this.display !== null &&
        "displayLeft" in config &&
        "displayTop" in config
      ) {
```

(b) Wrap the label-list draw AND both overlay draws (the transform must cover the delete-segment marching-ants/halos and the lasso/rectangle overlay, so their image-frame points rotate with the layer):

```typescript
      const rotation = this.viewRotation
      this.labelContext.save()
      this.controlContext.save()
      this.applyRotation(this.labelContext, this.labelCanvas, rotation)
      this.applyRotation(this.controlContext, this.controlCanvas, rotation)
      this._labelList.redraw(
        this.labelContext,
        this.controlContext,
        this.displayToImageRatio * this._upResRatio,
        config.hideLabels,
        config.hideTags,
        mode,
        viewScale,
        viewportBounds,
        hiddenLabelTypes,
        hiddenCategories,
        !isInteracting(),
        lineWidthMultiplier,
        showCurvesOnly
      )
      this.drawSegmentDeleteOverlay(
        this.labelContext,
        this.displayToImageRatio * this._upResRatio
      )
      this.drawFreeformOverlay(
        this.labelContext,
        this.displayToImageRatio * this._upResRatio
      )
      this.labelContext.restore()
      this.controlContext.restore()
```

- [ ] **Step 4: Un-rotate pointer input in `getMousePos`**

Replace the body:

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
      // With a rotated view, normalizeMouseCoordinates returns the point in
      // the rotated/display frame; map it back to the original image frame so
      // every stored coordinate stays in the original (unrotated) frame.
      const rotation = this.viewRotation
      if (rotation === 0) {
        return displayCoord
      }
      const size = getCurrentImageSize(this.state, this.props.id)
      return unrotatePoint(displayCoord, rotation, size.width, size.height)
    }
    return new Vector2D(0, 0)
  }
```

- [ ] **Step 5: Rotate the hit-test probe in `fetchHandleId`**

`getImageData` reads raw backing pixels and ignores the context transform, so probe at the rotated position:

```typescript
  private fetchHandleId(mousePos: Vector2D): number[] {
    if (this.controlContext !== null) {
      // The control canvas is drawn through the rotation, but getImageData
      // reads raw backing pixels (ignoring the context transform), so probe
      // at the rotated position of the (original-frame) mouse coordinate.
      const rotation = this.viewRotation
      let probe = mousePos
      if (rotation !== 0) {
        const size = getCurrentImageSize(this.state, this.props.id)
        probe = rotatePoint(mousePos, rotation, size.width, size.height)
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

- [ ] **Step 6: Verify compile + lint**

Run: `npx tsc --noEmit` — expected: no errors.
Run: `npm run lint 2>&1 | grep -v "prettier/prettier" | grep label2d_canvas` — expected: no NEW non-prettier violations vs HEAD.

- [ ] **Step 7: Commit**

```bash
git add app/src/components/label2d_canvas.tsx
git commit -m "feat: rotate label and control canvases; unrotate pointer input

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Toolbar buttons, R/Shift+R shortcut, legend entry

**Files:**
- Modify: `app/src/components/viewer2d.tsx` — icon imports, `getMenuComponents` return list (~line 324), two new methods
- Modify: `app/src/components/label2d_canvas.tsx` — `onKeyDown` (~line 1106, after the Space block)
- Modify: `app/src/components/toolbar.tsx` — keyboard-shortcut legend (~line 420)

**Interfaces:**
- Consumes: `rotation` field (Task 1); `changeViewerConfig` action (already imported in both components); `getSegmentDeletePhase` from `../common/segment_delete_state` (already imported in `label2d_canvas.tsx`; ADD to `viewer2d.tsx`'s existing segment_delete_state import); `Session.label2dList.isDrawingInProgress()`.
- Produces: `protected getRotationButtons(): JSX.Element[]` and `private rotateView(delta: number): void` on `Viewer2D`.

- [ ] **Step 1: Viewer2D buttons**

Add icon imports at the top of `viewer2d.tsx` (alphabetical among the existing `@material-ui/icons` imports):

```typescript
import RotateLeftIcon from "@material-ui/icons/RotateLeft"
import RotateRightIcon from "@material-ui/icons/RotateRight"
```

Add `getSegmentDeletePhase` to the existing `../common/segment_delete_state` import.

Add the two methods (near `getCutButton`):

```typescript
  /**
   * Rotate the image view by ±90° (display only; never affects the JSON).
   * Inert while a line is being drawn or a delete-segment preview is pending,
   * so the view cannot change frames mid-gesture.
   *
   * @param delta +90 (clockwise / right) or -90 (counter-clockwise / left)
   */
  private rotateView(delta: number): void {
    if (
      Session.label2dList.isDrawingInProgress() ||
      getSegmentDeletePhase() === "preview"
    ) {
      return
    }
    const config = this._viewerConfig as ImageViewerConfigType
    const current = config.rotation ?? 0
    const rotation = (((current + delta) % 360) + 360) % 360
    const newConfig: ImageViewerConfigType = { ...config, rotation }
    Session.dispatch(changeViewerConfig(this._viewerId, newConfig))
  }

  /**
   * Build the rotate-left / rotate-right toolbar buttons.
   *
   * @return {JSX.Element[]} rotate-left and rotate-right buttons
   */
  protected getRotationButtons(): JSX.Element[] {
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
    return [rotateLeftButton, rotateRightButton]
  }
```

In `getMenuComponents`, insert the buttons after `widthResetButton`:

```typescript
      return [
        zoomInButton,
        zoomOutButton,
        resetZoomButton,
        widthUpButton,
        widthDownButton,
        widthResetButton,
        ...this.getRotationButtons(),
        ...this.getHistoryButtons(),
        this.getCutButton(),
        this.getDeleteSegmentButton(),
        this.getFreeformSelectButton()
      ]
```

- [ ] **Step 2: R / Shift+R in the canvas key handler**

In `label2d_canvas.tsx` `onKeyDown`, immediately AFTER the Space block (after its closing `}`, ~line 1106) and BEFORE the `drawHistory.handleKeyboard` block, insert:

```typescript
    if ((e.key === "r" || e.key === "R") && !e.ctrlKey && !e.metaKey && !e.altKey) {
      // R rotates the view 90° clockwise; Shift+R counter-clockwise.
      // Display-only (stored coords stay in the original frame). Skipped
      // while typing, while drawing, and during a delete-segment preview.
      const target = e.target as HTMLElement | null
      const typing =
        target !== null &&
        (target.tagName === "INPUT" || target.tagName === "TEXTAREA")
      const blocked =
        Session.label2dList.isDrawingInProgress() ||
        getSegmentDeletePhase() === "preview"
      if (!typing && !blocked) {
        e.preventDefault()
        const config = this.state.user.viewerConfigs[
          this.props.id
        ] as ImageViewerConfigType
        const current = config.rotation ?? 0
        const delta = e.shiftKey ? -90 : 90
        const rotation = (((current + delta) % 360) + 360) % 360
        Session.dispatch(
          changeViewerConfig(this.props.id, { ...config, rotation })
        )
        return
      }
    }
```

- [ ] **Step 3: Legend entry**

In `toolbar.tsx`, in the shortcut legend array (~line 420), after the `Space` row add:

```typescript
              { keys: ["R"], label: "rotate view 90°" },
```

- [ ] **Step 4: Verify compile + lint**

Run: `npx tsc --noEmit` — expected: no errors.
Run: `npm run lint 2>&1 | grep -v "prettier/prettier" | grep -E "viewer2d|label2d_canvas|toolbar"` — expected: no NEW non-prettier violations vs HEAD.

- [ ] **Step 5: Commit**

```bash
git add app/src/components/viewer2d.tsx app/src/components/label2d_canvas.tsx app/src/components/toolbar.tsx
git commit -m "feat: rotate-view toolbar buttons and R/Shift+R shortcut

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Docs + runtime verification

**Files:**
- Modify: `docs/polyline-feature-map.md` (§3 and gotchas)
- No code changes (fix regressions found during verification inside this task if small; otherwise report).

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: accurate docs; verified feature.

- [ ] **Step 1: Update the feature map**

The map's §3 already references `viewRotation`/`applyRotation`, `rotatePoint`/`unrotatePoint`, and the rotation dim-swap (written against the never-committed June stash — accurate again after Tasks 1–4). Verify each named symbol now exists; then (a) add to §3's `viewer2d.tsx` entry: `getRotationButtons`/`rotateView` (±90°, guarded while drawing / during delete-segment preview; R / Shift+R shortcut in `label2d_canvas.tsx onKeyDown`), and (b) append a gotcha to the final section:

```markdown
- **Rotation is display-only and lives in three places.** The canvas is SIZED
  to swapped dims (`updateCanvasScale`), CONTENT is turned by a context
  transform (`applyRotation` wraps labels AND the delete/lasso overlays), and
  POINTER input is un-rotated once in `getMousePos`. `fetchHandleId` must
  probe at `rotatePoint(mousePos)` because `getImageData` ignores context
  transforms. Viewport culling is bypassed while rotated. Never store or
  export rotated coordinates.
```

- [ ] **Step 2: Full static verification**

Run: `npx tsc --noEmit` — expected: no errors.
Run the Task 1 jest command — expected: PASS.
Run: `npm run lint 2>&1 | grep -v "prettier/prettier"` and compare with HEAD~4 — expected: no new violations.

- [ ] **Step 3: Runtime verification (repo `/verify` skill — headless CDP)**

Use the repo's `verify` skill (requires redis + dev server per the skill's notes). Checklist (all four rotations where sensible):

1. Draw a polyline at 0° → click rotate-right through 90/180/270/0 → the line stays glued to the same image features at every step.
2. Rotate to 90° → draw a new line along a recognizable image feature → rotate back to 0° → the line sits on that feature in the upright image.
3. While rotated: drag a vertex; C+click a MID to curve; cut a line (scissors); delete-segment with two picks (watch the marching-ants preview render rotated); Ctrl+click-mark + lasso-mark lines and Delete them.
4. Wheel-zoom at the cursor and drag-pan while rotated — focal point and pan direction behave sensibly (pan directions follow the screen, not the image).
5. R and Shift+R rotate; R while a draw is in progress does nothing; R during a delete-segment preview does nothing; R in a sidebar text input types "r".
6. Export check: `GET /getExport` — vertex coordinates for the line drawn in step 2 are in the original frame (match a line drawn unrotated on the same feature within a few px).

Record pass/fail per item; park the cursor with a mousemove before pixel assertions (see memory: synthetic clicks without prior mousemove silently break canvas gestures).

- [ ] **Step 4: Commit**

```bash
git add docs/polyline-feature-map.md
git commit -m "docs: feature-map entries for display-only view rotation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
