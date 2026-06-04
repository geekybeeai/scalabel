# Smooth Zoom/Scroll/Pan Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make zoom/scroll/pan on large (~27MB) images smooth and eliminate the blank-canvas flash, by rendering cheaply during a gesture and crisply when it settles.

**Architecture:** A transient, non-Redux "interaction state" signal (set by every gesture, auto-cleared ~150ms after the last one) drives two paths: an in-motion path (no blank, reduced backing resolution, control canvas skipped) and an idle path (full-resolution crisp redraw). Pan is RAF-batched like zoom already is.

**Tech Stack:** TypeScript, React 17, Redux, HTML Canvas 2D. Verification via `tsc --noEmit`, `npm run build`, and headless-Chrome screenshots (puppeteer-core + installed Chrome).

---

## Environment / verification notes (read first)

- **jest can't run on this Windows dev box** — jest's global setup spawns
  `redis-server`, which isn't installed (`spawn redis-server ENOENT`). Unit
  tests below are written as jest specs (correct home, runnable in CI where
  redis exists). For **local** confirmation rely on: `npx tsc --noEmit -p
  tsconfig.json`, `npm run build`, and the runtime screenshot harness.
- **Runtime harness prerequisites:** Redis running on 6379, the Scalabel server
  running (`node app/dist/main.js --config ./local-data/scalabel/config.yml`),
  and a large-image project (e.g. `test2`). `puppeteer-core` is already in
  `node_modules`; Chrome at `C:/Program Files/Google/Chrome/Application/chrome.exe`.
- **Commit messages** end with the project's `Co-Authored-By` trailer.

## File structure

- **Create** `app/src/common/interaction_state.ts` — the transient gesture
  signal (single responsibility: "are we mid-gesture", + idle notification).
- **Create** `app/test/common/interaction_state.test.ts` — unit tests for it.
- **Modify** `app/src/components/image_canvas.tsx` — no-blank synchronous draw;
  subscribe to idle for the crisp pass.
- **Modify** `app/src/view_config/image.ts` — reduced backing resolution while
  interacting.
- **Modify** `app/src/drawable/2d/label2d_list.ts` — optional `drawControl`
  param on `redraw`.
- **Modify** `app/src/components/label2d_canvas.tsx` — skip control canvas in
  motion, `willReadFrequently`, subscribe to idle.
- **Modify** `app/src/components/viewer2d.tsx` — call `notifyGesture()` from
  zoom/wheel/pan handlers; RAF-batch pan.

---

## Task 1: Interaction-state module (R2)

**Files:**
- Create: `app/src/common/interaction_state.ts`
- Test: `app/test/common/interaction_state.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// app/test/common/interaction_state.test.ts
import {
  notifyGesture,
  isInteracting,
  onIdle,
  _resetForTest,
  IDLE_MS
} from "../../src/common/interaction_state"

describe("interaction_state", () => {
  beforeEach(() => {
    jest.useFakeTimers()
    _resetForTest()
  })
  afterEach(() => jest.useRealTimers())

  test("notifyGesture marks interacting, clears after IDLE_MS", () => {
    expect(isInteracting()).toBe(false)
    notifyGesture()
    expect(isInteracting()).toBe(true)
    jest.advanceTimersByTime(IDLE_MS - 1)
    expect(isInteracting()).toBe(true)
    jest.advanceTimersByTime(1)
    expect(isInteracting()).toBe(false)
  })

  test("repeated gestures keep it active (debounced)", () => {
    notifyGesture()
    jest.advanceTimersByTime(IDLE_MS - 10)
    notifyGesture()
    jest.advanceTimersByTime(IDLE_MS - 10)
    expect(isInteracting()).toBe(true)
  })

  test("onIdle fires once when the gesture settles", () => {
    const cb = jest.fn()
    onIdle(cb)
    notifyGesture()
    expect(cb).not.toBeCalled()
    jest.advanceTimersByTime(IDLE_MS)
    expect(cb).toBeCalledTimes(1)
  })

  test("onIdle returns an unsubscribe", () => {
    const cb = jest.fn()
    const off = onIdle(cb)
    off()
    notifyGesture()
    jest.advanceTimersByTime(IDLE_MS)
    expect(cb).not.toBeCalled()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx jest app/test/common/interaction_state.test.ts -v`
