# Rectangle Selection Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a two-click Rectangle selection mode beside the existing Freeform lasso, switched via a split-button dropdown, marking every polyline/polygon inside or crossing the rectangle for the existing batch-delete flow.

**Architecture:** A `mode` flag (`"freeform" | "rectangle"`) plus a mouse-up-driven two-click state machine live in the existing `freeform_select_state.ts`. In rectangle mode the canvas records a first corner, rubber-bands a dashed magenta rectangle to the cursor, and on the second click feeds the 4 corners into the existing `runFreeformSelect` (which already accepts any polygon). The toolbar button becomes a split button with a mode-switch menu.

**Tech Stack:** TypeScript, React 16 + Redux, HTML canvas, `@material-ui/core` v4 + `@material-ui/icons` v4, Jest.

## Global Constraints

- **Coordinates are always the original image frame.** Corners are captured via `getMousePos` (image coords); the overlay multiplies by `displayToImageRatio * _upResRatio` only when painting.
- **Reuse, don't rebuild:** a rectangle is a 4-point polygon passed to the existing `findLassoHits` / `runFreeformSelect` / `markLabels`. Do not add new selection, highlight, delete, or geometry math.
- **Selection semantics:** crossing (enclosed **or** touching), union (only ever adds marks).
- **Rectangle interaction:** click-move-click (mouse-up driven). A press-drag-release sets the first corner on release.
- **`Shift`+drag stays a freeform lasso** regardless of the selected mode.
- **Mode is a sticky preference** — it survives disarm and Escape; only the in-progress rectangle is cleared.
- **Targets:** `POLYLINE_2D` / `POLYGON_2D` only; respect the cut tool's visibility filters (`hideLabels` / `hiddenLabelTypes` / `hiddenCategories`).
- **No new `console.log`.**
- Commit messages end with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

## Testing Strategy (read before starting)

Per `docs/polyline-feature-map.md` §8, this checkout runs **pure-logic** suites but **not** drawable/component suites (missing native `canvas` + `redis`).

- **Tasks 1–2** are pure modules → real TDD with a runnable local test using the node-env recipe (run from the repo root `d:/Nikhil/Projects/GitHub/scalabel`):
  `npx jest <path> --env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js`
- **Tasks 3–5** (icon, canvas, toolbar) have no local unit harness → gate on `npx tsc --noEmit`, `npx eslint -c .eslintrc.json <files>` (filter the pre-existing CRLF `prettier/prettier` noise — the real gate is **zero non-`prettier/prettier`** violations), and a runtime check via the **`verify`** skill.
- **Runtime harness note:** on this machine CDP `Input.dispatchMouseEvent` does NOT register as a canvas gesture; drive gestures with **DOM-dispatched `MouseEvent`s** on the top canvas (see `verify` skill and the freeform `cdp_final.js` driver in the session scratchpad). Chrome's `/json` endpoint needs a `localhost` Host header (use `http://localhost:9333`, not `127.0.0.1`).

---

## File Structure

| File | Responsibility |
|---|---|
| `app/src/common/freeform_select_state.ts` | **Modify.** Add `mode` + rectangle two-click state machine + `getSelectionOverlay`. |
| `app/src/drawable/2d/freeform_select_geometry.ts` | **Unchanged** (already handles arbitrary polygons; only its test grows). |
| `app/src/components/cut_icon.tsx` | **Modify.** Add `RectangleSelectIcon`. |
| `app/src/components/label2d_canvas.tsx` | **Modify.** Rectangle gesture wiring; overlay reads `getSelectionOverlay`. |
| `app/src/components/viewer2d.tsx` | **Modify.** Split button + mode-switch `Menu`. |
| `app/test/common/freeform_select_state.test.ts` | **Modify.** Rectangle state-machine tests. |
| `app/test/drawable/freeform_select_geometry.test.ts` | **Modify.** Rectangle-polygon hit test. |

---

## Task 1: Mode + rectangle two-click state machine

