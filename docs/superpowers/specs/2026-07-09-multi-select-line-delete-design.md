# Multi-select lines for batch delete — Design

**Date:** 2026-07-09
**Branch:** feature-show-curves (fork of scalabel 2D annotator)
**Status:** Approved design, pending implementation plan

## Summary

Let the annotator mark multiple line labels (polylines and polygons) with
Ctrl/Cmd+click and delete them all at once with the Delete key. Marked lines are
highlighted in the same green used by the delete-segment preview
(`rgba(0, 230, 0, 0.95)`). The marked set is a dedicated "delete selection",
kept separate from the annotator's normal edit-selection.

## Goals

- Ctrl/Cmd+click directly on a polyline or polygon toggles it in/out of a
  "delete set"; marked labels render green.
- Ctrl+click on empty canvas (or on a box) keeps panning, exactly as today.
- The Delete key removes every label in the delete set in one atomic action,
  then clears the set. Undo restores the lines one at a time, matching the
  annotator's existing multi-label delete.
- Escape clears the set without deleting.

## Non-goals

- Boxes and custom labels are **not** markable (Ctrl+click on them does nothing
  special — it still pans).
- No rubber-band / drag-rectangle selection. Marking is click-by-click only.
- No change to the existing normal single-select / edit behavior (plain click
  still selects one label with edit handles in its normal color).
- No new sidebar/toolbar button — the gesture is the whole UI.

## Decisions (from brainstorming)