Expected: FAIL — module `interaction_state` not found.
(Local box: if jest aborts with `spawn redis-server ENOENT`, that's the env
limitation — proceed; CI runs it.)

- [ ] **Step 3: Write minimal implementation**

```ts
// app/src/common/interaction_state.ts
/**
 * Transient, non-Redux signal for "is the user mid zoom/scroll/pan gesture".
 * Kept out of Redux so per-frame gesture updates don't churn the store.
 * Auto-clears IDLE_MS after the last gesture and notifies idle listeners then.
 */
type Listener = () => void

export const IDLE_MS = 150

let active = false
let timer: ReturnType<typeof setTimeout> | null = null
const idleListeners: Set<Listener> = new Set()

/** Mark a gesture frame; (re)starts the idle countdown. */
export function notifyGesture(): void {
  active = true
  if (timer !== null) {
    clearTimeout(timer)
  }
  timer = setTimeout(() => {
    active = false
    timer = null
    idleListeners.forEach((l) => l())
  }, IDLE_MS)
}

/** True while a gesture is in progress. */
export function isInteracting(): boolean {
  return active
}

/** Subscribe to "gesture settled"; returns an unsubscribe fn. */
export function onIdle(listener: Listener): () => void {
  idleListeners.add(listener)
  return () => {
    idleListeners.delete(listener)
  }
}

/** Test-only reset. */
export function _resetForTest(): void {
  active = false
  if (timer !== null) {
    clearTimeout(timer)
  }
  timer = null
  idleListeners.clear()
}
```

- [ ] **Step 4: Run test to verify it passes (CI) / typecheck (local)**

Run (CI): `npx jest app/test/common/interaction_state.test.ts -v` → PASS.
Run (local): `npx tsc --noEmit -p tsconfig.json` → exit 0.

- [ ] **Step 5: Commit**

```bash
git add app/src/common/interaction_state.ts app/test/common/interaction_state.test.ts
git commit -m "feat: add transient interaction-state signal for gesture rendering"
```

---

## Task 2: Eliminate the blank flash (R1)

The image is cleared during React commit but redrawn one RAF later, leaving a
visible blank. Fix: draw the image **synchronously** right after the canvas is
(re)sized in the ref callback, so the resized canvas is painted in the same
commit, before the browser paints.

**Files:**
- Modify: `app/src/components/image_canvas.tsx` (render ref callback, ~line 96)

- [ ] **Step 1: Add the synchronous draw after `updateScale`**

In the `ref` callback, after the `this.updateScale(...)` call inside the
`if (...) { ... }` block, add an immediate redraw:

```ts
            if (
              displayRect.width !== 0 &&
              !isNaN(displayRect.width) &&
              displayRect.height !== 0 &&
              !isNaN(displayRect.height) &&
              isFrameLoaded(this.state, item, sensor) &&
              this.imageContext !== null
            ) {
              this.updateScale(this.imageCanvas, this.imageContext, true)
              // Draw synchronously in the same commit so the freshly-resized
              // (and therefore cleared) canvas is never shown blank. The
              // deferred RAF redraw in componentDidUpdate would otherwise leave
              // a blank gap that is visible while a slow blit is pending.
              this.redraw()
            }
```

- [ ] **Step 2: Typecheck**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: exit 0.

- [ ] **Step 3: Build**

Run: `npm run build`
Expected: `compiled successfully`.

- [ ] **Step 4: Runtime verify — no blank mid-zoom**

Create `verify_blank.cjs` (delete after):

