# Polyline Cut (Scissor) Tool — Design

**Date:** 2026-07-07
**Status:** Approved (brainstorming complete)

## Purpose

Annotators need to split one polyline into two — e.g. a lane line that was drawn
(or predicted) as a single line but is really two. Today the only way is to
delete and redraw. This feature adds a **scissor cut tool**: arm it, click on a
polyline, and the polyline is divided at that point into two independent
polylines, each getting its own new vertex at the cut point (coincident
coordinates, no gap — exported geometry stays faithful).

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Tool lifetime | **One-shot**: arms, performs a single cut, then deactivates |
| Undo | **Atomic**: one Ctrl+Z removes both halves and restores the original |
| Curved (bezier) segments | **Not cuttable in v1** — toast tells the user to straighten first |
| Closed shapes | **Open polylines only** — polygons and self-closed polylines excluded |
| Activation | Toolbar scissor button **and** right-click context-menu item; both arm the same mode |
| Right-click semantics | The menu item only **arms the tool** (it does not cut at the right-click point — precisely right-clicking a thin line is hard) |
| Architecture | Module-level mode singleton (pattern of `pointer_pan_state.ts`) + state-level split committed via direct redux dispatches (pattern of `pasteLabel` / `drawHistory.setLine`) |

## UX flow

1. **Arm** the tool either way:
   - Toolbar: a scissor `IconButton` right after Undo/Redo in
     `Viewer2D.getMenuComponents()`. No scissors icon exists in the installed
     `@material-ui/icons` v4 package, so use an inline `SvgIcon` with the
     standard material `content_cut` path (no new dependency). Tooltip: "Cut
     polyline". While armed the button is tinted.
   - Right-click **anywhere** on the 2D label canvas opens a small MUI `Menu`
     at the cursor with one item, "✂ Cut polyline"; selecting it arms the tool.
     Escape/click-away closes the menu without arming. The browser context menu
     is already suppressed globally (`window.tsx`).
2. While armed, the canvas cursor is a **scissors glyph** (custom CSS cursor
   from an inline SVG data-URI; browsers have no native scissors cursor).
3. The next **left-click** is consumed by the tool:
   - **Straight segment of an open polyline** → cut happens; tool disarms;
     cursor restores.
   - **Curved segment** → toast "Cannot cut a curved segment — straighten it
     first"; stays armed.
   - **Closed polygon / self-closed polyline** → toast "Cut works on open
     polylines only"; stays armed.
   - **Empty space** → click swallowed (does NOT start drawing); stays armed.
   - **Too close to an endpoint** → no cut, brief toast; stays armed.
4. **Cancel paths**: Escape, clicking the toolbar scissor again, or navigating
   to another item all disarm the tool.
5. Arming is a **no-op while a polyline is mid-draw**
   (`Session.label2dList.isDrawingInProgress()`) and when tracking mode is on
   (this fork annotates lanes without tracking). The context-menu item is
   disabled in the same conditions.
6. **Undo**: one Ctrl+Z / toolbar Undo removes both halves and restores the
   original exactly; Redo re-applies the cut.

## Architecture

### New files

- **`app/src/common/cut_state.ts`** — mode singleton, same shape as
  `pointer_pan_state.ts`: `isCutMode()`, `setCutMode(on)`, plus a tiny
  `onCutModeChange(listener)` subscription so the toolbar button can re-render
  when the mode is cleared from elsewhere (Escape, successful cut). No redux.
- **`app/src/drawable/2d/polyline_cut_geometry.ts`** — **pure** geometry (no
  Session/DOM imports, so it unit-tests with the `--env=node` jest recipe):
  - `findCutSite(points, click, radius, snapRadius)` — returns the nearest
    span's cut site or a rejection (`"curve" | "near-endpoint" | "miss"`),
    each carrying the distance so callers can compare across polylines.
  - `buildCutHalves(points, site)` — the two halves as plain point lists.
- **`app/src/drawable/2d/polyline_cut.ts`** — `performCut(click, radius,
  snapRadius, visibility?)`: **scans every open polyline in the current item**
  from redux, runs `findCutSite` on each, takes the globally nearest site,
  dispatches, records history, returns a result the caller maps to toasts.
  Scanning (vs requiring a control-canvas hit) makes the cut click forgiving
  on thin lines — the same concern that motivated the right-click arming
  path. Closed shapes are scanned too, but only so a click nearest to one
  yields the "open polylines only" toast. The optional `visibility` filter
  (`CutVisibilityFilter`: `hideLabels`, `hiddenLabelTypes`,
  `hiddenCategories`) mirrors `Label2DList.redraw`'s viewer-config
  visibility rules exactly — the scan skips any label the renderer is
  currently hiding, so a user can never cut a polyline they cannot see.
- **`app/src/components/cut_icon.tsx`** — the shared `content_cut` SVG path,
  the `ContentCutIcon` component (toolbar + menu item), and the `CUT_CURSOR`
  CSS value (SVG data-URI scissors cursor).

### Modified files

- **`app/src/components/viewer2d.tsx`** — scissor button appended after
  `getHistoryButtons()`; onClick toggles `cut_state` (with the mid-draw /
  tracking guards) and re-renders for the tint.
- **`app/src/components/label2d_canvas.tsx`** — four hooks:
  1. `onMouseDown` checks cut mode **before the pan/empty-drag arming** and
     consumes the click: `getMousePos` → `performCut` → toast/disarm per
     result. (No `fetchHandleId` dependency — `performCut` scans state.)
  2. Cursor logic shows the scissors cursor while armed.
  3. Escape in `onKeyDown` disarms.
  4. `onContextMenu` + component state (anchor position) render the MUI `Menu`
     with the "✂ Cut polyline" item.
