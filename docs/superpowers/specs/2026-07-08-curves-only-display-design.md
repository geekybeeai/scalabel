# Curves-Only Display Toggle — Design

**Date:** 2026-07-08
**Status:** Approved (brainstorming complete)

## Purpose

Annotators need to see at a glance which parts of the annotation data are
stored as bezier curves — including "secretly curved" segments whose control
points lie on the straight chord and therefore *look* straight (a real source
of confusion with the cut/delete tools, which reject curve spans by stored
type). This feature adds a **"Curves only" checkbox** to the category sidebar:
when checked, the canvas renders only the curved stretches of every line;
straight spans and fully-straight lines disappear.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Straight parts while ON | **Hidden completely** (not dimmed) |
| Interactivity while ON | **Fully editable**: everything visible is interactive (select, drag anchors/control points, C-revert, draw new lines). Hidden straight geometry is not a hover target. |
| Approach | **Draw-pass filter**: a viewer-config flag + a curves-only branch in `Polygon2D.draw`, applied identically to the VIEW and CONTROL canvases so visibility ≡ interactivity |
| Cut / delete-segment tools | **Inert while ON**: arming is a no-op (guarded like mid-draw), menu items disabled, and turning the checkbox ON disarms an armed tool — tools never act on invisible geometry |

## UX

1. A third checkbox, **"Curves only"**, in the "Show all / Show Tags" row of
   the category sidebar. Same styling; **unchecked by default**; per-viewer
   display state (never saved into annotation data; off on reload).
2. When ON, each polyline/polygon renders **only its bezier groups** —
   anchor → control → control → anchor strokes in the label's normal color and
   line width. A mixed line shows just its curved stretch(es), possibly
   several disconnected pieces. Fully-straight lines render nothing.
   Straight-looking bezier segments (collinear control points) DO show —
   that is the point of the feature.
3. Vertex handles render only for visible curve groups (the two anchors and
   two control points of each group), on both canvases.
4. **Editing exemption:** the label being actively drawn or reshaped renders
   in full until released (matches the category-checkbox exemption:
   `editing`, not `selected`). A merely-selected label still obeys the
   filter.
5. **Filter composition (AND):** hidden categories / hidden label types /
   hide-labels still apply on top. Show Tags still governs tags; a label
   skipped entirely draws no tag.
6. Reverting a visible curve with **C** makes that stretch disappear
   immediately (it is no longer a curve) — the intended cleanup workflow.
7. While ON: the cut and delete-segment toolbar buttons do not arm
   (guarded), the context-menu items are disabled, and checking the box
   disarms any armed tool (`setCutMode(false)` + `resetSegmentDelete()`).

## Architecture

### Flag

- `ImageViewerConfigType` gains `showCurvesOnly?: boolean`
  (`app/src/types/state.ts`), defaulting to undefined/false.
- Toggled via `changeViewerConfig`, mirroring the existing Show Tags
  handler in `app/src/components/toolbar.tsx`; checkbox props
  (`showCurvesOnly`, `onToggleCurvesOnly`) threaded into
  `app/src/components/toolbar_category.tsx`'s header row.

### Render path

- `app/src/components/label2d_canvas.tsx` `redraw()` reads the flag from the
  viewer config and passes it to `Label2DList.redraw` (new parameter).
- `Label2DList.redraw` (`app/src/drawable/2d/label2d_list.ts`): while ON,
  `labelsToDraw` keeps only drawables that are `Polygon2D` instances
  containing at least one `CURVE`-typed point, or that are `editing`.
  Non-polygon label types are hidden while ON.
- `Polygon2D.draw` (`app/src/drawable/2d/polygon2d.ts`) gains a
  `curvesOnly` parameter. When set (and the label is not `editing`):
  - the path is built as separate bezier-group strokes
    (`moveTo(anchorA)` + `bezierCurveTo(C1, C2, anchorB)` per group)
    instead of one continuous path;
  - the closed-polygon fill is skipped;
  - vertex handles are drawn only for indices belonging to curve groups;
  - the same branch runs for `DrawMode.VIEW` and `DrawMode.CONTROL`, so the
    hit-test canvas exposes exactly the visible geometry.

### Pure helper

- `curveGroupIndices(points)` — returns the index ranges
  `[anchorA, c1, c2, anchorB]` of every bezier group in a point list.
  Lives beside the drawing code but imports only types, so it unit-tests
  with the node-env jest recipe. Used for both path building and handle
  filtering.

### Tool guards

- The cut/delete-segment arming sites (toolbar buttons in `viewer2d.tsx`,
  context-menu items in `label2d_canvas.tsx`) add `showCurvesOnly` to their
  existing mid-draw/tracking guards.
- The "Curves only" toggle handler disarms both tools when switching ON.

## Edge cases

- Selected-but-not-editing labels obey the filter (only `editing` exempts).
- Closed polygons show only their curved arcs; no fill while ON.
- Back-to-back curve groups (shared anchor) render both groups; the shared
  anchor's handle draws once.
- A curve group at the very start or end of a line renders normally.
- Export, save, undo/redo, and stored coordinates are untouched — this is a
  pure view feature (golden rule respected).
- Zoom/pan/rotation compose as with any other drawing (the filter changes
  WHAT is stroked, not HOW coordinates map).

## Testing

- **Unit (node-env recipe):** `curveGroupIndices` — no curves, one group,
  multiple groups, back-to-back groups, group at start/end of the line,
  malformed tails (defensive).
- `npx tsc --noEmit`; eslint on touched files vs baseline (ignore the
  repo's pre-existing CRLF prettier noise).
- **Runtime:** headless-CDP drive via the repo verify skill — seed a mixed
  line (press C on one segment), toggle the checkbox, screenshot ON/OFF;
  verify the cut/delete buttons refuse to arm while ON.
- **Manual QA:** mixed lines show only curved stretches; straight-looking
  bezier segments appear; C-revert removes them live; editing exemption;
  filter composition with category checkboxes; tools disabled.

## Out of scope (v1)

- Dimming (rather than hiding) straight parts.
- A curve-count badge or list in the sidebar.
- Making the cut/delete tools operate in curves-only mode.