```js
const puppeteer = require('puppeteer-core')
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const URL = 'http://localhost:8686/label?project_name=test2&task_index=0'
;(async () => {
  const b = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox','--disable-gpu'] })
  const p = await b.newPage()
  await p.setViewport({ width: 1500, height: 950 })
  await p.goto(URL, { waitUntil: 'networkidle2', timeout: 30000 })
  await new Promise(r => setTimeout(r, 4000))
  // sample the image-canvas pixels right after several zoom keystrokes
  for (let i = 0; i < 8; i++) { await p.keyboard.press('Equal'); await new Promise(r=>setTimeout(r,30)) }
  await p.screenshot({ path: 'verify_blank.png' })
  // is the image canvas non-empty immediately after a zoom press?
  const nonBlank = await p.evaluate(() => {
    const c = document.querySelector('canvas')
    const cx = c.getContext('2d')
    const d = cx.getImageData(0, 0, Math.min(200,c.width), Math.min(200,c.height)).data
    let nz = 0; for (let i=3;i<d.length;i+=4) if (d[i] !== 0) nz++
    return nz > 0
  })
  console.log('image canvas non-blank after zoom:', nonBlank)
  await b.close()
})().catch(e=>{console.log('FATAL',e.message);process.exit(1)})
```

Run: `node verify_blank.cjs` then view `verify_blank.png`.
Expected: `non-blank after zoom: true` and the screenshot shows the image (not a
white/empty canvas) at high zoom. Then `rm -f verify_blank.cjs verify_blank.png`.

- [ ] **Step 5: Commit**

```bash
git add app/src/components/image_canvas.tsx
git commit -m "fix: draw image synchronously after resize to remove zoom blank flash"
```

---

## Task 3: Reduced backing resolution during motion + crisp idle pass for the image (R3, R5)

**Files:**
- Modify: `app/src/view_config/image.ts` (`getUpResRatio`, ~lines 26-28; or the
  resolution computation in `updateCanvasScale`)
- Modify: `app/src/components/image_canvas.tsx` (subscribe to `onIdle`)
- Modify: `app/src/components/viewer2d.tsx` (call `notifyGesture()` in zoom/wheel)

- [ ] **Step 1: Lower resolution while interacting**

In `app/src/view_config/image.ts`, import the signal and apply an in-motion
downscale where the up-res ratio is decided. Add the constant and update
`getUpResRatio`:

```ts
import { isInteracting } from "../common/interaction_state"

/** Backing-resolution multiplier applied while a gesture is in progress. */
export const MOTION_RESOLUTION_SCALE = 0.7

/**
 * Adaptive up-resolution ratio. 2x retina sharpness at low zoom, 1x at high
 * zoom (pixels already visible). While interacting, render cheaper so the
 * gesture stays smooth; the idle pass restores full resolution.
 */
function getUpResRatio(viewScale: number): number {
  if (isInteracting()) {
    return MOTION_RESOLUTION_SCALE
  }
  return viewScale > 3 ? 1 : 2
}
```

(If `getUpResRatio` already exists with that signature, replace its body with
the above. Keep the existing 4096 cap logic in `updateCanvasScale` unchanged —
it still applies on top.)

- [ ] **Step 2: Image canvas re-renders crisply on idle**

In `app/src/components/image_canvas.tsx`, import `onIdle` and subscribe in
`componentDidMount`, unsubscribe in `componentWillUnmount`. A `forceUpdate`
re-runs the render ref → `updateScale` (now full-res since `isInteracting()` is
false) → synchronous `redraw()` from Task 2.

```ts
import { onIdle } from "../common/interaction_state"
```

Add a field and lifecycle (inside the `ImageCanvas` class):

```ts
  /** unsubscribe from interaction-idle notifications */
  private _offIdle: (() => void) | null = null

  public componentDidMount(): void {
    super.componentDidMount()
    // After a gesture settles, re-render at full resolution (crisp pass).
    this._offIdle = onIdle(() => this.forceUpdate())
  }

  public componentWillUnmount(): void {
    super.componentWillUnmount()
    if (this._offIdle !== null) {
      this._offIdle()
      this._offIdle = null
    }
  }
```

(If `ImageCanvas` has no `componentDidMount`/`componentWillUnmount`, add them
calling `super`. `DrawableCanvas` defines both.)

