# 2D Viewer: Cursor-Zoom, Line-Width Buttons, Map-Like Pan — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Ctrl/pinch zoom keep the point under the cursor fixed, add toolbar buttons to change polyline render thickness, and add map-like panning (empty-space drag, double-click-drag, plus the existing Ctrl+drag) without breaking drawing/editing.

**Architecture:** Feature 1 fixes the focal point + pan clamp in `viewer2d.tsx`. Feature 2 adds a `lineWidthMultiplier` to the image viewer config and threads it through the existing `viewScale` draw chain to `polygon2d.draw`, with three toolbar buttons. Feature 3 adds a tiny `pointer_pan_state` coordination module; the label canvas decides "empty vs label" via its hit-test and defers the draw on empty-space so a drag pans and a click draws; double-click opens a short pan window.

**Tech Stack:** TypeScript, React 17, Redux, HTML Canvas 2D, Material-UI.

---

## Environment / verification notes (read first)

- **jest cannot run on this box** (its global setup spawns `redis-server`, unavailable → `spawn redis-server ENOENT`). Unit tests below are written as jest specs (valid in CI). Locally, verify with `npx tsc --noEmit -p tsconfig.json`, `npm run build`, and the runtime harness.
- **Runtime harness:** Redis on 6379 + the Scalabel server running (`node app/dist/main.js --config ./local-data/scalabel/config.yml`) + the `test2` project. `puppeteer-core` is in `node_modules`; Chrome at `C:/Program Files/Google/Chrome/Application/chrome.exe`. Pattern used throughout: write a `verify_*.cjs`, run with `node`, then delete it.
- **Do NOT `git commit`/`git push`** — the user commits manually. **Do NOT** run `npm run build` more than needed (slow ~60s); batch where noted.
- Features are independent: Feature 1 (Task 1), Feature 2 (Tasks 2–3), Feature 3 (Tasks 4–7). Ship/verify each before the next.

## File structure

- Modify `app/src/components/viewer2d.tsx` — zoom focal+clamp (T1), width buttons (T3), double-click/ctrl pan gating (T5), empty-drag pan gating (T6).
- Modify `app/src/types/state.ts` — `lineWidthMultiplier` field (T2).
- Modify `app/src/drawable/2d/label2d.ts` — abstract `draw` param (T2).
- Modify `app/src/drawable/2d/polygon2d.ts` — apply multiplier (T2).
- Modify `app/src/drawable/2d/label2d_list.ts` — thread multiplier (T2).
- Modify `app/src/components/label2d_canvas.tsx` — thread multiplier (T2); defer/empty-drag + cursor (T6, T7).
- Create `app/src/common/pointer_pan_state.ts` (+ test) — pan coordination (T4).

---

## Task 1: Zoom at the cursor (Feature 1)

**Files:** Modify `app/src/components/viewer2d.tsx` (`onWheel` ~line 306, `zoom` ~lines 337–364).

- [ ] **Step 1: Use the live wheel-cursor as the focal point**

In `onWheel`, replace line 306:
```ts
        this._pendingZoomOffset = new Vector2D(this._mX, this._mY)
```
with (computes the cursor from the wheel event itself, fixing trackpad-pinch drift):
```ts
        const wheelRect = this._container.getBoundingClientRect()
        this._pendingZoomOffset = new Vector2D(
          e.clientX - wheelRect.left,
          e.clientY - wheelRect.top
        )
```
(`this._container` is already non-null inside this `if` block.)

- [ ] **Step 2: Replace the centering override in `zoom()` with bounds clamping**