**Files:**
- Modify: `app/src/common/freeform_select_state.ts`
- Test: `app/test/common/freeform_select_state.test.ts`

**Interfaces:**
- Consumes: existing `Pt`, `armed`, `notify`, `resetFreeform`.
- Produces:
  - `type SelectMode = "freeform" | "rectangle"`
  - `getSelectMode(): SelectMode`
  - `setSelectMode(m: SelectMode): void`
  - `isRectSizing(): boolean`
  - `setRectFirstCorner(pt: Pt): void`
  - `updateRectCursor(pt: Pt): void`
  - `completeRect(pt: Pt): Pt[] | null`
  - `getRectPreview(): Pt[] | null`
  - `getSelectionOverlay(): Pt[]`

- [ ] **Step 1: Write the failing test**

Append to `app/test/common/freeform_select_state.test.ts` (after the existing `describe` block; keep existing imports and add the new names to the existing `freeform_select_state` import):

```ts
import {
  completeRect,
  getRectPreview,
  getSelectMode,
  getSelectionOverlay,
  isRectSizing,
  setRectFirstCorner,
  setSelectMode,
  updateRectCursor
} from "../../src/common/freeform_select_state"

describe("freeform_select_state rectangle mode", () => {
  beforeEach(() => {
    resetFreeform()
    setSelectMode("freeform")
  })

  test("mode defaults to freeform and can switch", () => {
    expect(getSelectMode()).toBe("freeform")
    setSelectMode("rectangle")
    expect(getSelectMode()).toBe("rectangle")
  })

  test("two-click lifecycle builds four ordered corners", () => {
    setSelectMode("rectangle")
    expect(isRectSizing()).toBe(false)
    setRectFirstCorner({ x: 10, y: 20 })
    expect(isRectSizing()).toBe(true)
    updateRectCursor({ x: 40, y: 60 })
    expect(getRectPreview()).toEqual([
      { x: 10, y: 20 },
      { x: 40, y: 20 },
      { x: 40, y: 60 },
      { x: 10, y: 60 }
    ])
    const corners = completeRect({ x: 40, y: 60 })
    expect(corners).toEqual([
      { x: 10, y: 20 },
      { x: 40, y: 20 },
      { x: 40, y: 60 },
      { x: 10, y: 60 }
    ])
    expect(isRectSizing()).toBe(false)
    expect(getRectPreview()).toBeNull()
  })

  test("too-small rectangle completes to null and clears sizing", () => {
    setSelectMode("rectangle")
    setRectFirstCorner({ x: 10, y: 10 })
    expect(completeRect({ x: 11, y: 11 })).toBeNull()
    expect(isRectSizing()).toBe(false)
  })

  test("getSelectionOverlay returns the rectangle preview in rectangle mode", () => {
    setSelectMode("rectangle")
    setRectFirstCorner({ x: 0, y: 0 })
    updateRectCursor({ x: 30, y: 30 })
    expect(getSelectionOverlay()).toHaveLength(4)
  })

  test("resetFreeform clears rectangle state but keeps the mode", () => {
    setSelectMode("rectangle")
    setRectFirstCorner({ x: 0, y: 0 })
    resetFreeform()
    expect(isRectSizing()).toBe(false)
    expect(getSelectMode()).toBe("rectangle")
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx jest app/test/common/freeform_select_state.test.ts --env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js`
Expected: FAIL — `completeRect` / `getSelectMode` / etc. are not exported.

- [ ] **Step 3: Write the implementation**

In `app/src/common/freeform_select_state.ts`:

(a) After the `let path: Pt[] = []` line (currently line 24), add the new module state:

```ts

/** The selection sub-mode; a sticky user preference. */
export type SelectMode = "freeform" | "rectangle"

/** Minimum |dx| and |dy| (image px) for a rectangle to count as sized. */
const MIN_RECT_PX = 3

let mode: SelectMode = "freeform"
let rectFirst: Pt | null = null
let rectCursor: Pt | null = null
```