- [ ] **Step 3: Fire `notifyGesture` from zoom/wheel**

In `app/src/components/viewer2d.tsx`, import and call the signal at the start of
the wheel handler and the `zoom` method:

```ts
import { notifyGesture } from "../common/interaction_state"
```

In `onWheel(...)` (first line of the handler body) and at the top of `zoom(...)`:

```ts
    notifyGesture()
```

- [ ] **Step 4: Typecheck + build**

Run: `npx tsc --noEmit -p tsconfig.json` → exit 0
Run: `npm run build` → `compiled successfully`

- [ ] **Step 5: Runtime verify — smoother zoom, crisp after settle**

Create `verify_zoom.cjs` (delete after):

```js
const puppeteer = require('puppeteer-core')
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const URL = 'http://localhost:8686/label?project_name=test2&task_index=0'
;(async () => {
  const b = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox','--disable-gpu'] })
  const p = await b.newPage()
  await p.setViewport({ width: 1500, height: 950 })
  await p.goto(URL, { waitUntil: 'networkidle2', timeout: 30000 })
  await new Promise(r => setTimeout(r, 4000))
  // measure event-handler durations during a zoom burst
  await p.evaluate(() => { window.__d = []; new PerformanceObserver(l => l.getEntries().forEach(e => window.__d.push(Math.round(e.duration)))).observe({ type: 'event', buffered: true }) })
  for (let i=0;i<15;i++){ await p.keyboard.press('Equal'); await new Promise(r=>setTimeout(r,40)) }
  await new Promise(r=>setTimeout(r,400)) // let idle crisp pass run
  await p.screenshot({ path: 'verify_zoom.png' })
  const durations = await p.evaluate(() => window.__d)
  console.log('event durations (ms):', durations.join(','))
  await b.close()
})().catch(e=>{console.log('FATAL',e.message);process.exit(1)})
```

Run: `node verify_zoom.cjs` then view `verify_zoom.png`.
Expected: max event duration noticeably lower than the ~230ms baseline; the
screenshot (taken after the 400ms settle) is **crisp**, not low-res. Then
`rm -f verify_zoom.cjs verify_zoom.png`.

- [ ] **Step 6: Commit**

```bash
git add app/src/view_config/image.ts app/src/components/image_canvas.tsx app/src/components/viewer2d.tsx
git commit -m "perf: render image at reduced resolution during gestures, sharpen on idle"
```

---

## Task 4: Skip the control canvas during motion + label idle pass (R4)

The control (hit-detection) canvas is invisible and ~1/3 of per-frame canvas
work. Skip it while interacting; rebuild on idle.

**Files:**
- Modify: `app/src/drawable/2d/label2d_list.ts` (`redraw`)
- Modify: `app/src/components/label2d_canvas.tsx` (pass flag, subscribe to idle)

- [ ] **Step 1: Add a `drawControl` param to `Label2DList.redraw`**

In `app/src/drawable/2d/label2d_list.ts`, add a trailing optional param and use
it to skip the CONTROL pass. Change the signature and the per-label draw loop:

```ts
  public redraw(
    labelContext: Context2D,
    controlContext: Context2D,
    ratio: number,
    hideLabels?: boolean,
    hideLabelTags?: boolean,
    sessionMode?: ModeStatus,
    viewScale?: number,
    viewportBounds?: [number, number, number, number],
    hiddenLabelTypes?: string[],
    hiddenCategories?: number[],
    drawControl: boolean = true
  ): void {
```

Replace the final draw loop so the CONTROL pass is conditional:

```ts
    labelsToDraw.forEach((v) => {
      const passes = drawControl
        ? [
            { ctx: labelContext, mode: DrawMode.VIEW },
            { ctx: controlContext, mode: DrawMode.CONTROL }
          ]
        : [{ ctx: labelContext, mode: DrawMode.VIEW }]
      passes.forEach(({ ctx, mode }) => {
        v.draw(
          ctx,
          ratio,
          mode,
          isTrackLinking,
          hideLabelTags ?? false,
          sessionMode,
          viewScale
        )
      })
    })
```