In `zoom()`, replace the whole blank-region block (current lines 345–361, from the comment `// The difference between the display area...` through the closing brace of the `else` branch):
```ts
        // The difference between the display area and the displayed image in
        // aspect ratio gives rise to blank regions. Expected behavior
        // is zooming to the center when the blank region exists,
        // or zooming to the cursor otherwise.
        if (rect.height / rect.width > ih / iw) {
          // Zoomed height < that of the display area, blanks on top/bottom
          if ((image.width * rect.height) / rect.width > ih) {
            // Set offset to 0
            displayTop = 0
          }
        } else {
          // Zoomed width < that of the display area, blanks on sides
          if ((image.height * rect.width) / rect.height > iw) {
            // Set offset to 0
            displayLeft = 0
          }
        }
```
with cursor-preserving clamping (keeps the focal-point pan but prevents dragging the image off into blank space; accounts for the centering padding the canvas applies when the image is smaller than the viewport):
```ts
        // Keep the cursor-focal pan, but clamp so the image cannot be pulled
        // past the viewport edges into blank space. dispW/dispH are the
        // displayed image size in CSS px (same basis as updateCanvasScale);
        // padX/padY mirror the centering padding it applies when the image is
        // smaller than the viewport.
        const imageAspect = image.width / image.height
        let dispW: number
        let dispH: number
        if (rect.width / rect.height > imageAspect) {
          dispH = rect.height * newScale
          dispW = dispH * imageAspect
        } else {
          dispW = rect.width * newScale
          dispH = dispW / imageAspect
        }
        const padX = Math.max(0, (rect.width - dispW) / 2)
        const padY = Math.max(0, (rect.height - dispH) / 2)
        const loL = Math.min(-padX, rect.width - dispW - padX)
        const hiL = Math.max(-padX, rect.width - dispW - padX)
        const loT = Math.min(-padY, rect.height - dispH - padY)
        const hiT = Math.max(-padY, rect.height - dispH - padY)
        displayLeft = Math.min(hiL, Math.max(loL, displayLeft))
        displayTop = Math.min(hiT, Math.max(loT, displayTop))
```
(`iw`/`ih` above this block are now unused by the replaced code but are still declared at lines ~337–338; leave them — they are harmless. If `tsc`/eslint flags them as unused, delete the two `const iw`/`const ih` lines.)

- [ ] **Step 3: Typecheck + build**

Run: `npx tsc --noEmit -p tsconfig.json` → exit 0
Run: `npm run build` → `compiled successfully`

- [ ] **Step 4: Runtime verify — point under cursor stays fixed**

Create `verify_zoomcursor.cjs`:
```js
const puppeteer = require('puppeteer-core')
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const URL = 'http://localhost:8686/label?project_name=test2&task_index=0'
const sleep = ms => new Promise(r => setTimeout(r, ms))
;(async () => {
  const b = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox','--disable-gpu'] })
  const p = await b.newPage(); await p.setViewport({ width: 1500, height: 950 })
  await p.goto(URL, { waitUntil: 'networkidle2', timeout: 30000 }); await sleep(5000)
  // Pick an off-center cursor point; zoom in there; the image content under it should stay put.
  const probe = async () => p.evaluate(() => {
    const c = document.querySelector('canvas'); const r = c.getBoundingClientRect()
    // sample the image canvas pixel under a fixed screen point (300,250 from canvas origin)
    const x = 300, y = 250
    const cx = c.getContext('2d'); const d = cx.getImageData(Math.round(x), Math.round(y), 1, 1).data
    return { r: { x: r.x, y: r.y, w: r.width, h: r.height }, px: [d[0], d[1], d[2]] }
  })
  const before = await probe()
  await p.evaluate(() => {
    const c = document.querySelector('canvas'); const r = c.getBoundingClientRect()
    for (let i = 0; i < 6; i++) c.dispatchEvent(new WheelEvent('wheel', { ctrlKey: true, deltaY: -120, bubbles: true, clientX: r.left + 300, clientY: r.top + 250 }))
  })
  await sleep(600)
  const after = await probe()
  console.log('pixel under cursor before:', before.px, 'after:', after.px)
  await p.screenshot({ path: 'verify_zoomcursor.png' })
  await b.close()
})().catch(e => { console.log('FATAL', e.message); process.exit(1) })
```
Run: `node verify_zoomcursor.cjs`; view `verify_zoomcursor.png`. Expected: the zoom visibly centers on (300,250) (the area under that point enlarges in place, not drifting to a corner); image stays within the black viewport (no runaway blank). Then `rm -f verify_zoomcursor.cjs verify_zoomcursor.png`.

- [ ] **Step 5: Commit** — SKIP (user commits manually). Leave changes in the working tree.

---

## Task 2: Line-width multiplier — state + render threading (Feature 2)

**Files:** `app/src/types/state.ts`, `app/src/drawable/2d/label2d.ts`, `app/src/drawable/2d/polygon2d.ts`, `app/src/drawable/2d/label2d_list.ts`, `app/src/components/label2d_canvas.tsx`.

- [ ] **Step 1: Add the config field**

In `app/src/types/state.ts`, inside `ImageViewerConfigType` (after `displayLeft` at line ~222), add:
```ts
  /** Display-only multiplier for polyline stroke thickness (1 = default) */
  lineWidthMultiplier?: number
```

