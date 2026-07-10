# Sidebar "Show image" Toggle — Design

- **Date:** 2026-07-10
- **Status:** Approved (design); ready for implementation planning
- **Area:** 2D annotator — category sidebar + image canvas

## 1. Objective

Add a **"Show image"** checkbox to the sidebar. When checked (the default), the
underlying image is drawn as today. When unchecked, the image layer is hidden
until the box is re-checked. Only the image is affected — lines and every other
annotation stay exactly as they are.

## 2. Background (why this is small)

The 2D viewer paints on **separate canvas layers**: the image is drawn by
`app/src/components/image_canvas.tsx` (`redraw()` → `drawImageOnCanvas`), while
labels/tags/overlays are drawn by `label2d_canvas.tsx`. Hiding the image is
therefore a single branch in the image canvas — the label layers are untouched.

View-level display flags already live on the per-viewer config
(`ViewerConfigType`: `hideLabels`, `hideTags`, `hiddenCategories`,
`showCurvesOnly`) and are toggled from `toolbar.tsx` via
`changeViewerConfig(id, {...config, flag})`, surfaced as checkboxes in
`toolbar_category.tsx`. "Show image" follows the exact same pattern, adding one
more flag.

## 3. Decision (locked)

| Decision | Choice | Rationale |
|---|---|---|
| Default | **Image shown** (`hideImage` unset/false) | Preserves current behavior |
| Scope | **Per-viewer config** | Matches Show Tags / Curves only; each 2D view remembers its own setting |
| What it hides | **The image layer only** | Labels/annotations are on separate canvases and are unaffected |
| Storage | New optional `hideImage?: boolean` on `ViewerConfigType` | Consistent with the existing display flags |

## 4. Architecture

### 4.1 Modified: `app/src/types/state.ts`

Add an optional field to `ViewerConfigType`:

```ts
/** whether to hide the underlying image (labels stay visible) */
hideImage?: boolean
```

Optional + default-false means existing saved sessions and other callers are
unaffected.

### 4.2 Modified: `app/src/components/image_canvas.tsx`

In `redraw()`, when the frame is loaded, branch on the flag:

```ts
const config = this.state.user.viewerConfigs[this.props.id]
if (config.hideImage === true) {
  clearCanvas(this.imageCanvas, this.imageContext)
} else {
  drawImageOnCanvas(this.imageCanvas, this.imageContext, image, item, sensor)
}
```

The frame-loaded / bounds guards are unchanged; only the "loaded" branch gains
the `hideImage` check. Toggling the flag triggers a redux update → the viewer
re-renders → `redraw()` clears or paints the image accordingly.

### 4.3 Modified: `app/src/components/toolbar_category.tsx`

Add a fourth control to the display row (beside Show lines / Show Tags /
Curves only):

- New props: `showImage?: boolean`, `onToggleImage?: () => void`.
- A `Checkbox` (`checked={showImage ?? true}`) + `<span>Show image</span>`,
  rendered when `onToggleImage !== undefined`, `title` "Toggle the underlying
  image".

### 4.4 Modified: `app/src/components/toolbar.tsx`

Pass the new props to `ToolbarCategory`, mirroring `onToggleTags`:

```ts
showImage={!(activeConfig.hideImage ?? false)}
onToggleImage={() => {
  const config = { ...activeConfig }
  config.hideImage = !(config.hideImage ?? false)
  Session.dispatch(changeViewerConfig(this.safeActiveViewerId, config))
}}
```

## 5. Data flow

```
checkbox -> onToggleImage
         -> changeViewerConfig(id, {...config, hideImage: !hideImage})
         -> redux update -> ImageCanvas.redraw()
             hideImage ? clearCanvas(imageCanvas) : drawImageOnCanvas(...)
Label canvases: unaffected (separate layers) -> annotations stay visible
```

## 6. Edge cases & guards

- **Frame not loaded:** the existing `clearCanvas` path already runs; the
  `hideImage` branch only changes the loaded case.
- **Default / older sessions:** `hideImage` unset ⇒ image shown (no behavior
  change).
- **Per-view:** two 2D views can independently show/hide their image.
- **Annotations:** because labels/tags/overlays live on other canvases, hiding
  the image cannot hide or alter them.

## 7. Non-goals (YAGNI)

- No global (all-views) image toggle.
- No dimming/opacity control — a binary show/hide only.
- No effect on label visibility, tags, or export.
- No 3D / point-cloud viewer changes.

## 8. Testing

- **Runtime** (headless Chrome / CDP): count non-black image-canvas pixels with
  the box checked (image present) vs unchecked (image cleared), and confirm the
  label-canvas pixel count is unchanged across the toggle (annotations stay).
  (Reuses the freeform verification harness; drive the checkbox via DOM.)
- **Static:** `npx tsc --noEmit`, `npm run lint` (filter pre-existing CRLF
  `prettier/prettier` noise).

## 9. File change summary

| File | Change |
|---|---|
| `app/src/types/state.ts` | add `hideImage?: boolean` to `ViewerConfigType` |
| `app/src/components/image_canvas.tsx` | skip `drawImageOnCanvas` when `hideImage` |
| `app/src/components/toolbar_category.tsx` | add "Show image" checkbox + props |
| `app/src/components/toolbar.tsx` | wire `showImage` / `onToggleImage` |
