# Self-Close Merge Curve Corruption Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Closing a polyline into a polygon (endpoint snap onto its own other endpoint) never corrupts curved segments — the ring is normalized to start with a LINE anchor before rebuilding.

**Architecture:** One guarded rotation loop inserted in `Polygon2D.mergeWith`'s self-closing branch (the same normalization `deleteVertex` already uses), plus a feature-map gotcha documenting the ring invariant. No other code paths change. Spec (read it first): `docs/superpowers/specs/2026-07-16-selfclose-curve-corruption-fix-design.md`.

**Tech Stack:** TypeScript React app under `app/`; verification via `npx tsc --noEmit`, eslint-vs-baseline, and the repo `verify` skill (headless Chrome CDP).

## Global Constraints

- **Only the self-closing branch changes.** Do NOT rotate the non-self merge results (cases 1-4) — they are OPEN polylines; rotation would reorder the path. Do not touch `getVertices`, `updateShapes`, `deleteVertex`, snap detection, or the draw path builder.
- **No new drawable jest tests:** `app/test/drawable/` suites cannot load here (native `canvas` + redis). Verification is tsc + lint + runtime CDP.
- **Lint baseline caveat:** compare only a changed file's **non-prettier** rule violations against HEAD (pervasive pre-existing CRLF `prettier/prettier` noise on Windows checkouts).
- **CDP driver rules** (hard-won): dispatch 1-2 `mousemove`s before every synthetic click (consecutive clicks with no move get eaten by the empty-space defer/replay flow); park the cursor at canvas fraction (0.02, 0.02) before pixel-count assertions; target the visible canvas with `z-index: 1` (label canvas); map scenario coordinates off the image-content bbox scanned from the image canvas (z:0).
- Commit messages end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: Normalize the self-closed ring to start with a LINE anchor

**Files:**
- Modify: `app/src/drawable/2d/polygon2d.ts` (self-closing branch of `mergeWith`, ~lines 249-271)

**Interfaces:**
- Consumes: `PathPointType` from `../../types/state` (already imported); `vertices: PathPoint2D[]` local from `getVertices()`.
- Produces: the invariant Task 3's scenarios verify — a self-closed ring always begins with a `PathPointType.LINE` point before `updateShapes` runs.

- [ ] **Step 1: Insert the rotation into the self-closing branch**

Find this exact block at the top of `mergeWith`:

```ts
    if (targetPolyline === this) {
      // Self-closing
      const vertices = this.getVertices()
      const draggedIndex = this._highlightedHandle - 1
      const isStartA = draggedIndex === 0

      if (isStartA) {
        vertices.shift() // Remove start vertex
      } else {
        vertices.pop() // Remove end vertex
      }

      this._closed = true
      if (this._label !== null) {
        this._label.closed = true
      }

      const shapes = vertices.map((v) => v.shape())
      this.updateShapes(shapes)
      this._labelList.addUpdatedLabel(this)
      return
    }
```

Replace it with:

```ts
    if (targetPolyline === this) {
      // Self-closing
      const vertices = this.getVertices()
      const draggedIndex = this._highlightedHandle - 1
      const isStartA = draggedIndex === 0

      if (isStartA) {
        vertices.shift() // Remove start vertex
      } else {
        vertices.pop() // Remove end vertex
      }

      // The ring must start with a LINE anchor: draw()'s path builder,
      // updateShapes' closing-MID insertion, and curveGroupIndices all
      // assume points[0] is a vertex. getVertices() also returns CURVE
      // control points, so removing the dragged duplicate endpoint above
      // can strand its curve's control points at the array head (outline
      // anchored at a control point, bogus MID inside the group). Rotate
      // until an anchor leads — same normalization as deleteVertex; the
      // stranded pair becomes a legal wrap-around curve group.
      if (vertices.some((v) => v.type === PathPointType.LINE)) {
        while (vertices[0].type !== PathPointType.LINE) {
          const point = vertices.shift()
          if (point !== undefined) {
            vertices.push(point)
          }
        }
      }

      this._closed = true
      if (this._label !== null) {
        this._label.closed = true
      }

      const shapes = vertices.map((v) => v.shape())
      this.updateShapes(shapes)
      this._labelList.addUpdatedLabel(this)
      return
    }
```

- [ ] **Step 2: Type-check**

Run: `npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 3: Lint the changed file vs baseline**

```bash
npx eslint app/src/drawable/2d/polygon2d.ts 2>&1 | grep -v "prettier/prettier" | grep -v "^$"
```

Expected: only the pre-existing non-prettier violations already present on HEAD (5 as of the C-curve fix). No new rules violated.

- [ ] **Step 4: Commit**

```bash
git add app/src/drawable/2d/polygon2d.ts
git commit -m "fix: self-closing a polyline no longer corrupts curved segments