- [ ] **Step 2: Add the optional param to the abstract `draw`**

In `app/src/drawable/2d/label2d.ts`, change the abstract `draw` signature (lines 301–309) — add a trailing optional param:
```ts
  public abstract draw(
    canvas: Context2D,
    ratio: number,
    mode: DrawMode,
    isTrackLinking: boolean,
    hideLabelTags: boolean,
    sessionMode: ModeStatus | undefined,
    viewScale?: number,
    lineWidthMultiplier?: number
  ): void
```
(Other implementations — box2d/tag2d/custom_label — need no change: TS permits overrides to omit trailing params, and JS ignores the extra argument.)

- [ ] **Step 3: Consume it in `polygon2d.draw`**

In `app/src/drawable/2d/polygon2d.ts`, change the `draw` signature (lines 146–154) to add the param:
```ts
  public draw(
    context: Context2D,
    ratio: number,
    mode: DrawMode,
    isTrackLinking: boolean,
    hideLabelTags: boolean,
    sessionMode: ModeStatus | undefined,
    viewScale: number = 1,
    lineWidthMultiplier: number = 1
  ): void {
```
Then change the VIEW-mode line-width line (line 175) from:
```ts
        edgeStyle.lineWidth = Math.max(1, edgeStyle.lineWidth * styleFactor)
```
to (multiplier affects only the visible stroke; CONTROL/hit width unchanged):
```ts
        edgeStyle.lineWidth = Math.max(
          1,
          edgeStyle.lineWidth * styleFactor * lineWidthMultiplier
        )
```

- [ ] **Step 4: Thread it in `label2d_list.redraw`**

In `app/src/drawable/2d/label2d_list.ts`, add a trailing param to `redraw` (after `drawControl` at line 196):
```ts
    drawControl: boolean = true,
    lineWidthMultiplier: number = 1
```
And pass it to `v.draw` (the call at lines 257–265) — add it as the last argument:
```ts
        v.draw(
          ctx,
          ratio,
          mode,
          isTrackLinking,
          hideLabelTags ?? false,
          sessionMode,
          viewScale,
          lineWidthMultiplier
        )
```

- [ ] **Step 5: Pass it from `label2d_canvas.redraw`**

In `app/src/components/label2d_canvas.tsx`, in `redraw()` where `config` is read (around line 248–262), add after the `hiddenCategories` line:
```ts
      const lineWidthMultiplier: number =
        "lineWidthMultiplier" in config &&
        (config as unknown as { lineWidthMultiplier?: number })
          .lineWidthMultiplier !== undefined
          ? (config as unknown as { lineWidthMultiplier: number })
              .lineWidthMultiplier
          : 1
```
Then in the `this._labelList.redraw(...)` call (lines 288–296), append `lineWidthMultiplier` as the final argument after the existing last argument (`!isInteracting()`):
```ts
        hiddenLabelTypes,
        hiddenCategories,
        !isInteracting(),
        lineWidthMultiplier
      )
```
(Confirm the existing trailing args match; only add the new final argument.)

- [ ] **Step 6: Typecheck**

Run: `npx tsc --noEmit -p tsconfig.json` → exit 0. (Build happens in Task 3.)

- [ ] **Step 7: Commit** — SKIP.

---

## Task 3: Line-width toolbar buttons (Feature 2)

**Files:** `app/src/components/viewer2d.tsx` (imports + `getMenuComponents`).

- [ ] **Step 1: Import icons**

At the top of `app/src/components/viewer2d.tsx`, near the existing icon imports (`ZoomInIcon`, `ZoomOutIcon`, `FindReplaceIcon`), add:
```ts
import AddIcon from "@material-ui/icons/Add"
import RemoveIcon from "@material-ui/icons/Remove"
import LineWeightIcon from "@material-ui/icons/LineWeight"
```

- [ ] **Step 2: Add a width-change helper method**

In the `Viewer2D` class (e.g. right after the `zoom` method, before the closing brace at line ~368), add:
```ts
  /**
   * Change the polyline line-width multiplier (display-only).
   *
   * @param next absolute new multiplier (will be clamped), or undefined
   * @param delta additive change applied to the current value
   */
  protected changeLineWidth(delta: number, reset = false): void {
    const config = this._viewerConfig as ImageViewerConfigType
    const current = config.lineWidthMultiplier ?? 1
    const value = reset
      ? 1
      : Math.min(2.5, Math.max(0.5, Math.round((current + delta) * 10) / 10))
    const newConfig = { ...config, lineWidthMultiplier: value }
    Session.dispatch(changeViewerConfig(this._viewerId, newConfig))
  }
```