- **`app/src/common/draw_history.ts`** — new command kind `"cut"` (below).

### Data flow

Button / menu item → `cut_state` armed → scissors cursor. Left-click →
`performCut` → one `makeSequential` redux dispatch → store subscribers rebuild
drawables (`Label2DList.updateState`) → repaint → `drawHistory.recordCut` →
undo button undims → `cut_state` disarmed.

No drawable is mutated mid-lifecycle; redux is the single source of truth, the
same as undo/redo today.

## Split semantics

Stored shapes are the real vertices (MID points are never stored — `shapes()`
strips them). A **straight segment** is two adjacent stored vertices both of
type `LINE`; a `CURVE`-typed neighbor marks a bezier span.

`findCutSite` measures the click's distance to **every** span (curved spans
approximated by their control polygon) and takes the global nearest — so a
click that genuinely lands on a curved part yields the curve toast rather than
a surprise cut on a farther straight segment.

Guards, in order:

1. **Sanity bound** — nearest span farther than ~20 *screen* px (converted to
   image px the same way `findNearestEndpoint`'s radius already is) → `miss`.
2. **Vertex snap** — projection within 8 *screen* px (same conversion) of an
   **interior** vertex `j` → cut exactly at that vertex (avoids hair-thin
   sliver segments). Clicking an interior vertex handle directly does the same.
3. **Endpoint guard** — cut point within the same 8-px threshold of the first
   or last vertex → `near-endpoint` (would create a zero-length stub).

With cut point `C` projected onto segment `(i, i+1)`:

- Half **A** = `V[0..i] + C` — keeps the original label id, category,
  attributes, and is marked `manual: true` (the truncated geometry is a
  user edit even if the original label was an untouched prediction).
- Half **B** = `C + V[i+1..end]` — new `uid()` label id, cloned
  category/attributes, `type: POLYLINE_2D`, `closed: false`, `manual: true`,
  fresh shape ids.

In the vertex-snap case (cut at interior vertex `j`) no new coordinate is
introduced *within* a half: **A** = `V[0..j]`, **B** = `V[j..end]` — the two
halves share only the coordinate of `V[j]` (B gets its own copy).

Both halves always have ≥ 2 vertices by construction. All math is in
**original-image coordinates** (the golden rule); zoom/pan/rotation never touch
stored coords — `getMousePos` already returns image-frame coordinates.

## Commit mechanics

Mirrors two proven patterns — `pasteLabel` (direct redux label creation) and
`drawHistory.setLine` (wholesale geometry replacement):

1. Deep-snapshot the original label + shapes (undo target).
2. Half A **reuses the original label id**, replaced wholesale via
   `deleteLabel` + `addLabel` with the same id (exactly how `setLine` replaces
   geometry when the vertex count changes). Fresh shape ids; `label.shapes`
   rewired.
3. Half B added via `addLabel` with its new id.
4. Selection is cleared before the dispatch so no stale selected-drawable
   references survive the rebuild.
5. All actions dispatched as **one `makeSequential(...)`** so the backend
   synchronizer sees a single atomic update; then `drawHistory.recordCut(...)`.

## Undo/redo

New command kind in `draw_history.ts` alongside `created`/`edited`/`deleted`:

```ts
{ kind: "cut", itemIndex, labelId,      // A (original id)
  before: LineSnapshot,                  // A pre-cut
  after: LineSnapshot,                   // A post-cut
  newLabelId: IdType,                    // B
  newLine: LineSnapshot }                // B post-cut
```

- **Undo** = `removeLine(B)` + `setLine(A.before)` — one press.
- **Redo** = `setLine(A.after)` + `setLine(B.newLine)`.
- Because commands reference label ids and `setLine` reuses ids, later edits of
  either half undo independently before the cut itself unwinds — consistent
  with existing history behavior. `canUndo()` needs no change (non-`created`
  kinds always act).

## Edge cases

- 2-point polyline → two 2-point halves; fine.
- Self-closed polyline (`label.closed === true`) excluded along with
  `POLYGON_2D`.
- Overlapping polylines → the one whose segment is **nearest to the click**
  wins (the cut scans all open polylines; it does not use the paint-order
  control-canvas hit-test).
- After a successful one-shot cut, the next click behaves normally (may start
  a draw) — intended.
- Item navigation already resets `drawHistory`; it also disarms cut mode.

## Error handling

`performCut` returns a discriminated result; the canvas maps it to toasts via
the existing `alert(Severity.WARNING, ...)`. All dispatches happen only after
validation passes — no partial state is ever committed.

## Testing

Matching what actually runs in this environment (see
`docs/polyline-feature-map.md` §8):

- **Unit tests** (pure, `--env=node` + noop globalSetup/Teardown):
  `findCutSite` — mid-segment projection, vertex snap, endpoint rejection,
  curve rejection, miss, 2-point line; half-builder — vertex counts, shared cut
  coordinate, id reuse for A / fresh id for B.
- **Draw-history cut command** test following `app/test/drawable/draw_history.test.ts`
  if that suite loads here; otherwise rely on the checks below.
- `npx tsc --noEmit` and `npm run lint` (ignore pre-existing CRLF
  prettier/prettier noise on Windows checkouts).
- **Manual runtime check**: draw two polylines → arm via button → cut → undo →
  redo; arm via right-click menu → cut; verify curve/polygon/endpoint toasts;
  verify export coordinates stay in the original image frame.

## Out of scope (v1)

- Cutting bezier/curved segments (de Casteljau subdivision) — future work.
- Opening closed polygons with the scissor.
- Cutting in tracking mode.
- Multi-cut sticky mode.