mergeWith's self-close branch dedupes the coincident endpoints with a
blind shift()/pop() on getVertices(), which also contains CURVE control
points. Removing the dragged start anchor stranded its curve's control
points at the ring head, so draw() anchored the outline at a control
point and updateShapes planted a MID inside the curve group. Rotate the
ring until a LINE anchor leads (deleteVertex's normalization) — the
stranded pair becomes a legal wrap-around curve group.
Spec: docs/superpowers/specs/2026-07-16-selfclose-curve-corruption-fix-design.md

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Document the ring invariant in the feature map

**Files:**
- Modify: `docs/polyline-feature-map.md` (gotchas section at the end)

**Interfaces:**
- Consumes: the behavior implemented in Task 1 (describe it, don't invent).
- Produces: nothing code-visible.

- [ ] **Step 1: Append the gotcha**

Add this bullet at the end of the "Gotchas that will save the next agent hours" section (after the "C never straightens a curve" bullet):

```markdown
- **Closed rings must START with a LINE anchor.** `draw()`'s path builder
  (`moveTo(points[0])`), `updateShapes`' closing-MID insertion, and
  `curveGroupIndices` all assume `_points[0]` is a vertex. `getVertices()`
  returns anchors AND curve control points (it only filters MIDs), so any
  code that removes an anchor from a ring can strand control points at the
  head. Both `deleteVertex` and `mergeWith`'s self-close branch restore the
  invariant by rotating (`while points[0] !== LINE: shift→push`) — do the
  same in any new ring-editing code. Symptom if violated: the outline passes
  through cyan control points and a stray pale MID handle appears inside the
  curve group.
```

- [ ] **Step 2: Commit**

```bash
git add docs/polyline-feature-map.md
git commit -m "docs: ring-starts-with-LINE-anchor gotcha in feature map

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Runtime verification (acceptance gate)

**Files:**
- None modified. Rebuild (`npm run build`) before driving — the server serves `app/dist` from disk. If a scenario fails, fix via Task 1's file and re-run; do not patch ad hoc here.

**Interfaces:**
- Consumes: the built app with Tasks 1-2 applied; the repro geometry from the spec.
- Produces: pass/fail evidence per scenario, reported to the user.

- [ ] **Step 1: Rebuild and confirm the dev server** (`npm run build`, ~2 min; server on 8686 picks up the new dist without restart). Launch headless Chrome with `--user-data-dir=%TEMP%\chrome-cdp-verify` on CDP port 9333.

- [ ] **Step 2: Scenario 1 — the bug:** on `test2`, draw a 3-vertex polyline V0-V1-V2 in a clear region; C+click the FIRST segment's MID (two cyan points appear); drag V0 (START) onto V2 (END) so it snap-closes.
Expected: cyan count in the shape's region unchanged (±small); **no pale MID-handle pixels in a small box at midpoint(V2, control-point-1)** — the corruption signature; screenshot shows the lens/curve intact, mirroring the drag-END control case.

- [ ] **Step 3: Scenario 2 — control (must keep working):** same shape elsewhere; drag V2 (END) onto V0 (START).
Expected: same correct lens rendering as before the fix.

- [ ] **Step 4: Scenario 3 — curve at the far end:** curve the LAST segment instead; drag V0 (START) onto V2 (END).
Expected: closes cleanly (ring head is already a LINE anchor; rotation no-ops).

- [ ] **Step 5: Scenario 4 — two-line merge regression:** draw polyline A and polyline B endpoint-adjacent; curve the segment of A nearest the seam; drag A's end onto B's start (snap-merge into one line).
Expected: single merged polyline, curve intact. Then drag one endpoint of the merged line onto its other endpoint.
Expected: clean closed polygon.

- [ ] **Step 6: Scenario 5 — post-close editing:** on scenario 1's closed ring, click-drag a cyan control point.
Expected: curve adjusts (C-curve fix behavior holds on merged rings).

- [ ] **Step 7: Final sweep**

Run: `npx tsc --noEmit` (exit 0) and the Task 1 Step 3 lint command (no new non-prettier violations). Kill only the `chrome-cdp-verify` Chrome. Report all scenario outcomes with pass/fail; note any labels left in the scratch projects.

---

## Self-Review (completed at write time)

- **Spec coverage:** rotation + guard + placement (Task 1 Step 1, code verbatim from spec); non-self merges untouched (Global Constraints); feature-map gotcha (Task 2); all five spec verification scenarios (Task 3 Steps 2-6); tsc/lint gates (Task 1 Steps 2-3, Task 3 Step 7).
- **Placeholders:** none — the one code step shows the exact before/after block.
- **Type consistency:** `vertices` is `PathPoint2D[]` with `.type: PathPointType` (same accessors `deleteVertex` uses); no new symbols introduced.
