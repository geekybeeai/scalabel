# Polyline Preview Review Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the viewport-bounds test and PR-hygiene findings without changing the targeted `Polygon2D.bounds()` fix or the behavior of other annotation tools.

**Architecture:** Retain the current production approach: during `DRAW`, polygon/polyline bounds include the live cursor. Harden the focused tests with name-based tool selection, multi-vertex polygon coverage, a shared redraw helper, and safe global cleanup. Do not add a blanket `label.editing` culling exemption. Remove the committed agent plan from the PR; keep this remediation plan local unless the repository owner explicitly requests it.

**Tech Stack:** TypeScript, Jest, ts-jest, ESLint, webpack

**Spec:** `docs/superpowers/plans/2026-11-12-polyline-preview-offscreen-anchor.md` and the verified review findings for `fix-polyline-preview-offscreen-anchor`

## Global Constraints

- Do not modify drawing coordinates, mouse handling, persistent shapes, or rendering styles.
- Preserve viewport culling for finished and actively edited committed labels.
- Do not change `Label2DList.redraw()` or add `label.editing` to its viewport filter.
- Preserve category/type filters, curves-only mode, control-canvas rendering, and the `viewScale > 2` threshold.
- Select tools by `LabelTypeName`, never fixture positions such as `1` or `3`.
- Restore all globals replaced by the focused test suite.
- Keep this plan untracked/local and remove the previously committed 446-line plan from the effective PR diff.
- Do not commit or push unless the user explicitly requests it.

## Review Focus

- Fixture label-type reordering must not change which tool a test creates.
- A multi-vertex polygon must remain drawable when its live closing edge crosses the viewport.
- The original off-screen-anchor polyline reproduction must remain covered.
- `editing` alone must not disable culling for a committed off-screen label.
- Test-created `window` and `document` properties must not leak.

## Decision Record

The current fix updates `Polygon2D.bounds()` so `_mouseCoord` participates
only while `_state === Polygon2DState.DRAW`. Keep it. It fixes the bounds
contract at the source and is shared by polygons and polylines.

Do not use the broader alternative `label.editing || ...` in viewport
culling. That would also disable culling during move/reshape and could affect
boxes or future tools. Separate preview overlays and exact line clipping are
also unnecessary for this test-remediation task.

## File Map

| Path | Action |
|---|---|
| `app/test/drawable/polygon_viewport_bounds.test.ts` | Correct and extend focused coverage; refactor test-only setup. |
| `app/src/drawable/2d/polygon2d.ts` | Verify only; retain the existing live-cursor bounds block. |
| `app/src/drawable/2d/label2d_list.ts` | Verify only; retain existing culling behavior. |
| `docs/superpowers/plans/2026-11-12-polyline-preview-offscreen-anchor.md` | Delete from the effective PR. |
| This remediation plan | Keep local/untracked; do not stage. |

---

### Task 1: Correct tool selection and clean up the test harness

**Files:**

- Modify: `app/test/drawable/polygon_viewport_bounds.test.ts`

**Interfaces:**

- Produce: `labelTypeIndex(type: LabelTypeName): number`.
- Produce: `POLYGON_LABEL_TYPE` and `POLYLINE_LABEL_TYPE`.
- Produce: `redrawAt(viewScale: number, viewportBounds: [number, number, number, number]): void`.

- [ ] **Step 1: Expose the mislabeled test with a failing assertion**

Import `LabelTypeName`. In
`"polygon bounds stay unknown until the first vertex is committed"`, capture
the selected drawable and add:

```typescript
expect(active.type).toBe(LabelTypeName.POLYGON_2D)
expect(active.bounds()).toBeNull()
```

Leave `labelType: 3` temporarily and run only this test. Expected RED:
received type is `polyline2d`.

- [ ] **Step 2: Replace numeric positions with checked name lookup**

Implement `labelTypeIndex` against
`testJson.task.config.labelTypes`. It must throw a descriptive error if a
requested tool is absent; it must never return `-1` silently. Define the two
named constants and replace every numeric `labelType` in this file.

Run:

```powershell
rg -n "labelType:\s*[0-9]+" app/test/drawable/polygon_viewport_bounds.test.ts
```

Expected: no matches. Rerun the corrected polygon test; expected PASS.

- [ ] **Step 3: Extract the repeated redraw call**

Implement `redrawAt` with the exact existing shared `redraw(...)` arguments,
including `drawControl = false`. Replace all three repeated calls. Do not
generalize unrelated parameters.

- [ ] **Step 4: Restore global property descriptors**

Before defining test `window` and `document`, save each original
`PropertyDescriptor | undefined`. In `afterAll`, restore an existing
descriptor or delete the test-created property when it was originally absent.
Keep `afterEach(jest.restoreAllMocks)` for per-test spies.

- [ ] **Step 5: Run the focused file**

```powershell
npx jest app/test/drawable/polygon_viewport_bounds.test.ts --runInBand --detectOpenHandles --config '{"transform":{"^.+\\.tsx?$":"ts-jest"},"globals":{"ts-jest":{"tsconfig":"tsconfig.test.json"}},"testEnvironment":"node"}'
```

Expected: all existing tests pass with no open-handle warning.

Checkpoint: keep changes local; do not commit or push.

---

### Task 2: Add missing polygon and culling regression coverage

**Files:**

- Modify: `app/test/drawable/polygon_viewport_bounds.test.ts`
- Verify only: `app/src/drawable/2d/polygon2d.ts:363-388`
- Verify only: `app/src/drawable/2d/label2d_list.ts:296-317`