(b) In `resetFreeform`, also clear the rectangle state. Replace the current body:

```ts
export function resetFreeform(): void {
  if (!armed && !drawing && path.length === 0) {
    return
  }
  armed = false
  drawing = false
  path = []
  notify()
}
```

with:

```ts
export function resetFreeform(): void {
  if (
    !armed &&
    !drawing &&
    path.length === 0 &&
    rectFirst === null &&
    rectCursor === null
  ) {
    return
  }
  armed = false
  drawing = false
  path = []
  rectFirst = null
  rectCursor = null
  notify()
}
```

(c) Before the `// Mutual exclusion` comment near the end of the file (currently line 133), add the mode + rectangle API:

```ts
/** The current selection sub-mode (freeform lasso or rectangle). */
export function getSelectMode(): SelectMode {
  return mode
}

/**
 * Set the selection sub-mode. Clears any in-progress rectangle but leaves the
 * armed state untouched (switching modes should not disarm the tool).
 *
 * @param m the new mode
 */
export function setSelectMode(m: SelectMode): void {
  if (mode === m) {
    return
  }
  mode = m
  rectFirst = null
  rectCursor = null
  notify()
}

/** Whether a rectangle's first corner is placed and awaiting the second. */
export function isRectSizing(): boolean {
  return rectFirst !== null
}

/**
 * Record the rectangle's first corner (first click). Enters the sizing phase.
 *
 * @param pt the first corner (image frame)
 */
export function setRectFirstCorner(pt: Pt): void {
  rectFirst = { x: pt.x, y: pt.y }
  rectCursor = { x: pt.x, y: pt.y }
  notify()
}

/**
 * Update the live opposite corner while sizing (mouse move). No-op if the
 * first corner has not been placed yet.
 *
 * @param pt the current cursor position (image frame)
 */
export function updateRectCursor(pt: Pt): void {
  if (rectFirst === null) {
    return
  }
  rectCursor = { x: pt.x, y: pt.y }
  notify()
}

/**
 * The four rectangle corners for the current first corner + cursor, in a
 * consistent traversal order, or null when not sizing.
 */
export function getRectPreview(): Pt[] | null {
  if (rectFirst === null || rectCursor === null) {
    return null
  }
  return [
    { x: rectFirst.x, y: rectFirst.y },
    { x: rectCursor.x, y: rectFirst.y },
    { x: rectCursor.x, y: rectCursor.y },
    { x: rectFirst.x, y: rectCursor.y }
  ]
}

/**
 * Finish the rectangle (second click): returns its four corners, or null if it
 * is too small (a click without sizing). Clears the rectangle state either way.
 *
 * @param pt the opposite corner (image frame)
 */
export function completeRect(pt: Pt): Pt[] | null {
  if (rectFirst === null) {
    return null
  }
  const first = rectFirst
  const dx = Math.abs(pt.x - first.x)
  const dy = Math.abs(pt.y - first.y)
  rectFirst = null
  rectCursor = null
  notify()
  if (dx < MIN_RECT_PX || dy < MIN_RECT_PX) {
    return null
  }
  return [
    { x: first.x, y: first.y },
    { x: pt.x, y: first.y },
    { x: pt.x, y: pt.y },
    { x: first.x, y: pt.y }
  ]
}

/**
 * The vertices the overlay should paint: the in-progress lasso path in
 * freeform mode, or the rectangle preview in rectangle mode (empty when
 * nothing is in progress).
 */
export function getSelectionOverlay(): Pt[] {
  if (mode === "rectangle") {
    return getRectPreview() ?? []
  }
  return path
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx jest app/test/common/freeform_select_state.test.ts --env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js`
Expected: PASS (existing + new tests green).

- [ ] **Step 5: Commit**

```bash
git add app/src/common/freeform_select_state.ts app/test/common/freeform_select_state.test.ts
git commit -m "feat: select-mode flag and rectangle two-click state machine"
```

---

## Task 2: Rectangle-polygon hit test

