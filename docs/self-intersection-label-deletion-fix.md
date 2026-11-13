# Prevent Silent Deletion of Self-Intersecting Closed Labels

## Status

The production behavior described in this document is already present on the
current `dev` branch: `Polygon2D.isValid()` does not reject a closed shape only
because its edges intersect. The remaining work is to replace stale test
expectations and add focused regression coverage so a future merge or conflict
resolution cannot silently restore the destructive behavior.

## Problem

Scalabel supports closed lane and curb annotations that may legitimately fold
back across themselves. A hairpin curb edge and an imported model prediction
can therefore be closed and self-intersecting without being corrupt.

The faulty validation path treated every self-intersection as invalid:

```text
reviewer edits or touches a closed label
                |
                v
       Polygon2D.isValid() == false
                |
                v
          commit2DLabels()
             /       \
            /         \
 non-tracking          tracking
 deleteInvalidLabel    terminateTrackFromDrawable
       |                       |
 label disappears       track is ended/deleted
```

There is no confirmation or error message on this path. The user experiences
the result as silent data loss.

## Root cause

The old `Polygon2D.isValid()` implementation built all straight segments in a
closed path and rejected the drawable when any non-adjacent pair intersected.
That geometric rule is unsuitable for this fork's annotation domain: a closed
self-crossing lane or curb can be intentional.

The destructive consequence comes from the generic invalid-drawable handling
in `commit2DLabels()`:

- an existing non-tracking drawable is passed to `deleteInvalidLabel()`;
- an existing tracking drawable is passed to
  `terminateTrackFromDrawable()`;
- a temporary invalid drawable is simply not added.

Changing `commit2DLabels()` globally would be unsafe because it also handles
genuinely invalid geometry and merge bookkeeping. The validation rule must be
corrected at its source instead.

## Selected solution

Keep self-intersecting closed paths valid. `Polygon2D.isValid()` must reject a
drawable only when one of the existing, domain-safe conditions applies:

1. the drawable has been merged into another drawable (`_mergedOut`);
2. drawing is not finished;
3. a closed shape's bounding box is smaller than `MIN_SIZE`;
4. an open polyline has fewer than two vertices.

Do not restore segment-intersection helpers or a category-specific exception.
The same rule must apply to newly drawn shapes, imported predictions, and
tracking labels.

## Alternatives considered

### Stop deleting all invalid existing drawables

Rejected. This would change the meaning of invalid merge targets and genuinely
degenerate shapes throughout `commit2DLabels()`. It fixes the symptom at a
shared downstream layer and risks retaining corrupt drawables.

### Allow self-intersections only for curb categories

Rejected. Geometry validation would become coupled to mutable category names,
and imported datasets could use different taxonomies. Other legitimate closed
lane shapes could still be deleted.

### Warn before deletion while retaining the rejection

Rejected as the primary fix. A warning does not make a valid domain shape
invalid, and tracking termination would remain the wrong operation.

## Implementation

### 1. Preserve the production invariant

File: `app/src/drawable/2d/polygon2d.ts`

The closed-shape branch of `Polygon2D.isValid()` must retain the bounding-box
degeneracy check and must not enumerate or intersect-test line segments. Keep a
nearby comment explaining that this is deliberate because invalid existing
drawables are deleted or terminate tracks downstream.

The current `dev` branch already satisfies this requirement. No additional
production refactor is needed unless the rejection is present on the target
branch.

### 2. Correct the stale polygon validation test

File: `app/test/drawable/polygon.test.ts`

The existing `validation check for polygon2d` test still describes a
self-touching closed path as invalid. Split or rename the test so it explicitly
asserts both sides of the contract:

- a self-intersecting closed shape with a non-degenerate bounding box is
  created and retained;
- reshaping an existing closed shape into a self-intersection commits the new
  points instead of reverting or deleting the label;
- a closed shape below `MIN_SIZE` remains invalid and is not added.

Use fixed coordinates so the test is deterministic. Suitable bow-tie vertices
are `(200,100)`, `(400,300)`, `(200,300)`, `(400,100)`.

### 3. Cover imported/touched labels and tracking projects

Create: `app/test/drawable/polygon_self_intersection_regression.test.ts`

Add focused behavioral tests using real `Polygon2D`, `Label2DList`, Redux
state, and `commit2DLabels()`:

1. Seed an imported, non-temporary closed bow-tie label, commit/touch it, and
   assert that the label and all of its shapes remain in state.
2. Seed the same geometry as a label in a track, commit/touch it, and assert
   that the track and its labels remain present on every frame.

The too-small-shape assertion remains in `polygon.test.ts` alongside the draw
and reshape coverage.

These tests protect the actual destructive boundary rather than testing a
private intersection helper.

## Regression-proof test procedure

The current production code already contains the fix, so new tests would pass
immediately. Prove that they detect the historical bug with a temporary local
mutation:

1. Add the regression tests against the intended behavior.
2. Temporarily restore the old self-intersection rejection in
   `Polygon2D.isValid()`.
3. Run the focused tests and confirm failures show label deletion or track
   termination.
4. Remove the temporary rejection and restore the intended implementation.
5. Run the same tests and confirm they pass.
6. Confirm `git diff` contains no temporary intersection implementation.

The mutation is verification-only and must never be committed.

## Automated verification

The Jest global setup expects Redis on port `6378`. On Windows, start a
temporary Docker instance when a native `redis-server` binary is unavailable:

```powershell
docker run --name scalabel-test-redis -d -p 6378:6379 redis:7-alpine
```

Run the focused drawable tests:

```powershell
npm test -- --runInBand app/test/drawable/polygon.test.ts app/test/drawable/polygon_self_intersection_regression.test.ts
```

The repository teardown currently invokes Unix `pkill`. On native Windows,
run Jest from WSL or supply a Windows-compatible `globalTeardown` override;
this is a test-harness portability issue and is unrelated to polygon
validation.

Then run repository checks:

```powershell
npm test -- --runInBand
npm run lint
npm run build
```

If a check cannot run because Redis, Docker, or another external dependency is
unavailable, report that limitation explicitly; do not claim a passing suite.

## Implementation result (2026-11-13)

The implementation preserves the existing production `isValid()` behavior and
adds six focused assertions:

- drawing a self-intersecting closed polygon retains it;
- drawing a polygon whose edge touches a non-adjacent vertex retains it;
- reshaping an existing polygon into a self-intersection retains the label and
  new point;
- too-small closed polygons are still rejected;
- committing an imported self-intersecting prediction retains its label and
  shapes;
- committing the same geometry in a tracking task retains the track and every
  frame label.

For mutation verification, the historical segment-crossing rejection was
temporarily restored. The imported-label and tracking tests both failed: the
first label was deleted and the second track became `undefined`. Removing the
mutation restored all focused tests to green. No mutation code remains in the
working tree.

The historical collinear-touch branch was also restored temporarily. The
explicit non-adjacent vertex-touch test failed with zero retained labels, then
passed after the mutation was removed.

### Verification record

| Check                                                          | Result                                                                                                                        |
| -------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Six focused self-intersection/self-touch/degeneracy assertions | PASS                                                                                                                          |
| Mutation with historical crossing/touch rejection              | Expected RED: self-touch, imported label, and tracking tests fail                                                             |
| Same focused assertions after mutation removal                 | PASS                                                                                                                          |
| Changed-file ESLint                                            | PASS                                                                                                                          |
| Changed-file Prettier check                                    | PASS                                                                                                                          |
| Production Webpack build                                       | PASS                                                                                                                          |
| Complete `polygon.test.ts` plus new regression file            | New tests pass; two unrelated existing curve/deletion assertions fail with `expected 4, received 6`                           |
| Full Jest suite on Node 24.18.0                                | Environment-blocked: `canvas@2.9.1` has no `build/Release/canvas.node`; 78 suites cannot start, while 15 suites/102 tests run |
| Repository-wide lint on this Windows checkout                  | Baseline-blocked by 76,455 unrelated CRLF/Prettier errors; changed files are clean                                            |

For a clean full-suite result, use the repository's supported Node/toolchain
with a successfully installed native `canvas` binding, then rerun the commands
in **Automated verification**. Do not treat the Node 24 native-module failure
as a polygon regression.

## Manual QA

Use a full Scalabel dataset export containing a closed bow-tie label:

```json
{
  "vertices": [
    [100, 100],
    [300, 300],
    [100, 300],
    [300, 100]
  ],
  "types": "LLLL",
  "closed": true
}
```

1. Create an Image/Polygon project with **Submit single file** enabled.
2. Open the task and select the imported self-crossing label.
3. Drag a vertex slightly and release it.
4. Save or navigate away and return.

Expected results:

- the label remains visible;
- its edited geometry persists;
- no related labels disappear;
- in a tracking project, the track still exists on all expected frames;
- undo history does not record a deletion merely because the label was
  touched.

## Acceptance criteria

- `Polygon2D.isValid()` does not reject a closed label solely for
  self-intersection.
- Newly drawn and imported self-intersecting closed labels survive commit.
- Touching such a label does not call the invalid-label deletion behavior.
- Tracking labels do not terminate their tracks.
- Too-small closed shapes remain invalid.
- Regression tests fail when the old rejection is temporarily restored and
  pass with the intended implementation.
- The focused tests, full Jest suite, lint, and build results are recorded
  truthfully.

## Rollback guidance

Do not roll back by restoring the segment-intersection rejection. If a newly
discovered geometry causes renderer or exporter failures, isolate that exact
unsupported condition with a separate test and reject only that condition.
Keep legitimate self-crossing lane and curb annotations valid.