- [ ] **Step 3: Add three buttons in `getMenuComponents`**

In `getMenuComponents`, after `resetZoomButton` is defined and before the `return [...]` (currently `return [zoomInButton, zoomOutButton, resetZoomButton]` near line 189), add:
```ts
      const widthUpButton = (
        <Tooltip
          key={`widthUp2dButton${this.props.id}`}
          title="Thicker lines"
          enterDelay={500}
          TransitionComponent={Fade}
          TransitionProps={{ timeout: 600 }}
          arrow
        >
          <IconButton
            onClick={() => this.changeLineWidth(0.1)}
            className={this.props.classes.viewer_button}
            edge={"start"}
          >
            <AddIcon />
          </IconButton>
        </Tooltip>
      )
      const widthDownButton = (
        <Tooltip
          key={`widthDown2dButton${this.props.id}`}
          title="Thinner lines"
          enterDelay={500}
          TransitionComponent={Fade}
          TransitionProps={{ timeout: 600 }}
          arrow
        >
          <IconButton
            onClick={() => this.changeLineWidth(-0.1)}
            className={this.props.classes.viewer_button}
            edge={"start"}
          >
            <RemoveIcon />
          </IconButton>
        </Tooltip>
      )
      const widthResetButton = (
        <Tooltip
          key={`widthReset2dButton${this.props.id}`}
          title="Reset line width"
          enterDelay={500}
          TransitionComponent={Fade}
          TransitionProps={{ timeout: 600 }}
          arrow
        >
          <IconButton
            onClick={() => this.changeLineWidth(0, true)}
            className={this.props.classes.viewer_button}
            edge={"start"}
          >
            <LineWeightIcon />
          </IconButton>
        </Tooltip>
      )
```
Then change the return to include them:
```ts
      return [
        zoomInButton,
        zoomOutButton,
        resetZoomButton,
        widthUpButton,
        widthDownButton,
        widthResetButton
      ]
```

- [ ] **Step 4: Typecheck + build**

Run: `npx tsc --noEmit -p tsconfig.json` → exit 0
Run: `npm run build` → `compiled successfully`

- [ ] **Step 5: Runtime verify — buttons change thickness, don't touch data**

Create `verify_width.cjs`:
```js
const puppeteer = require('puppeteer-core')
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const URL = 'http://localhost:8686/label?project_name=test2&task_index=0'
const sleep = ms => new Promise(r => setTimeout(r, ms))
;(async () => {
  const b = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox','--disable-gpu'] })
  const p = await b.newPage(); await p.setViewport({ width: 1500, height: 950 })
  const errors = []; p.on('pageerror', e => errors.push(e.message))
  await p.goto(URL, { waitUntil: 'networkidle2', timeout: 30000 }); await sleep(5000)
  await p.screenshot({ path: 'verify_width_before.png' })
  // click "Thicker lines" tooltip button a few times
  const clicked = await p.evaluate(() => {
    const btns = Array.from(document.querySelectorAll('button'))
    // tooltip title is on an ancestor; match via aria-label/title text isn't reliable, so click by icon path count
    return btns.length
  })
  // click the thicker button via title lookup on the MUI tooltip wrapper
  for (let i = 0; i < 5; i++) {
    await p.evaluate(() => {
      const t = document.querySelector('[aria-label="Thicker lines"]') ||
        Array.from(document.querySelectorAll('button')).find(bn => bn.closest('[title="Thicker lines"]'))
      // MUI Tooltip sets title on the child; fall back to data attribute scan
    })
  }
  // robust: dispatch through React by finding the button whose tooltip title is "Thicker lines"
  await p.evaluate(() => {
    const wrap = Array.from(document.querySelectorAll('*')).find(el => el.getAttribute && el.getAttribute('title') === 'Thicker lines')
    const btn = wrap ? wrap.querySelector('button') || wrap.closest('button') : null
    for (let i = 0; i < 6 && btn; i++) btn.click()
  })
  await sleep(400)
  await p.screenshot({ path: 'verify_width_after.png' })
  console.log('errors:', errors.length ? errors.join(' | ') : 'none')
  await b.close()
})().catch(e => { console.log('FATAL', e.message); process.exit(1) })
```
Run: `node verify_width.cjs`; compare `verify_width_before.png` vs `verify_width_after.png` — polylines visibly thicker after, no errors. Also confirm export is unaffected: `curl -s "http://localhost:8686/getExport?project_name=test2" | findstr lineWidthMultiplier` returns nothing. Then `rm -f verify_width.cjs verify_width_*.png`.
(If the title-based button lookup proves flaky in headless, instead click by bounding box of the 4th toolbar button; the goal is only to confirm thickness changes + no errors.)