**Files:**
- Modify: `app/test/drawable/freeform_select_geometry.test.ts`

**Interfaces:**
- Consumes: existing `findLassoHits`, `LassoLine`, `Pt`. No production code changes — this proves a rectangle polygon flows through the existing crossing test.

- [ ] **Step 1: Write the failing test**

Append inside the top-level `describe("freeform_select_geometry", ...)` block in `app/test/drawable/freeform_select_geometry.test.ts` (before its closing `})`):

```ts
  describe("rectangle polygon (reused by rectangle-select)", () => {
    const RECT: Pt[] = [
      { x: 0, y: 0 },
      { x: 10, y: 0 },
      { x: 10, y: 10 },
      { x: 0, y: 10 }
    ]
    test("marks a line inside and a line crossing, not one outside", () => {
      const lines: LassoLine[] = [
        {
          id: "inside",
          pts: [
            { x: 2, y: 2 },
            { x: 8, y: 8 }
          ],
          closed: false
        },
        {
          id: "crossing",
          pts: [
            { x: -5, y: 5 },
            { x: 15, y: 5 }
          ],
          closed: false
        },
        {
          id: "outside",
          pts: [
            { x: 20, y: 20 },
            { x: 30, y: 30 }
          ],
          closed: false
        }
      ]
      expect(findLassoHits(lines, RECT)).toEqual(["inside", "crossing"])
    })
  })
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `npx jest app/test/drawable/freeform_select_geometry.test.ts --env=node --globalSetup=./app/test/setup/noop.js --globalTeardown=./app/test/setup/noop.js`
Expected: PASS. (No implementation change — `findLassoHits` already accepts arbitrary polygons; this locks in the reuse.)

- [ ] **Step 3: Commit**

```bash
git add app/test/drawable/freeform_select_geometry.test.ts
git commit -m "test: rectangle polygon flows through findLassoHits crossing test"
```

---

## Task 3: Rectangle toolbar icon

**Files:**
- Modify: `app/src/components/cut_icon.tsx`

**Interfaces:**
- Produces: `RectangleSelectIcon(props: SvgIconProps): JSX.Element`.

- [ ] **Step 1: Add the icon**

In `app/src/components/cut_icon.tsx`, append after `FreeformSelectIcon`:

```tsx
/**
 * Rectangle-marquee icon for the rectangle select mode: a dashed axis-aligned
 * rectangle. Mirrors FreeformSelectIcon (strokes, fill="none").
 *
 * @param props standard SvgIcon props (fontSize, style, ...)
 */
export function RectangleSelectIcon(props: SvgIconProps): JSX.Element {
  return (
    <SvgIcon {...props}>
      <rect
        x={4}
        y={6}
        width={16}
        height={12}
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeDasharray="3 2"
      />
    </SvgIcon>
  )
}
```

- [ ] **Step 2: Type-check and lint**

Run: `npx tsc --noEmit`
Expected: no new errors referencing `cut_icon.tsx`.

Run: `npx eslint -c .eslintrc.json app/src/components/cut_icon.tsx`
Expected: zero non-`prettier/prettier` violations.

- [ ] **Step 3: Commit**

```bash
git add app/src/components/cut_icon.tsx
git commit -m "feat: rectangle-select toolbar icon"
```

---

## Task 4: Canvas rectangle gesture wiring

**Files:**
- Modify: `app/src/components/label2d_canvas.tsx`

**Interfaces:**
- Consumes: Task 1 (`getSelectMode`, `isRectSizing`, `setRectFirstCorner`, `updateRectCursor`, `completeRect`, `getSelectionOverlay`), existing `runFreeformSelect`, `isFreeformActive`.

- [ ] **Step 1: Extend the freeform-state import**

In `app/src/components/label2d_canvas.tsx`, replace the existing import block:

```ts
import {
  addFreeformPoint,
  beginFreeformPath,
  endFreeformPath,
  getFreeformPath,
  isFreeformActive,
  isFreeformDrawing,
  onFreeformChange,
  resetFreeform
} from "../common/freeform_select_state"
```

with:

```ts
import {
  addFreeformPoint,
  beginFreeformPath,
  completeRect,
  endFreeformPath,
  getSelectionOverlay,
  getSelectMode,
  isFreeformActive,
  isFreeformDrawing,
  isRectSizing,
  onFreeformChange,
  resetFreeform,
  setRectFirstCorner,
  updateRectCursor
} from "../common/freeform_select_state"
```

(`getFreeformPath` is dropped — the overlay now uses `getSelectionOverlay`. `beginFreeformPath` and `addFreeformPoint` remain for the lasso.)

- [ ] **Step 2: Branch `onMouseDown` on the mode**

Replace the current freeform block in `onMouseDown` (currently lines 527–536):

```ts
    if (
      (isFreeformActive() || e.shiftKey) &&
      !e.ctrlKey &&
      !e.metaKey &&
      freeformAllowed
    ) {
      beginFreeformPath(mousePos)
      this.setCursor("crosshair")
      return
    }