**Interfaces:**

- Consume: the Task 1 tool constants, mouse helpers, and `redrawAt`.
- Preserve: `Polygon2D.bounds(): [number, number, number, number] | null`.

- [ ] **Step 1: Add a multi-vertex closing-edge test**

Add a test named:

```text
high-zoom culling draws a multi-vertex polygon whose closing preview edge crosses the viewport
```

Use the real polygon handler and this geometry:

- committed first point: `(100, 500)`;
- committed second point: `(100, 100)`;
- committed third point: `(200, 900)`;
- live cursor: `(900, 500)`;
- viewport: `[450, 450, 100, 100]` at scale `3`.

The committed bounds remain left of the viewport. The last-to-cursor preview
passes below it, while the closing cursor-to-first edge crosses it. Assert the
active drawable is `polygon2d`, drawing remains in progress, and `draw()` is
called exactly once. Spy on `draw()` only; do not mock `bounds()`.

- [ ] **Step 2: Prove the new test detects regression**

Temporarily remove only the `DRAW` block that includes `_mouseCoord` in
`Polygon2D.bounds()`. Run the new test and confirm RED because `draw()` is not
called. Restore the block exactly, rerun, and confirm PASS. Do not retain the
mutation.

- [ ] **Step 3: Guard the rejected general culling alternative**

Add:

```text
editing alone does not bypass high-zoom culling for a committed off-screen polyline
```

Finish an off-screen polyline, set `finished.editing = true`, call
`redrawAt(3, [800, 800, 100, 100])`, and assert `draw()` is not called. Reset
`editing` in `finally`. This locks the decision not to add an editing exemption.

- [ ] **Step 4: Run the complete focused suite**

Use Task 1's focused Jest command. Retain all existing direction, live
polyline, finished-label, and scale-threshold cases. Expected: all pass.

Checkpoint: keep changes local; do not commit or push.

---

### Task 3: Remove agent execution material from the effective PR

**Files:**

- Delete: `docs/superpowers/plans/2026-11-12-polyline-preview-offscreen-anchor.md`
- Keep local: this remediation plan

- [ ] **Step 1: Delete the committed original plan with `apply_patch`**

The deletion cancels that file's addition relative to `dev`. Do not add this
remediation plan to the index.

- [ ] **Step 2: Inspect the effective branch diff**

```powershell
git diff --stat dev
git diff --name-status dev
git status --short
```

Expected effective PR files: `polygon2d.ts` and
`polygon_viewport_bounds.test.ts`. The original plan must be absent. This
remediation plan may appear only as untracked (`??`).

Checkpoint: keep changes local; do not commit or push.

---

### Task 4: Verify no other feature changed

**Files:**

- Verify all intended changes

- [ ] **Step 1: Run automated verification**

```powershell
# Focused regression suite
npx jest app/test/drawable/polygon_viewport_bounds.test.ts --runInBand --detectOpenHandles --config '{"transform":{"^.+\\.tsx?$":"ts-jest"},"globals":{"ts-jest":{"tsconfig":"tsconfig.test.json"}},"testEnvironment":"node"}'

# Type checking
npx tsc --noEmit -p tsconfig.json
npx tsc --noEmit -p tsconfig.test.json

# Scoped lint
npx eslint -c .eslintrc.json --ext .ts --quiet app/test/drawable/polygon_viewport_bounds.test.ts app/src/drawable/2d/polygon2d.ts

# Diff and production build
git diff --check
npm run build
```

Expected: every command exits `0`. If Windows CRLF produces only Prettier
noise, rerun the same scoped lint with
`--rule "prettier/prettier: off"` and report the limitation; do not suppress
other rules.

- [ ] **Step 2: Attempt the repository-standard suite**

```powershell
npm test -- --runInBand
```

Expected in a provisioned environment: PASS. If setup stops with
`spawn redis-server ENOENT`, report that environment limitation separately;
do not claim the full suite passed.

- [ ] **Step 3: Audit the production diff**

```powershell
git diff dev -- app/src/drawable/2d/polygon2d.ts app/src/drawable/2d/label2d_list.ts
```

Confirm that only the existing `Polygon2D.bounds()` live-cursor block differs
from `dev`, and `Label2DList.redraw()` is unchanged.

- [ ] **Step 4: Run the UI smoke matrix**

1. At zoom above `2x`, start a polyline and pan its first point off-screen;
   confirm the cursor-following segment remains visible.
2. Start a polygon with three committed points and arrange the viewport so
   only the live closing edge crosses it; confirm the preview remains visible.
3. Finish both shapes; confirm off-screen labels are culled at high zoom and
   reappear when panning back.
4. Move and reshape an existing polygon; confirm interaction is unchanged.
5. Toggle type visibility, category visibility, and curves-only mode; confirm
   existing behavior is unchanged.
6. Draw and edit a box; confirm this remediation did not affect other tools.

- [ ] **Step 5: Final local-state check**

Run `git status --short`. Confirm only the intended test cleanup, old-plan
deletion, and the untracked remediation plan are present. Confirm no commit or
push occurred.

## Self-Review

- Every verified review finding maps to a task.
- The corrected test asserts it created a polygon.
- The multi-vertex geometry isolates a closing edge crossing the viewport.
- A mutation run proves the new coverage fails without the production fix.
- No numeric fixture positions or duplicated redraw calls remain.
- Both globals are restored.
- The rejected editing exemption is guarded by a regression test.
- The original agent plan is absent from the effective PR.
- Automated and UI checks cover polygon, polyline, visibility filters, and an unrelated box tool.
