# Sidebar "Show image" Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Show image" sidebar checkbox that hides only the underlying image layer (annotations stay visible), on by default, stored per-viewer.

**Architecture:** A new optional `hideImage` flag on the per-viewer config gates the image canvas's draw call; the label canvases are a separate layer and are untouched. A sidebar checkbox toggles the flag through the existing `changeViewerConfig` pattern.

**Tech Stack:** TypeScript, React 16 + Redux, HTML canvas, `@material-ui/core` v4.

## Global Constraints

- **Default: image shown** (`hideImage` unset/false) — existing sessions unaffected.
- **Per-viewer config flag**, mirroring `hideTags` / `showCurvesOnly`.
- **Hide the image layer only** — never the labels/tags/overlays (they render on separate canvases).
- **No new `console.log`.**
- Commit messages end with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

## Testing Strategy (read before starting)

No local unit harness covers the image canvas or sidebar. Gate on `npx tsc --noEmit`, `npx eslint -c .eslintrc.json <files>` (the real gate is **zero non-`prettier/prettier`** violations vs HEAD — the pre-existing CRLF `prettier/prettier` noise is ignored), and a runtime check via the **`verify`** skill. All commands run from the repo root `d:/Nikhil/Projects/GitHub/scalabel`.

**Runtime harness note:** Chrome's `/json` endpoint needs a `localhost` Host header (`http://localhost:9333`, not `127.0.0.1`); drive the checkbox via a DOM `.click()`. Compare non-black image-canvas pixel counts (checked vs unchecked) and confirm the label-canvas pixel count is unchanged across the toggle.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/src/types/state.ts` | **Modify.** Add `hideImage?: boolean` to `ViewerConfigType`. |
| `app/src/components/image_canvas.tsx` | **Modify.** Skip `drawImageOnCanvas` when `hideImage` is set. |
| `app/src/components/toolbar_category.tsx` | **Modify.** Add the "Show image" checkbox + props. |
| `app/src/components/toolbar.tsx` | **Modify.** Wire `showImage` / `onToggleImage`. |

---

## Task 1: Hide the image layer on a config flag

**Files:**
- Modify: `app/src/types/state.ts`
- Modify: `app/src/components/image_canvas.tsx`

**Interfaces:**
- Produces: `ViewerConfigType.hideImage?: boolean` — read by the image canvas (and, in Task 2, by the toolbar).

- [ ] **Step 1: Add the config field**

In `app/src/types/state.ts`, inside `ViewerConfigType`, after the `showCurvesOnly?: boolean` field (currently line 213), add:

```ts
  /** whether to hide the underlying image (labels stay visible) */
  hideImage?: boolean
```

- [ ] **Step 2: Branch the image redraw on the flag**

In `app/src/components/image_canvas.tsx`, replace the `redraw` body's loaded/else branch (currently lines 147–159):

```ts
      if (
        isFrameLoaded(this.state, item, sensor) &&
        item < Session.images.length &&
        sensor in Session.images[item]
      ) {
        // Always redraw: the inline ref callback recreates canvas on every render
        // (HTML canvas is cleared whenever canvas.width is assigned, even with the
        // same value). ImageBitmap cache keeps this fast.
        const image = Session.images[item][sensor]
        drawImageOnCanvas(this.imageCanvas, this.imageContext, image, item, sensor)
      } else {
        clearCanvas(this.imageCanvas, this.imageContext)
      }
```

with:

```ts
      const config = this.state.user.viewerConfigs[this.props.id]
      if (
        config.hideImage !== true &&
        isFrameLoaded(this.state, item, sensor) &&
        item < Session.images.length &&
        sensor in Session.images[item]
      ) {
        // Always redraw: the inline ref callback recreates canvas on every render
        // (HTML canvas is cleared whenever canvas.width is assigned, even with the
        // same value). ImageBitmap cache keeps this fast.
        const image = Session.images[item][sensor]
        drawImageOnCanvas(this.imageCanvas, this.imageContext, image, item, sensor)
      } else {
        // Frame not loaded OR the image is toggled off: leave the image layer
        // blank. Labels live on separate canvases and are unaffected.
        clearCanvas(this.imageCanvas, this.imageContext)
      }
```

- [ ] **Step 3: Type-check and lint**

Run: `npx tsc --noEmit`
Expected: no new errors referencing `state.ts` or `image_canvas.tsx`.