```

with:

```ts
    if (!e.ctrlKey && !e.metaKey && freeformAllowed) {
      // Shift+drag is always a freeform lasso; the armed toolbar mode follows
      // the selected sub-mode.
      if (e.shiftKey || (isFreeformActive() && getSelectMode() === "freeform")) {
        beginFreeformPath(mousePos)
        this.setCursor("crosshair")
        return
      }
      if (isFreeformActive() && getSelectMode() === "rectangle") {
        // Consume the mousedown so the empty-space pan-arming never runs; the
        // two-click corner logic runs in onMouseUp.
        this.setCursor("crosshair")
        return
      }
    }
```

- [ ] **Step 3: Add the rectangle branch to `onMouseUp`**

In `onMouseUp`, immediately after the `isFreeformDrawing()` lasso block (which currently ends with its `return` at line 704) and **before** `if (isArmed())`, insert:

```ts
    if (isFreeformActive() && getSelectMode() === "rectangle") {
      const rectPos = this.getMousePos(e)
      if (!isRectSizing()) {
        setRectFirstCorner(rectPos)
      } else {
        const corners = completeRect(rectPos)
        if (corners !== null) {
          const config = this.state.user.viewerConfigs[this.props.id]
          runFreeformSelect(corners, {
            hideLabels: config.hideLabels,
            hiddenLabelTypes:
              config.hiddenLabelTypes !== undefined
                ? config.hiddenLabelTypes
                : [],
            hiddenCategories:
              config.hiddenCategories !== undefined
                ? config.hiddenCategories
                : []
          })
        }
      }
      this.setCursor("crosshair")
      this._labelList.onDrawableUpdate()
      return
    }
```

- [ ] **Step 4: Add the rectangle sizing branch to `onMouseMove`**

In `onMouseMove`, immediately after the `isFreeformDrawing()` block (currently lines 742–746) and **before** `if (isArmed())`, insert:

```ts
    if (
      isFreeformActive() &&
      getSelectMode() === "rectangle" &&
      isRectSizing()
    ) {
      updateRectCursor(this.getMousePos(e))
      this.setCursor("crosshair")
      return
    }
```

- [ ] **Step 5: Generalize the overlay to both modes**

In `drawFreeformOverlay`, change the first line of the body (currently line 963):

```ts
    const path = getFreeformPath()
```

to:

```ts
    const path = getSelectionOverlay()
