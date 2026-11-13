# Auto-correct Category-aware Joining Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge eligible same-category polylines while snapping eligible different-category endpoints together without absorbing either label or losing either category.

**Architecture:** Keep same-category merging as the first auto-connect phase, relaxing only the straight-endpoint gap-direction rejection while retaining distance, angle, and curve-seam guards. Run a second deterministic cross-category snap phase over surviving labels, anchoring the earlier label and moving only the later endpoint; expose snaps separately from merges through the correction report.

**Tech Stack:** TypeScript, Jest, ts-jest

**Spec:** `docs/superpowers/specs/2026-11-13-autocorrect-category-aware-joining-design.md`

## Global Constraints

- Apply the behavior only to project-creation batch auto-correct.
- Preserve manual editor snapping and merging unchanged.
- Keep the configured 40-image-pixel tolerance and 150-degree minimum angle.
- Preserve curve seam/tangent safeguards.
- Keep both labels, IDs, categories, point types, and non-endpoint geometry for cross-category snaps.
- Use original label order as the deterministic anchor rule.
- Keep all changes local and uncommitted; do not push.

## Review Focus

- The verified 10.728-pixel, 180-degree offset same-category pair must merge despite its sideways gap.
- Perpendicular same-category lines must remain separate under the 150-degree guard.
- Cross-category snapping must move only the later endpoint and must not reduce the label count.
- Already-coincident cross-category endpoints must remain unchanged and must not generate repeated snap operations.
- A label endpoint must participate in at most one correction, with shortest gap and stable original-order tie-breaking.

---

### Task 1: Implement two-phase merge and snap behavior

**Files:**
- Modify: `app/test/server/annotation_fix/autoconnect.test.ts`
- Modify: `app/src/server/annotation_fix/autoconnect.ts`

**Interfaces:**
- Produces: `Snap`, `ConnectResult.snaps`, and `snapToDict(snap: Snap): { [key: string]: unknown }`.
- Produces: `connectLabels(labels, tolerance?, minAngle?)` returning merged survivors with eligible different-category endpoints snapped in place.

- [ ] **Step 1: Write failing engine regression tests**

Add literal fixtures and assertions for the verified 10.728-pixel aligned same-category merge; perpendicular same-category rejection; different-category anchoring with two labels/categories retained and only the later endpoint changed; coincident endpoints producing no snap; and shortest-gap endpoint consumption. Assert the separate `connections` and `snaps` collections.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `npx jest app/test/server/annotation_fix/autoconnect.test.ts --runInBand`

Expected: FAIL because the aligned offset pair is rejected and different-category endpoints are not snapped or reported.

- [ ] **Step 3: Implement the engine behavior**

Add `Snap` with `anchorId`, `movedId`, `anchorCategory`, `movedCategory`, `junction`, `gap`, and optional `angle`. Keep the existing same-category merge phase, but for ordinary straight endpoints require tolerance plus minimum junction angle without `gapFollowsTangents`; retain the curve-adjacent seam checks. Then scan surviving cross-category endpoints shortest-gap first, snap the later endpoint to the earlier endpoint, and consume both endpoint keys once. Do not report a zero-gap no-op.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `npx jest app/test/server/annotation_fix/autoconnect.test.ts --runInBand`

Expected: PASS with all auto-connect tests green.

### Task 2: Propagate snap reporting through auto-correct

**Files:**
- Modify: `app/test/server/annotation_fix/core.test.ts`
- Modify: `app/test/server/annotation_fix/stdio.test.ts`
- Modify: `app/src/server/annotation_fix/core.ts`
- Modify: `app/src/server/annotation_fix/stdio.ts`
- Modify: `app/src/server/annotation_fix.ts`

**Interfaces:**
- Consumes: `ConnectResult.snaps` and `snapToDict()` from Task 1.
- Produces: `FrameReport.snapped`, `FrameReport.snaps`, `ReportDict.summary.totalSnapped`, and `AnnotationFixSummary.totalSnapped`.

- [ ] **Step 1: Write failing report and stdio integration tests**

Update literal expected report shapes to include zero snap counts. Add a cross-category stdio fixture with a nonzero eligible gap and assert two labels remain, the later endpoint equals the earlier endpoint, both categories survive, `totalMerged` is zero, `totalSnapped` is one, and the frame contains one serialized snap.

- [ ] **Step 2: Run reporting tests and verify RED**

Run: `npx jest app/test/server/annotation_fix/core.test.ts app/test/server/annotation_fix/stdio.test.ts --runInBand`

Expected: FAIL because snap report fields and serialization do not exist.

- [ ] **Step 3: Implement reporting and repair preflight**

Populate per-frame snap data from `connectLabels`, aggregate `totalSnapped`, add the count to the project-creation log, and update the worker preflight so its merge probe uses matching categories and it separately verifies a cross-category snap without absorption.

- [ ] **Step 4: Run focused and broader verification**

Run the two reporting tests, all `app/test/server/annotation_fix` tests, changed-file ESLint, and `npm run build`.

Expected: all scoped tests and lint pass; the production build succeeds. Run the repository-wide Jest suite and report unrelated baseline/environment failures explicitly.

- [ ] **Step 5: Verify scope and leave changes uncommitted**

Run `git diff --name-only`; confirm no file under `app/src/drawable/` changed. Do not commit or push.
