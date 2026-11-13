# Self-Intersection Label Deletion Regression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing self-intersection fix durable by correcting stale polygon expectations and adding behavioral coverage that prevents silent label deletion or track termination.

**Architecture:** Keep the domain rule in `Polygon2D.isValid()` and test through the existing drawable/Redux commit path. Do not broaden `commit2DLabels()` or reintroduce intersection helpers; use mutation verification to prove the regression tests detect the historical rejection.

**Tech Stack:** TypeScript, Jest, Redux-backed Scalabel test utilities, Node.js, Redis test service.

**Spec:** `docs/self-intersection-label-deletion-fix.md`

## Global Constraints

- Preserve the current production rule: closed self-intersections are valid when the shape is otherwise non-degenerate.
- Preserve `_mergedOut`, unfinished-drawable, `MIN_SIZE`, and open-polyline vertex-count validation.
- Do not change generic invalid-label deletion or track-termination behavior in `commit2DLabels()`.
- Do not add dependencies or category-name special cases.
- Temporary mutation code used for red/green proof must not remain in the final diff.

## Review Focus

- A self-touch where a segment passes through another vertex must remain valid; Task 1 exercises it.
- A proper bow-tie crossing between non-adjacent edges must remain valid; Tasks 1 and 2 exercise it.
- An imported existing label must survive a no-op touch/commit; Task 2 exercises it.
- A tracked imported label must preserve both the track and per-frame labels; Task 2 exercises it.
- A sub-`MIN_SIZE` closed shape must still be rejected; Task 1 exercises it.

---

### Task 1: Align polygon validation tests with the domain contract

**Files:**

- Modify: `app/test/drawable/polygon.test.ts:187-266`
- Test: `app/test/drawable/polygon.test.ts`

**Interfaces:**

- Consumes: `drawPolygon()`, mouse helpers, `getState()`, `getShapes()`.
- Produces: executable expectations for drawing, editing, and rejecting degenerate closed polygons.

- [ ] **Step 1: Run the current targeted test and record the stale failure**

Run:

```powershell
npm test -- --runInBand app/test/drawable/polygon.test.ts
```

Expected on the current fixed production code: the old assertion that only one label exists after drawing the self-intersecting polygon fails because the valid shape is retained.

- [ ] **Step 2: Replace the stale mixed test with explicit behavior tests**

Create clear cases asserting:

- drawing a non-degenerate self-intersecting closed polygon adds one label;
- drawing the historical self-touch geometry adds one label;
- reshaping an existing polygon into a crossing commits the moved vertex and preserves the label;
- drawing a closed shape whose bounding box is below `MIN_SIZE` adds no label.

- [ ] **Step 3: Run the polygon test file**

Run:

```powershell
npm test -- --runInBand app/test/drawable/polygon.test.ts
```

Expected: PASS with zero failed tests in the file.

- [ ] **Step 4: Checkpoint the focused change**

Inspect:

```powershell
git diff -- app/test/drawable/polygon.test.ts
```

Expected: only the stale validation expectations and their test descriptions changed.

### Task 2: Protect imported labels and tracks at the commit boundary

**Files:**

- Create: `app/test/drawable/polygon_self_intersection_regression.test.ts`
- Reference: `app/src/drawable/2d/polygon2d.ts:905-940`
- Reference: `app/src/drawable/states.ts:239-328`
- Test: `app/test/drawable/polygon_self_intersection_regression.test.ts`

**Interfaces:**

- Consumes: `setupTestStore()`, `makePolygon2dLabel()`, `Label2DList.updateState()`, `commit2DLabels()`, `updateTracks()`, and the polygon tracking fixture.
- Produces: regression coverage for existing non-tracking and tracking bow-tie labels.

- [ ] **Step 1: Read the test-quality rules before adding tests**

Read `writing-good-tests.md` from the installed test-driven-development skill and ensure each assertion observes real state rather than a mock call.

- [ ] **Step 2: Add an existing-label survival test**

Seed a non-temporary closed polygon with vertices `[[100,100],[300,300],[100,300],[300,100]]`; update a real `Label2DList`; commit its drawable; assert the label id and all shape ids remain in item `0`.

- [ ] **Step 3: Add a tracking survival test**

Use `test_track_polygon2d_objects.ts` to seed the same closed geometry across the fixture's frames in one track; initialize `Session.tracks`; commit the frame-zero drawable; assert the track id remains and every original frame-label mapping resolves to an existing label.

- [ ] **Step 4: Run the focused regression file**

Run:

```powershell
npm test -- --runInBand app/test/drawable/polygon_self_intersection_regression.test.ts
```

Expected: PASS on the intended implementation.

### Task 3: Prove the tests detect the historical rejection

**Files:**

- Temporarily modify, then restore: `app/src/drawable/2d/polygon2d.ts:905-940`
- Test: `app/test/drawable/polygon.test.ts`
- Test: `app/test/drawable/polygon_self_intersection_regression.test.ts`

**Interfaces:**

- Consumes: the tests from Tasks 1 and 2.
- Produces: recorded red/green evidence that the tests fail under the historical behavior and pass under the intended behavior.

- [ ] **Step 1: Temporarily restore a minimal self-intersection rejection**

Use a verification-only mutation in `Polygon2D.isValid()` that returns `false` for the bow-tie fixtures. Do not retain or commit this mutation.

- [ ] **Step 2: Run both focused test files and verify RED**

Run:

```powershell
npm test -- --runInBand app/test/drawable/polygon.test.ts app/test/drawable/polygon_self_intersection_regression.test.ts
```

Expected: failures show that self-intersecting labels are not retained and/or tracked labels are removed. A syntax, setup, or Redis error does not count as RED.

- [ ] **Step 3: Restore the intended `isValid()` implementation**

Remove the temporary rejection and confirm the explanatory comment and `MIN_SIZE` check remain.

- [ ] **Step 4: Run both focused test files and verify GREEN**

Run the same command. Expected: PASS with zero failed tests.

- [ ] **Step 5: Confirm the mutation is absent**

Run:

```powershell
git diff -- app/src/drawable/2d/polygon2d.ts
```

Expected: no diff unless a documentation-only comment improvement was intentionally required.

### Task 4: Record final verification and handoff

**Files:**

- Modify: `docs/self-intersection-label-deletion-fix.md`
- Verify: repository-wide tests, lint, and build

**Interfaces:**

- Consumes: final code and tests from Tasks 1-3.
- Produces: a truthful implementation record and reviewer-ready diff.

- [ ] **Step 1: Run the full Jest suite**

Run:

```powershell
npm test -- --runInBand
```

Expected: zero failed suites and zero failed tests. Record any unrelated or environment-blocked failures by name.

- [ ] **Step 2: Run lint**

Run:

```powershell
npm run lint
```

Expected: exit code `0`; otherwise record exact errors.

- [ ] **Step 3: Run the production build**

Run:

```powershell
npm run build
```

Expected: exit code `0`; otherwise record exact errors.

- [ ] **Step 4: Update the solution document with verification evidence**

Add a dated verification-results section to `docs/self-intersection-label-deletion-fix.md` listing each command, exit code, and result without claiming checks that did not run.

- [ ] **Step 5: Review the final diff**

Run:

```powershell
git status --short
git diff --check
git diff -- app/test/drawable/polygon.test.ts app/test/drawable/polygon_self_intersection_regression.test.ts docs/self-intersection-label-deletion-fix.md
```

Expected: only the intended tests and documentation are changed, with no whitespace errors or temporary mutation.