- [ ] **Step 2: Pass `!isInteracting()` from the canvas; rebuild on idle**

In `app/src/components/label2d_canvas.tsx`:

```ts
import { isInteracting, onIdle } from "../common/interaction_state"
```

At the `this._labelList.redraw(...)` call, append the new argument:

```ts
        hiddenLabelTypes,
        hiddenCategories,
        !isInteracting()
```

In `componentDidMount`, after the existing subscriptions, add:

```ts
    this._offIdle = onIdle(() => this.redraw())
```

Add the field and unsubscribe in `componentWillUnmount`:

```ts
  /** unsubscribe from interaction-idle notifications */
  private _offIdle: (() => void) | null = null
```

```ts
    if (this._offIdle !== null) {
      this._offIdle()
      this._offIdle = null
    }
```

- [ ] **Step 3: Typecheck + build**

Run: `npx tsc --noEmit -p tsconfig.json` → exit 0
Run: `npm run build` → `compiled successfully`

- [ ] **Step 4: Runtime verify — labels visible during zoom, hit-test works after**

Reuse the `verify_zoom.cjs` harness from Task 3 (view the screenshot): after the
settle, labels are drawn and crisp. Then manually (or via puppeteer) confirm
hovering a label still highlights it (control canvas rebuilt on idle): extend
the script to `await p.mouse.move(x,y)` over a known label after settle and
screenshot; the label should show its hover/highlight state.
Expected: labels render during and after zoom; hover works once settled.

- [ ] **Step 5: Commit**

```bash
git add app/src/drawable/2d/label2d_list.ts app/src/components/label2d_canvas.tsx
git commit -m "perf: skip control canvas during gestures, rebuild on idle"
```

---

## Task 5: RAF-batch pan + fire `notifyGesture` on pan (R6)

Pan currently dispatches to Redux on every `mousemove`. Batch it per frame like
zoom, and mark gestures.

**Files:**
- Modify: `app/src/components/viewer2d.tsx` (`onMouseMove` pan branch)

- [ ] **Step 1: Add pan RAF batching fields**

In `viewer2d.tsx`, add instance fields near the existing zoom-RAF fields:

```ts
  /** pending pan offset accumulated within a frame */
  private _pendingPan: { left: number; top: number } | null = null
  /** whether a pan RAF is already scheduled */
  private _panRAFPending: boolean = false
```

- [ ] **Step 2: Route pan through the batcher**

In the pan branch of `onMouseMove` (where it currently builds the new config and
dispatches `changeViewerConfig` with updated `displayLeft`/`displayTop`),
replace the direct dispatch with: compute the new left/top, store in
`_pendingPan`, call `notifyGesture()`, and schedule one RAF that applies the
latest pending pan:

```ts
    notifyGesture()
    this._pendingPan = { left: newDisplayLeft, top: newDisplayTop }
    if (!this._panRAFPending) {
      this._panRAFPending = true
      requestAnimationFrame(() => {
        this._panRAFPending = false
        const pan = this._pendingPan
        this._pendingPan = null
        if (pan === null) {
          return
        }
        const config = { ...this.activeViewerConfig() }
        ;(config as ImageViewerConfigType).displayLeft = pan.left
        ;(config as ImageViewerConfigType).displayTop = pan.top
        Session.dispatch(changeViewerConfig(this.safeViewerId(), config))
      })
    }
```

(Use whatever this component already uses to read the active config and viewer
id — mirror the existing zoom dispatch in the same file. `newDisplayLeft` /
`newDisplayTop` are the values the old code computed before dispatching.)

- [ ] **Step 3: Typecheck + build**

Run: `npx tsc --noEmit -p tsconfig.json` → exit 0
Run: `npm run build` → `compiled successfully`

- [ ] **Step 4: Runtime verify — smooth pan, correct final position**

Create `verify_pan.cjs` (delete after): zoom in a few steps, then drag with Ctrl
held across the canvas and screenshot during + after.