```

(The rest of the method is unchanged; `closePath()` draws the rectangle correctly from its 4 corners.)

- [ ] **Step 6: Type-check and lint**

Run: `npx tsc --noEmit`
Expected: no new errors referencing `label2d_canvas.tsx`.

Run: `npx eslint -c .eslintrc.json app/src/components/label2d_canvas.tsx`
Expected: zero non-`prettier/prettier` violations vs HEAD.

- [ ] **Step 7: Commit**

```bash
git add app/src/components/label2d_canvas.tsx
git commit -m "feat: two-click rectangle gesture wiring and mode-aware overlay"
```

---

## Task 5: Toolbar split button with mode-switch menu

**Files:**
- Modify: `app/src/components/viewer2d.tsx`

**Interfaces:**
- Consumes: Task 1 (`getSelectMode`, `setSelectMode`), Task 3 (`RectangleSelectIcon`), existing `armFreeform`, `isFreeformArmed`, `resetFreeform`.

- [ ] **Step 1: Add imports**

In `app/src/components/viewer2d.tsx`:

(a) Change the `@material-ui/core` import (currently line 1):

```ts
import { IconButton } from "@material-ui/core"
```

to:

```ts
import { IconButton, Menu, MenuItem } from "@material-ui/core"
```

(b) After the `ZoomOutIcon` import (currently line 11), add:

```ts
import ArrowDropDownIcon from "@material-ui/icons/ArrowDropDown"
```

(c) Update the `cut_icon` import to include the rectangle icon:

```ts
import {
  ContentCutIcon,
  DeleteSegmentIcon,
  FreeformSelectIcon,
  RectangleSelectIcon
} from "./cut_icon"
```

(d) Update the `freeform_select_state` import to include the mode API:

```ts
import {
  armFreeform,
  getSelectMode,
  isFreeformArmed,
  onFreeformChange,
  resetFreeform,
  setSelectMode
} from "../common/freeform_select_state"
```

- [ ] **Step 2: Add the menu-anchor field and freeform subscription**

Add the field beside `_offSegmentDeleteChange` (currently line 93):

```ts
  /** anchor element for the select-mode dropdown menu (null = closed) */
  private _selectMenuAnchor: HTMLElement | null = null
  /** unsubscribe from select-tool (freeform/rectangle) state changes */
  private _offFreeformChange: (() => void) | null = null
```

In `componentDidMount`, after the `onSegmentDeleteChange` subscription (currently lines 103–105), add:

```ts
    this._offFreeformChange = onFreeformChange(() => this.forceUpdate())
```

In `componentWillUnmount`, after the `_offSegmentDeleteChange` teardown block (currently lines 117–120), add:

```ts
    if (this._offFreeformChange !== null) {
      this._offFreeformChange()
      this._offFreeformChange = null
    }
```

- [ ] **Step 3: Replace the button builder with a split button**

Replace the entire `getFreeformSelectButton` method (currently lines 464–496) with:

```tsx
  /**
   * Build the split select button: the icon arms/disarms the currently-selected
   * mode (freeform lasso or rectangle); the caret opens a menu to switch mode.
   * The icon reflects the active mode; green when armed. Mutually exclusive with
   * the cut and delete-segment tools.
   *
   * @return {JSX.Element} the select split button
   */
  protected getFreeformSelectButton(): JSX.Element {
    const armed = isFreeformArmed()
    const mode = getSelectMode()
    const canArm = (): boolean =>
      !Session.label2dList.isDrawingInProgress() &&
      !this.state.task.config.tracking &&
      (this._viewerConfig as ImageViewerConfigType)?.showCurvesOnly !== true
    const ModeIcon =
      mode === "rectangle" ? RectangleSelectIcon : FreeformSelectIcon
    return (
      <React.Fragment key={`selectSplit2dButton${this.props.id}`}>
        <Tooltip
          title={mode === "rectangle" ? "Rectangle select" : "Freeform select"}
          enterDelay={500}
          TransitionComponent={Fade}
          TransitionProps={{ timeout: 600 }}
          arrow
        >
          <IconButton
            onClick={() => {
              if (armed) {
                resetFreeform()
              } else if (canArm()) {
                armFreeform()
              }
            }}
            className={this.props.classes.viewer_button}
            style={{ color: armed ? "#4caf50" : undefined }}
            edge={"start"}
          >
            <ModeIcon />
          </IconButton>
        </Tooltip>
        <IconButton
          size="small"
          onClick={(e) => {
            this._selectMenuAnchor = e.currentTarget
            this.forceUpdate()
          }}
          className={this.props.classes.viewer_button}
          style={{ color: armed ? "#4caf50" : undefined, padding: 0 }}
        >
          <ArrowDropDownIcon />
        </IconButton>
        <Menu
          anchorEl={this._selectMenuAnchor}
          open={this._selectMenuAnchor !== null}
          onClose={() => {
            this._selectMenuAnchor = null
            this.forceUpdate()
          }}
        >
          <MenuItem
            selected={mode === "freeform"}
            onClick={() => {
              setSelectMode("freeform")
              if (!isFreeformArmed() && canArm()) {
                armFreeform()
              }
              this._selectMenuAnchor = null
              this.forceUpdate()
            }}
          >
            Freeform
          </MenuItem>
          <MenuItem
            selected={mode === "rectangle"}
            onClick={() => {
              setSelectMode("rectangle")
              if (!isFreeformArmed() && canArm()) {
                armFreeform()
              }
              this._selectMenuAnchor = null
              this.forceUpdate()
            }}
          >
            Rectangle
          </MenuItem>
        </Menu>
      </React.Fragment>
    )
  }