| Question | Decision |
| --- | --- |
| Gesture vs. panning | Ctrl+click **on a line** marks it; Ctrl+click/drag on **empty space or a box** pans (unchanged). |
| Selection model | **Separate** "marked-for-deletion" set. Green appears only for Ctrl+clicked lines, never for a plain single-select. |
| Scope | **Polylines and polygons** (both are the `Polygon2D` drawable). Boxes excluded. |
| Highlight color | `rgba(0, 230, 0, 0.95)` — the exact color of the segment-delete overlay ([label2d_canvas.tsx:805](../../../app/src/components/label2d_canvas.tsx#L805)). |
| Rendering approach | **A** — `Polygon2D.draw()` reads the marked set live (`isMarked`) and paints the stroke green, reusing the real path/curve geometry. No cached drawable flag or sync step needed. |

## Architecture

Source of truth is a new transient (non-Redux) module, mirroring the existing
`cut_state.ts` and `segment_delete_state.ts` tool-state modules.

### New module: `app/src/common/multi_delete_state.ts`

Holds a `Set<IdType>` of marked label ids for the current item, plus a change
listener so views can redraw.

Public API (final names may shift during planning):

- `toggleMarked(labelId: IdType): void` — add if absent, remove if present.
- `isMarked(labelId: IdType): boolean`
- `getMarked(): IdType[]` — snapshot of the current set.
- `markedCount(): number`
- `clearMarked(): void`
- `onMarkedChange(cb): () => void` — subscribe; returns an unsubscribe fn (used
  to trigger a canvas redraw, same pattern as `onCutModeChange`).

The set holds ids for **one item at a time**. It is cleared on item/frame
navigation (see "Lifecycle") so ids can never leak across items.

### Data flow

```
Ctrl+click on a markable line (non-tracking task)
   -> multi_delete_state.toggleMarked(labelId)
   -> onMarkedChange fires -> canvas.redraw()
   -> Polygon2D.draw() reads isMarked(labelId) live -> paints stroke green

Delete key
   -> toolbar.deletePressed(): commitMarkedDelete()
        if markedCount() > 0:
          -> for each marked polyline/polygon: drawHistory.recordDeletedLine(snapshot)  // undo
          -> dispatch deleteLabels([itemIndex], [markedIds])
          -> clearMarked() ; return "deleted"
        else: "ignored" -> fall through to existing deleteSelectedLabels path (unchanged)

Escape          -> clearMarked()
Item navigation -> clearMarked()  (alongside the existing cut / segment-delete disarm)
```

The batch delete lives in a standalone `commitMarkedDelete()` function (mirrors
`commitPendingSegmentDelete`) so it is unit-testable without the DOM.

## Hook points (exact locations)

1. **Marking gesture** — [label2d_canvas.tsx:487](../../../app/src/components/label2d_canvas.tsx#L487),
   the `if (e.ctrlKey || e.metaKey) return` guard. Before returning, hit-test the
   click (the canvas already computes `[labelIndex, handleIndex]` via
   `fetchHandleId` just above at line 474). If `labelIndex >= 0`, the task is not
   a tracking task, and the hit drawable's `.type` is `POLYLINE_2D` or
   `POLYGON_2D`, call `toggleMarked(drawable.labelId)` and `return`. Otherwise
   fall through to the existing `return` — panning is untouched. Placing this at
   the top guard means marking takes priority over the cut / segment-delete
   branches (which only handle non-Ctrl clicks).

2. **Redraw subscription** — `componentDidMount`
   ([label2d_canvas.tsx:179-193](../../../app/src/components/label2d_canvas.tsx#L179-L193)):
   `this._offMarkedChange = onMarkedChange(() => this.redraw())`, torn down in
   `componentWillUnmount` (mirrors `_offSegmentDelete`). Toggling / clearing marks
   thus repaints, and `Polygon2D.draw()` reads the fresh membership.

3. **Green render** — [polygon2d.ts:409-410](../../../app/src/drawable/2d/polygon2d.ts#L409-L410),
   immediately after `context.strokeStyle` / `context.lineWidth` are set for the
   line, inside the `save()`/`restore()` that wraps only the stroke. When
   `mode === DrawMode.VIEW && isMarked(this._labelId)`, override `strokeStyle` to
   the green and bump `lineWidth`. VIEW-only is essential — overriding the CONTROL
   canvas color would corrupt hit-testing. All existing path + curve drawing below
   is reused, so curved segments highlight correctly.

4. **Batch delete** — `commitMarkedDelete()` in a new module
   `app/src/drawable/2d/multi_delete.ts`, called first from
   `toolbar.deletePressed()`
   ([toolbar.tsx:585](../../../app/src/components/toolbar.tsx#L585)). It snapshots
   each marked polyline/polygon with the existing public
   `drawHistory.recordDeletedLine(itemIndex, labelId, {label, shapes})`
   ([draw_history.ts:145](../../../app/src/common/draw_history.ts#L145)), dispatches
   `deleteLabels([itemIndex], [markedIds])`
   ([action/common.ts:421](../../../app/src/action/common.ts#L421)), clears the
   `Session.label2dList.selectedLabels` array (mirrors segment delete), and
   `clearMarked()`. Returns `"deleted"`, else `"ignored"` when nothing valid is
   marked. `deletePressed()` returns early on `"deleted"`; on `"ignored"` it runs
   today's `deleteSelectedLabels` path unchanged.

5. **Escape / cancel** — [label2d_canvas.tsx:850-855](../../../app/src/components/label2d_canvas.tsx#L850-L855),
   after the cut-tool Escape branch: `if (e.key === Key.ESCAPE && markedCount() > 0) { clearMarked(); return }`.
   `clearMarked()` notifies, so the subscription redraws.

6. **Lifecycle clear** — `label2d_canvas.updateState`
   ([label2d_canvas.tsx:901-908](../../../app/src/components/label2d_canvas.tsx#L901-L908)),
   inside the item-navigation block that disarms cut and segment-delete: also
   `clearMarked()` so marks never cross items.

## Undo

Reuses the existing `"deleted"` command kind in `draw_history`. Each marked line
is snapshotted (deep-cloned label + shapes) via the already-public
`recordDeletedLine` **before** `deleteLabels` dispatches. `drawHistory.undo()`
restores deleted lines one command at a time, identical to how the segment-delete
whole-line delete already undoes. No new command kind is needed.

## Interaction with existing tools

- The delete set is a passive selection, not an armed "mode", so it coexists with
  cut / segment-delete without a mutual-exclusion handshake. Those tools only
  intercept **plain** clicks; Ctrl+click is handled above them.
- On item/frame navigation the set is cleared (Lifecycle clear above), the same
  moment cut and segment-delete are disarmed.
- Tracking tasks: batch-marked delete follows the non-tracking path only. If the
  task is a tracking task, `deletePressed()` keeps its existing tracking behavior
  and the marked-set branch is skipped (marking is a no-op there). This keeps the
  first version scoped; tracking support can be a follow-up.

## Edge cases

- **Ctrl+click a line already marked** → unmark (toggle off), returns to normal
  color.
- **Ctrl+click near but not on any line** → no hit, falls through to pan.
- **Delete with an empty set** → unchanged: normal `deleteSelectedLabels`.
- **A marked line is deleted by another path** (e.g. undo/redo, segment-delete):
  membership is by id; a stale id in the set simply matches nothing on the next
  delete and is harmless. `updateState` reconciliation naturally drops it from the
  rendered highlight because the drawable is gone.
- **Frozen / non-keyframe item**: marking is gated by the same `checkFreeze` /
  keyframe guards already at the top of `onMouseDown`.

## Testing

- **Unit** — `app/test/common/multi_delete_state.test.ts`: toggle adds/removes,
  `isMarked`, `getMarked` snapshot, `clearMarked`, listener fires on change.
  (Mirrors `app/test/common/segment_delete_state.test.ts`.)
- **Runtime (verify skill)** — drive the label page over CDP: draw two polylines,
  Ctrl+click both (assert both render green), Delete (assert both gone), Ctrl+Z
  (assert both restored). Confirm Ctrl+drag on empty canvas still pans and a plain
  click still single-selects in normal color.

## Files touched

| File | Change |
| --- | --- |
| `app/src/common/multi_delete_state.ts` | **New** transient marked-set module (`toggleMarked` / `isMarked` / `getMarked` / `markedCount` / `clearMarked` / `onMarkedChange`). |
| `app/src/drawable/2d/multi_delete.ts` | **New** `commitMarkedDelete()` — snapshots + batch-deletes the marked lines. |
| `app/src/drawable/2d/polygon2d.ts` | Green stroke override in `draw()` (VIEW mode, reads `isMarked` live). |
| `app/src/components/label2d_canvas.tsx` | Ctrl+click marking at the pan guard; `onMarkedChange` redraw subscription; clear on Escape + item nav. |
| `app/src/components/toolbar.tsx` | `deletePressed()` calls `commitMarkedDelete()` first, else falls through. |
| `app/test/common/multi_delete_state.test.ts` | **New** unit tests for the marked-set module. |
| `app/test/drawable/multi_delete.test.ts` | **New** integration test: mark → delete → undo restores. |

## Open questions

None blocking. Deferred by decision: tracking-task support, rubber-band selection.