- [ ] **Step 6: Commit** — SKIP.

---

## Task 4: `pointer_pan_state` coordination module (Feature 3)

**Files:** Create `app/src/common/pointer_pan_state.ts`; Test `app/test/common/pointer_pan_state.test.ts`.

- [ ] **Step 1: Write the failing test**

```ts
// app/test/common/pointer_pan_state.test.ts
import {
  armEmptyDrag, isArmed, downPos, markPanned, didPan, reset,
  openPanWindow, inPanWindow, PAN_THRESHOLD, exceededThreshold
} from "../../src/common/pointer_pan_state"

describe("pointer_pan_state", () => {
  beforeEach(() => reset())

  test("arm/down/threshold/reset", () => {
    expect(isArmed()).toBe(false)
    armEmptyDrag(100, 100)
    expect(isArmed()).toBe(true)
    expect(downPos()).toEqual({ x: 100, y: 100 })
    expect(exceededThreshold(100 + PAN_THRESHOLD - 1, 100)).toBe(false)
    expect(exceededThreshold(100 + PAN_THRESHOLD + 1, 100)).toBe(true)
    reset()
    expect(isArmed()).toBe(false)
  })

  test("panned flag", () => {
    armEmptyDrag(0, 0)
    expect(didPan()).toBe(false)
    markPanned()
    expect(didPan()).toBe(true)
  })

  test("pan window", () => {
    expect(inPanWindow(1000)).toBe(false)
    openPanWindow(1000)
    expect(inPanWindow(1100)).toBe(true)
    expect(inPanWindow(1000 + 9999)).toBe(false)
  })
})
```

- [ ] **Step 2: Run (CI) / typecheck (local)**

Run (CI): `npx jest app/test/common/pointer_pan_state.test.ts -v` → FAIL (module missing).
Local: skip jest (redis); proceed to implement then `tsc`.

- [ ] **Step 3: Implement the module**

```ts
// app/src/common/pointer_pan_state.ts
/**
 * Transient, non-Redux coordination between Label2dCanvas (which owns the
 * hit-test) and Viewer2D (which owns panning) so they agree on whether the
 * current left-button gesture is a draw/edit or a map-like pan.
 *
 * - Label2dCanvas arms an "empty drag" on mouse-down over empty canvas and
 *   defers the draw; Viewer2D pans once movement passes the threshold and marks
 *   panned; Label2dCanvas, on mouse-up, draws only if no pan happened.
 * - A double-click opens a short pan window during which any drag pans.
 */
let armed = false
let panned = false
let downX = 0
let downY = 0
let panWindowUntilMs = 0

/** Pixels of movement that turn a click into a drag/pan. */
export const PAN_THRESHOLD = 5
/** How long after a double-click a drag is treated as pan (ms). */
export const PAN_WINDOW_MS = 400

/** Arm a deferred empty-space gesture at the given container-relative point. */
export function armEmptyDrag(x: number, y: number): void {
  armed = true
  panned = false
  downX = x
  downY = y
}

export function isArmed(): boolean {
  return armed
}

export function downPos(): { x: number; y: number } {
  return { x: downX, y: downY }
}

/** True once the pointer has moved past PAN_THRESHOLD from the down point. */
export function exceededThreshold(x: number, y: number): boolean {
  return Math.abs(x - downX) > PAN_THRESHOLD || Math.abs(y - downY) > PAN_THRESHOLD
}

export function markPanned(): void {
  panned = true
}

export function didPan(): boolean {
  return panned
}

export function reset(): void {
  armed = false
  panned = false
}

/** Open the post-double-click pan window. Pass Date.now(). */
export function openPanWindow(nowMs: number): void {
  panWindowUntilMs = nowMs + PAN_WINDOW_MS
}

/** Whether we are still in the double-click pan window. Pass Date.now(). */
export function inPanWindow(nowMs: number): boolean {
  return nowMs < panWindowUntilMs
}
```

- [ ] **Step 4: Typecheck**

Run: `npx tsc --noEmit -p tsconfig.json` → exit 0.

- [ ] **Step 5: Commit** — SKIP.

