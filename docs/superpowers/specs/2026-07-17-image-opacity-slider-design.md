# Sidebar "Image opacity" Slider — Design

## Problem

The sidebar "Show image" checkbox (2026-07-10) is binary: unchecking it blanks
the image layer entirely. Annotators want to *dim* the image gradually so faint
lines stay readable against a partially visible image, not just full-on /
full-off.

## Decision

Keep the "Show image" checkbox as the quick on/off. Add an **"Image opacity"**
slider (0–100 %, default 100 %) rendered directly below the sidebar toggle
grid. The value is stored per-viewer as `imageOpacity?: number` (0–1) on
`ViewerConfigType`, next to `hideImage`, and is view-only — it never touches
label data or exports.

The slider is disabled while "Show image" is unchecked (`hideImage` still
blanks the layer in `image_canvas.tsx redraw`).

## Rendering approach

Apply the value as CSS `opacity` on the image `<canvas>` element in
`image_canvas.tsx render`. Labels live on separate canvases, so only the image
layer dims toward the display background. This avoids touching the blit path
(`drawImageOnCanvas` / ImageBitmap cache) entirely and is GPU-composited, so
dragging the slider is cheap.

Rejected alternatives:

- `ctx.globalAlpha` in `drawImageOnCanvas` — same visual result but forces a
  re-blit per slider tick and touches the shared draw utility.
- Replacing the checkbox with a slider only (0 % = hidden) — loses the
  one-click toggle and the existing `hideImage` semantics.

## Changes

| File | Change |
| --- | --- |
| `app/src/types/state.ts` | `imageOpacity?: number` on `ViewerConfigType` (0–1, default 1) |
| `app/src/components/image_canvas.tsx` | `style={{ opacity }}` on the image canvas from viewer config |
| `app/src/components/toolbar_category.tsx` | "Image opacity" label + MUI `Slider` row below the toggle grid; props `imageOpacity?`, `onImageOpacityChange?` |
| `app/src/components/toolbar.tsx` | wire props; dispatch `changeViewerConfig` with the new value |
| `docs/polyline-feature-map.md` | note the flag/flow in §7 |

`Slider` comes from `@material-ui/core` (v4), already used in
`player_control.tsx`.

## Error handling / edge cases

- Missing `imageOpacity` (old sessions, sync'd states) → `?? 1`, identical to
  today's behaviour.
- Slider at 0 % with "Show image" checked → image invisible but checkbox state
  unchanged; re-dragging restores it. This is intentional (opacity and
  visibility are independent axes).

## Testing

Type-check + lint, then runtime verification via the `verify` skill: drag the
slider, assert image-layer pixels dim while label pixels are unchanged;
uncheck "Show image", assert the slider is disabled and the layer blank.