```js
const puppeteer = require('puppeteer-core')
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const URL = 'http://localhost:8686/label?project_name=test2&task_index=0'
;(async () => {
  const b = await puppeteer.launch({ executablePath: CHROME, headless:'new', args:['--no-sandbox','--disable-gpu'] })
  const p = await b.newPage(); await p.setViewport({width:1500,height:950})
  await p.goto(URL,{waitUntil:'networkidle2',timeout:30000}); await new Promise(r=>setTimeout(r,4000))
  for(let i=0;i<6;i++){await p.keyboard.press('Equal');await new Promise(r=>setTimeout(r,40))}
  await new Promise(r=>setTimeout(r,300))
  await p.keyboard.down('Control')
  await p.mouse.move(800,500); await p.mouse.down()
  for(let i=0;i<20;i++){await p.mouse.move(800-i*15,500-i*8);await new Promise(r=>setTimeout(r,16))}
  await p.mouse.up(); await p.keyboard.up('Control')
  await new Promise(r=>setTimeout(r,300))
  await p.screenshot({path:'verify_pan.png'})
  console.log('pan done')
  await b.close()
})().catch(e=>{console.log('FATAL',e.message);process.exit(1)})
```

Run: `node verify_pan.cjs`, view `verify_pan.png`.
Expected: image panned to the new position, crisp after settle, no blank during
drag. Then `rm -f verify_pan.cjs verify_pan.png`.

- [ ] **Step 5: Commit**

```bash
git add app/src/components/viewer2d.tsx
git commit -m "perf: RAF-batch pan and mark it as a gesture"
```

---

## Task 6: `willReadFrequently` on the control context (R8)

**Files:**
- Modify: `app/src/components/label2d_canvas.tsx` (where the control canvas
  `getContext("2d")` is created — `updateCanvas`)

- [ ] **Step 1: Add the hint to the control context only**

Find where the control canvas context is created (the control canvas, not the
label canvas) and pass the option:

```ts
    this.controlContext = controlCanvas.getContext("2d", {
      willReadFrequently: true
    })
```

(Leave the label/visible canvas `getContext("2d")` unchanged — that one is
composited, not read back.)

- [ ] **Step 2: Typecheck + build**

Run: `npx tsc --noEmit -p tsconfig.json` → exit 0
Run: `npm run build` → `compiled successfully`

- [ ] **Step 3: Runtime verify — hit-testing still works**

Open the editor, hover/click labels; selection and hover highlight still work
(the `getImageData` hit-test reads from the control canvas). Confirm no console
errors. (Can reuse the Task 4 hover check.)

- [ ] **Step 4: Commit**

```bash
git add app/src/components/label2d_canvas.tsx
git commit -m "perf: mark control canvas context willReadFrequently for hit-test reads"
```

---

## Task 7 (stretch, optional): CSS-transform preview during motion (R7)

Only attempt if Tasks 2–6 don't make motion smooth enough at extreme zoom.
During a gesture, instead of re-blitting, apply `transform: translate()/scale()`
to the already-painted image canvas (pure GPU) and do the real blit on idle. The
focal-point transform math must mirror the existing zoom offset computation in
`viewer2d.tsx` `zoom()` exactly. **Defer** — design/verify separately; do not
implement blind. Left as a documented follow-up, not part of this plan's
committed scope.

---

## Self-review (coverage)

- R1 no-blank → Task 2. R2 signal → Task 1. R3 in-motion res → Task 3.
  R4 skip control → Task 4. R5 idle crisp pass → Tasks 3 (image) & 4 (labels).
  R6 RAF pan → Task 5. R7 CSS transform → Task 7 (deferred). R8
  willReadFrequently → Task 6.
- `notifyGesture()` is wired from zoom/wheel (Task 3) and pan (Task 5) — every
  gesture source marks interaction.
- Types consistent: `notifyGesture`/`isInteracting`/`onIdle`/`IDLE_MS`/
  `_resetForTest` defined in Task 1 and used unchanged in Tasks 3–5; `redraw`'s
  new `drawControl` param (Task 4) matches its caller.
