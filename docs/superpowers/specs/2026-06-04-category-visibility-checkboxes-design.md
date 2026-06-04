# Per-Category Visibility Checkboxes — Design

Date: 2026-06-04
Status: Approved

## Goal

In the annotation ToolBar, let the user filter which labels render on the 2D
canvas by category. Each category gets a checkbox; only labels in checked
categories are drawn. A "Show All" master checkbox controls every category at
once and is checked by default.

## Behavior

- A new **Category** group appears in the ToolBar's Visibility section, below the
  existing per-label-type toggles.
- A **"Show All"** master checkbox sits at the top of the group, **checked by
  default** — on open, every category is visible.
- Below it, one checkbox per entry in `config.categories`.
- A category whose box is **checked = visible**. "Show All" checked ⇔ no
  categories hidden (`hiddenCategories` empty).
- Unchecking "Show All" unchecks all individual categories (only selected labels
  remain drawn); the user then re-checks the categories they want.
- Toggling individual boxes keeps "Show All" in sync: it shows checked only when
  every category is checked.
- **Selected labels are always drawn**, regardless of category filter — matches
  the existing per-label-type rule so a selection can't be lost.

## State

Mirror the existing `hiddenLabelTypes` field.

- Add `hiddenCategories?: number[]` (category indices) to `ViewerConfigType` in
  `app/src/types/state.ts`. Empty or undefined means all categories visible.

## Wiring

Reuse the existing `changeViewerConfig` flow — no new action or reducer.

1. **ToolBar** (`app/src/components/toolbar.tsx`): render the "Show All" + per
   category checkboxes inside the existing Visibility block. Toggling dispatches
   `changeViewerConfig(activeViewerId, { ...activeConfig, hiddenCategories })`,
   exactly how `hiddenLabelTypes`/`hideTags` already work.
   - Individual toggle: add/remove that category's index from `hiddenCategories`.
   - "Show All" toggle: set `hiddenCategories` to `[]` (show all) or to every
     category index (hide all).
2. **Canvas** (`app/src/components/label2d_canvas.tsx`): extract
   `hiddenCategories` from config (default `[]`) and pass it into `redraw`
   alongside `hiddenLabelTypes`.
3. **Drawable list** (`app/src/drawable/2d/label2d_list.ts`): add a parallel
   filter in `redraw` after the `hiddenLabelTypes` filter:
   `label.selected || !hiddenCategories.includes(label.category[0])`.
   The drawable `Label2D` already exposes a `category` getter returning the
   index array.

## Scope / YAGNI

- Uses the flat `config.categories` list and `label.category[0]` (primary
  index) — consistent with how label color and tags already resolve a category.
- Tree categories are not specially handled; leaf categories appear as flat
  entries, same as elsewhere in the UI.
- 2D canvas only, mirroring the existing label-type toggles.

## Testing

- Unit: extend the `label2d_list` redraw test to assert labels with a hidden
  category index are excluded, and that a selected label in a hidden category is
  still drawn.
- Manual: open a project with ≥2 categories, confirm "Show All" default shows
  everything, unchecking it hides all, re-checking individual categories shows
  only those.