---

## Task 5: Double-click-drag pan + keep Ctrl+drag (Feature 3, low risk)

**Files:** `app/src/components/viewer2d.tsx` (imports, `onDoubleClick` line 253, `onMouseMove` pan gate ~line 210).

- [ ] **Step 1: Import the module**

Near the existing `import { notifyGesture } from "../common/interaction_state"`, add:
```ts
import {
  isArmed,
  markPanned,
  exceededThreshold,
  openPanWindow,
  inPanWindow
} from "../common/pointer_pan_state"
```

- [ ] **Step 2: Open the pan window on double-click**

Replace `protected onDoubleClick(): void {}` (line 253) with:
```ts
  protected onDoubleClick(): void {
    // A drag begun shortly after a double-click pans anywhere (trackpad-friendly).
    openPanWindow(Date.now())
  }
```

- [ ] **Step 3: Widen the pan gate in `onMouseMove`**

In `onMouseMove`, change the pan condition (line 210) from:
```ts
      if (e.ctrlKey || e.metaKey) {
```
to (adds the double-click window; the empty-drag case is wired in Task 6):
```ts
      const allowPan =
        e.ctrlKey ||
        e.metaKey ||
        inPanWindow(Date.now()) ||
        (isArmed() && exceededThreshold(this._mX, this._mY))
      if (allowPan) {
```
Then, immediately **inside** that `if` block (before `const dx = ...`), add:
```ts
        markPanned()
```
(`markPanned()` is a no-op for the ctrl/window cases that don't read it; it records the pan for the Task-6 empty-drag path.)

- [ ] **Step 4: Typecheck + build**

Run: `npx tsc --noEmit -p tsconfig.json` → exit 0
Run: `npm run build` → `compiled successfully`

- [ ] **Step 5: Runtime verify — double-click-drag pans; Ctrl+drag still pans**

Create `verify_pan_dblclick.cjs`:
```js
const puppeteer = require('puppeteer-core')
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const URL = 'http://localhost:8686/label?project_name=test2&task_index=0'
const sleep = ms => new Promise(r => setTimeout(r, ms))
const left = p => p.evaluate(() => document.querySelector('canvas').getBoundingClientRect().left)
;(async () => {
  const b = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox','--disable-gpu'] })
  const p = await b.newPage(); await p.setViewport({ width: 1500, height: 950 })
  const errors=[]; p.on('pageerror',e=>errors.push(e.message))
  await p.goto(URL, { waitUntil: 'networkidle2', timeout: 30000 }); await sleep(5000)
  // zoom in first
  await p.evaluate(async () => { const c=document.querySelector('canvas'); const r=c.getBoundingClientRect(); for(let i=0;i<6;i++){c.dispatchEvent(new WheelEvent('wheel',{ctrlKey:true,deltaY:-120,bubbles:true,clientX:r.left+r.width/2,clientY:r.top+r.height/2}));await new Promise(r=>setTimeout(r,25))} }); await sleep(400)
  const r = await p.evaluate(() => { const x=document.querySelector('canvas').getBoundingClientRect(); return {x:x.x,y:x.y,w:x.width,h:x.height} })
  const sx=Math.round(r.x+r.w/2), sy=Math.round(r.y+r.h/2)
  const before = await left(p)
  // double-click then drag
  await p.mouse.click(sx, sy); await p.mouse.click(sx, sy) // double click
  await p.mouse.move(sx, sy); await p.mouse.down()
  for (let i=1;i<=10;i++){ await p.mouse.move(sx+i*20, sy); await sleep(16) }
  await p.mouse.up(); await sleep(400)
  const after = await left(p)
  console.log('double-click-drag shift px:', Math.round(after-before), '(expect ~200)')
  console.log('errors:', errors.length?errors.join(' | '):'none')
  await b.close()
})().catch(e=>{console.log('FATAL',e.message);process.exit(1)})
```
Run: `node verify_pan_dblclick.cjs`. Expected: shift ≈ 200px (pan happened), no errors. Then `rm -f verify_pan_dblclick.cjs`.

- [ ] **Step 6: Commit** — SKIP.

---

## Task 6: Empty-space drag pans, click draws (Feature 3, higher risk)

**Files:** `app/src/components/label2d_canvas.tsx` (`onMouseDown`/`onMouseMove`/`onMouseUp`), `app/src/components/viewer2d.tsx` (already gated in Task 5).

Behavior: on empty canvas, defer the draw; if the drag exceeds the threshold it pans (handled by Viewer2D via the armed flag from Task 5); a release without a drag replays the click as a draw.

- [ ] **Step 1: Import the module in label2d_canvas**

In `app/src/components/label2d_canvas.tsx`, add near the `interaction_state` import:
```ts
import {
  armEmptyDrag,
  isArmed,
  didPan,
  reset as resetPanState,
  inPanWindow
} from "../common/pointer_pan_state"
```

- [ ] **Step 2: Defer the draw on empty mouse-down**

In `onMouseDown` (lines 325–351), after computing `mousePos`/`[labelIndex, handleIndex]` (line 344) and **before** the `this._labelHandler.onMouseDown(...)` call, insert:
```ts
    // Map-like pan: an explicit pan gesture (ctrl/cmd, or within the
    // double-click window) must not draw/edit — let Viewer2D handle the drag.
    if (e.ctrlKey || e.metaKey || inPanWindow(Date.now())) {
      return
    }
    // On empty canvas (no label/handle under cursor), defer the draw decision:
    // a release without movement draws (handled in onMouseUp); a drag pans
    // (Viewer2D, via the armed flag). Container-relative coords drive the
    // movement threshold, matching Viewer2D's this._mX/_mY.
    if (labelIndex < 0) {
      const rect = (this.display as HTMLDivElement).getBoundingClientRect()
      armEmptyDrag(e.clientX - rect.left, e.clientY - rect.top)
      return
    }
```
(Leave the existing `this._labelHandler.onMouseDown(...)` + `onDrawableUpdate()` for the on-a-label case.)

- [ ] **Step 3: Suppress label-move while an empty-drag is armed**

In `onMouseMove` (lines 374–405), right after the freeze guard (`if (this.checkFreeze()) return`) and the crosshair update, add a short-circuit so a deferred empty-drag does not run the label handler (the crosshair still updates above):
```ts
    if (isArmed()) {
      // While a deferred empty-space gesture is in progress, do not draw/edit.
      // Viewer2D decides pan-vs-nothing from the movement threshold.
      return
    }
```
Place this **after** the crosshair block (lines 379–381) and **before** `const mousePos = this.getMousePos(e)` (line 384).

- [ ] **Step 4: Replay the draw on a click (no pan) in mouse-up**

In `onMouseUp` (lines 358–367), at the very start of the method body (after the `if (e.button !== 0 || this.checkFreeze()) return` guard), add:
```ts
    if (isArmed()) {
      const panned = didPan()
      resetPanState()
      if (!panned) {
        // It was a click, not a pan: perform the deferred draw now
        // (down then up) so empty-space click still adds a polyline point.
        const pos = this.getMousePos(e)
        const [li, hi] = this.fetchHandleId(pos)
        this._labelHandler.onMouseDown(pos, li, hi)
        this._labelHandler.onMouseUp(pos, li, hi)
        this._labelList.onDrawableUpdate()
      }
      return
    }
```

- [ ] **Step 5: Typecheck + build**

Run: `npx tsc --noEmit -p tsconfig.json` → exit 0
Run: `npm run build` → `compiled successfully`

- [ ] **Step 6: Runtime verify — drawing/editing intact AND empty-drag pans**

Create `verify_pan_empty.cjs` (drives real clicks/drags):
```js
const puppeteer = require('puppeteer-core')
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const URL = 'http://localhost:8686/label?project_name=test2&task_index=0'
const sleep = ms => new Promise(r => setTimeout(r, ms))
const left = p => p.evaluate(() => document.querySelector('canvas').getBoundingClientRect().left)
const labelCount = p => p.evaluate(() => (window.Session && window.Session.label2dList ? window.Session.label2dList.labelList.length : -1))
;(async () => {
  const b = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox','--disable-gpu'] })
  const p = await b.newPage(); await p.setViewport({ width: 1500, height: 950 })
  const errors=[]; p.on('pageerror',e=>errors.push(e.message))
  await p.goto(URL, { waitUntil: 'networkidle2', timeout: 30000 }); await sleep(5000)
  const r = await p.evaluate(() => { const x=document.querySelector('canvas').getBoundingClientRect(); return {x:x.x,y:x.y,w:x.width,h:x.height} })
  const cx = Math.round(r.x + r.w*0.5), cy = Math.round(r.y + r.h*0.5)
  // (a) empty-space DRAG should PAN (canvas shifts), not create a label
  const beforeLeft = await left(p)
  await p.mouse.move(cx, cy); await p.mouse.down()
  for (let i=1;i<=10;i++){ await p.mouse.move(cx-i*18, cy-i*6); await sleep(16) }
  await p.mouse.up(); await sleep(300)
  const afterLeft = await left(p)
  console.log('empty-drag pan shift px:', Math.round(afterLeft-beforeLeft), '(expect non-zero)')
  // (b) empty-space CLICKS should DRAW a polyline (3 points) then Enter
  await p.mouse.click(cx-100, cy-100); await sleep(60)
  await p.mouse.click(cx, cy-60); await sleep(60)
  await p.mouse.click(cx+100, cy-100); await sleep(60)
  await p.keyboard.press('Enter'); await sleep(200)
  await p.screenshot({ path: 'verify_pan_empty.png' })
  console.log('errors:', errors.length?errors.join(' | '):'none')
  await b.close()
})().catch(e=>{console.log('FATAL',e.message);process.exit(1)})
```
Run: `node verify_pan_empty.cjs`; view `verify_pan_empty.png`. Expected:
- (a) non-zero pan shift from the empty-space drag (and no stray 1-point label left behind).
- (b) a new 3-point polyline drawn by the clicks is visible after Enter.
- no page errors.
Then `rm -f verify_pan_empty.cjs verify_pan_empty.png`.

- [ ] **Step 7: Manual-style verification of edit paths (still via the harness)**

Extend or re-run with: select the drawn polyline (click on it), drag a vertex, press D on a vertex (delete), press C on a segment (curve). Confirm each still works (screenshot) — the on-a-label path was untouched, but verify because this task is high-risk.
If the click-replay (Step 4) misbehaves for the FIRST point (e.g. a 1-point label persists), fall back: scope `armEmptyDrag` to fire only when nothing is selected and not mid-draw (guard with `Session.label2dList.selectedLabels.length === 0`), so empty-drag-pan applies in the idle state and drawing is never deferred. Document whichever path is taken.

- [ ] **Step 8: Commit** — SKIP.

---

## Task 7: Cursor feedback for panning (Feature 3, polish)

**Files:** `app/src/components/label2d_canvas.tsx` (`onMouseMove` cursor block lines 400–404).

- [ ] **Step 1: Show grab cursor while a pan gesture is active**

In `onMouseMove`, the cursor block at the end (lines 400–404) currently:
```ts
    if (this._labelHandler.highlightedLabel !== null) {
      this.setCursor(this._labelHandler.highlightedLabel.highlightCursor)
    } else {
      this.setDefaultCursor()
    }
```
This block is only reached when NOT armed (Task 6 returns early when armed). Add a grab cursor on mouse-down over empty space by setting it in `onMouseDown` right after `armEmptyDrag(...)` (Task 6, Step 2):
```ts
      this.setCursor("grab")
```
and restore the default in `onMouseUp` inside the `if (isArmed())` block (Task 6, Step 4), right before `return`:
```ts
      this.setDefaultCursor()
```

- [ ] **Step 2: Typecheck + build**

Run: `npx tsc --noEmit -p tsconfig.json` → exit 0
Run: `npm run build` → `compiled successfully`

- [ ] **Step 3: Runtime verify** — reopen the editor; on empty-space press-drag the cursor shows grab and the image pans; on release it returns to crosshair. (Re-run `verify_pan_empty.cjs` from Task 6; confirm still no errors.)

- [ ] **Step 4: Commit** — SKIP.

---

## Self-review (coverage)

- Spec Feature 1 (live cursor + clamp) → Task 1. Feature 2 (field, threading, polygon2d, 3 buttons, display-only) → Tasks 2–3. Feature 3 (pointer_pan_state, double-click-drag, ctrl+drag kept, empty-drag pan with defer/replay, cursor) → Tasks 4–7.
- Type consistency: `lineWidthMultiplier` optional on config (T2.1) and abstract draw (T2.2), `= 1` default in polygon2d (T2.3) and label2d_list (T2.4); module exports `armEmptyDrag/isArmed/downPos/exceededThreshold/markPanned/didPan/reset/openPanWindow/inPanWindow/PAN_THRESHOLD/PAN_WINDOW_MS` (T4) used unchanged in T5–T7.
- No placeholders: every code step shows the exact code; verification uses runtime harness (jest is CI-only here).
- Risk: Task 6 is the only behavior-risky change; it has explicit draw + edit verification and a documented fallback (scope empty-drag-pan to the idle state).
