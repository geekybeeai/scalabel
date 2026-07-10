# Sidebar "Show all" → "Show lines" Rename — Design

- **Date:** 2026-07-10
- **Status:** Approved (design); ready for implementation planning
- **Area:** 2D annotator — category sidebar

## 1. Objective

Rename the sidebar's **"Show all"** checkbox label to **"Show lines"**. This is a
label-only change; the checkbox's behavior is unchanged.

## 2. Background

`app/src/components/toolbar_category.tsx` renders a row (gated on
`onToggleAllCategoryVisibility !== undefined`) containing three controls:
**Show all** (toggles visibility of every category at once), **Show Tags**, and
**Curves only**. The "Show all" checkbox is bound to
`hiddenCategories.length === 0` (checked when nothing is hidden) with an
`indeterminate` state for partial hiding, and calls
`onToggleAllCategoryVisibility`. In this annotator every category is a line
type, so "Show lines" reads more accurately than "Show all".

## 3. Decision (locked)

| Decision | Choice | Rationale |
|---|---|---|
| Scope of change | **Label text + tooltip only** | Behavior (toggle all category visibility) is correct; only the wording is off |
| New wording | **"Show lines"** | Categories are line types; clearer intent |

## 4. Architecture

Single-file change in `app/src/components/toolbar_category.tsx`:

- The `<span>` currently rendering `Show all` (the label beside the
  all-categories checkbox) changes to `Show lines`.
- The checkbox's `title` tooltip ("Toggle visibility of all categories")
  updates to match (e.g. "Toggle visibility of all lines").

No prop, callback, state, binding, or `indeterminate` logic changes. The
checkbox continues to reflect and toggle `hiddenCategories`.

## 5. Edge cases & guards

- The `indeterminate` (partial-hidden) visual state is preserved unchanged.
- No other component references the literal string "Show all", so there is no
  ripple; if a test asserts on the old text, it is updated to the new label.

## 6. Non-goals (YAGNI)

- No change to what the checkbox toggles.
- No change to the Show Tags / Curves only controls.
- No relayout of the row.

## 7. Testing

- **Static:** `npx tsc --noEmit`, `npm run lint` (filter pre-existing CRLF
  `prettier/prettier` noise).
- **Runtime** (headless Chrome / CDP): confirm the sidebar row now reads
  "Show lines" and that toggling it still shows/hides all lines. (Reuses the
  freeform verification harness.)

## 8. File change summary

| File | Change |
|---|---|
| `app/src/components/toolbar_category.tsx` | rename "Show all" label to "Show lines"; update its tooltip |