Run: `npx eslint -c .eslintrc.json app/src/types/state.ts app/src/components/image_canvas.tsx`
Expected: zero non-`prettier/prettier` violations vs HEAD.

- [ ] **Step 4: Commit**

```bash
git add app/src/types/state.ts app/src/components/image_canvas.tsx
git commit -m "feat: hideImage viewer-config flag hides the image layer"
```

---

## Task 2: "Show image" sidebar checkbox

**Files:**
- Modify: `app/src/components/toolbar_category.tsx`
- Modify: `app/src/components/toolbar.tsx`

**Interfaces:**
- Consumes: Task 1 (`ViewerConfigType.hideImage`).
- Produces: `ToolbarCategory` props `showImage?: boolean` and `onToggleImage?: () => void`.

- [ ] **Step 1: Add the props to `ToolbarCategory`**

In `app/src/components/toolbar_category.tsx`, in the `Props` interface, after the `onToggleCurvesOnly?` field (currently line 159), add:

```ts
  /** whether the underlying image is shown on the canvas */
  showImage?: boolean
  /** toggle the underlying image */
  onToggleImage?: () => void
```

- [ ] **Step 2: Render the checkbox**

In the same file, after the "Curves only" block (currently ends with `)}` at line 339) and **before** the closing `</>` (currently line 340), insert:

```tsx
                  {this.props.onToggleImage !== undefined && (
                    <>
                      <Checkbox
                        size="small"
                        checked={this.props.showImage ?? true}
                        onChange={() => this.props.onToggleImage?.()}
                        title="Toggle the underlying image"
                        style={{ padding: 2, color: "inherit", marginLeft: 8 }}
                      />
                      <span style={{ fontSize: 12, opacity: 0.75 }}>
                        Show image
                      </span>
                    </>
                  )}
```

- [ ] **Step 3: Wire the props from the toolbar**

In `app/src/components/toolbar.tsx`, in the `<ToolbarCategory ... />` element, after the `onToggleCurvesOnly={...}` prop block (currently ends at line 308) and **before** `onToggleCategoryVisibility={...}` (currently line 309), add:

```tsx
            showImage={!(activeConfig.hideImage ?? false)}
            onToggleImage={() => {
              const config = { ...activeConfig }
              config.hideImage = !(config.hideImage ?? false)
              Session.dispatch(
                changeViewerConfig(this.safeActiveViewerId, config)
              )
            }}
```

- [ ] **Step 4: Type-check and lint**

Run: `npx tsc --noEmit`
Expected: no new errors referencing `toolbar_category.tsx` or `toolbar.tsx`.

Run: `npx eslint -c .eslintrc.json app/src/components/toolbar_category.tsx app/src/components/toolbar.tsx`
Expected: zero non-`prettier/prettier` violations vs HEAD.

- [ ] **Step 5: Runtime verification**

Rebuild first (`npm run build`; the server serves the bundle from disk), then use the `verify` skill to drive the label page in headless Chrome. Confirm:
1. A "Show image" checkbox appears in the sidebar display row, checked by default; the image is visible.
2. Uncheck it → the image disappears (image-canvas non-black pixel count drops to ~0) while the lines/annotations remain (label-canvas pixel count unchanged).
3. Re-check it → the image reappears.

- [ ] **Step 6: Commit**

```bash
git add app/src/components/toolbar_category.tsx app/src/components/toolbar.tsx
git commit -m "feat: 'Show image' sidebar checkbox toggles the image layer"
```

---

## Self-Review

**1. Spec coverage** (against `docs/superpowers/specs/2026-07-10-sidebar-show-image-toggle-design.md`):

| Spec item | Task |
|---|---|
| `hideImage?: boolean` on `ViewerConfigType` | Task 1 Step 1 |
| Image canvas skips draw when `hideImage` | Task 1 Step 2 |
| "Show image" checkbox + props | Task 2 Steps 1–2 |
| Toolbar wiring via `changeViewerConfig` | Task 2 Step 3 |
| Default shown; per-view; labels unaffected | Task 1 (default-false; separate canvas) + Task 2 |

**2. Placeholder scan:** No placeholders; every step shows complete code.

**3. Type consistency:** `hideImage` (Task 1) is read in `image_canvas.tsx` (Task 1) and written in `toolbar.tsx` (Task 2). The `showImage` / `onToggleImage` props (Task 2 Step 1) match their use in the checkbox (Step 2) and the values passed from `toolbar.tsx` (Step 3). Consistent.