```

- [ ] **Step 4: Type-check and lint**

Run: `npx tsc --noEmit`
Expected: no new errors referencing `viewer2d.tsx`.

Run: `npx eslint -c .eslintrc.json app/src/components/viewer2d.tsx`
Expected: zero non-`prettier/prettier` violations vs HEAD.

- [ ] **Step 5: Runtime verification**

Rebuild first (`npm run build`; the server serves the bundle from disk), then use the `verify` skill to drive the label page in headless Chrome. Confirm:
1. The select button shows a caret; clicking the caret opens a menu with **Freeform** and **Rectangle** (Freeform checked initially).
2. Pick **Rectangle**, click the button → it turns green (armed); the icon is the rectangle glyph.
3. Click one corner, move the cursor → a dashed magenta rectangle rubber-bands; click the opposite corner → lines inside or crossing turn magenta.
4. `Delete` removes the marked lines; `Ctrl+Z` restores them.
5. `Escape` cancels an in-progress rectangle and disarms.
6. Switch back to **Freeform**; `Shift`+drag still lassos.
7. Clicking cut / delete-segment disarms the select button (green clears), and vice-versa.

- [ ] **Step 6: Commit**

```bash
git add app/src/components/viewer2d.tsx
git commit -m "feat: split select button with freeform/rectangle mode menu"
```

---

## Self-Review

**1. Spec coverage** (against `docs/superpowers/specs/2026-07-10-rectangle-select-mode-design.md`):

| Spec item | Task |
|---|---|
| Mode flag + rectangle two-click state machine | Task 1 |
| Rectangle reuses `findLassoHits` (crossing + union) | Task 2 (proof) + Task 4 (call site) |
| Rectangle icon | Task 3 |
| Canvas gesture wiring (down/move/up) + overlay generalization | Task 4 |
| Split button + mode-switch menu + icon-reflects-mode | Task 5 |
| Shift+drag stays freeform | Task 4 Step 2 |
| Escape/nav clears rectangle; mode persists | Task 1 (resetFreeform) + existing Escape/nav |
| Magenta highlight / Delete / undo reuse | Reused unchanged (verified Task 5 Step 5) |

**2. Placeholder scan:** No "TBD"/"TODO"; every code step shows complete code.

**3. Type consistency:** `SelectMode`, `getSelectMode`/`setSelectMode`, `isRectSizing`, `setRectFirstCorner`, `updateRectCursor`, `completeRect`, `getRectPreview`, `getSelectionOverlay` (Task 1) are imported and called with matching names/signatures in Tasks 4 and 5. `RectangleSelectIcon` (Task 3) matches its import and use in Task 5. `runFreeformSelect(corners, visibility)` (Task 4) uses the existing signature with the exact `{ hideLabels, hiddenLabelTypes, hiddenCategories }` shape. Consistent.
