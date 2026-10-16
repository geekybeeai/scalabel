# Annotation QA: find and fix lane/edge label mistakes

Date: 2026-10-17 · Owner: Roshan · Time budget: 8–12 hours total, including Roshan's triage and Scalabel review time

## Why

Six Scalabel projects of aerial road imagery have been annotated and reviewed
once (export of 2026-10-15). A downstream reviewer still flags frames with one
or more mistakes. The most common are curbs labelled as soft edges and the
reverse, lines that are not on their paint or road edge, and wrong lane-line
classes. Checking about 1,900 frames by hand is not possible in the time
available. We need a tool that finds the likely mistakes, then fixes them one
issue type at a time, with every fix confirmed by Roshan in Scalabel.

## Inputs

- **Exports (the labels to check):**
  `local-data/items/Full_Verfication/Annotation_Reviewed_by_Roshan/2026_10_15_export/*.json`.
  There are 6 files, each `{frames, config}`, with about 1,890 frames and about
  49k `poly2d` labels in total. Every label is an open or closed polyline in one
  of 6 categories.
- **Images:** each frame's `url` is a path relative to `local-data/`, for
  example `items/Full_Verfication/batchwise_data/<batch>/images/<id>_verification.png`.
  They are RGB orthomosaics 5,000–20,000 px wide. The captured road footprint
  sits inside pure-black padding.
- **Reviewer-flagged examples:**
  `local-data/items/Full_Verfication/Comment_by_Reviewer/data_2026_10_16_verify/*.jpg`.
  These are 8 frames, plus 2 old-vs-new comparisons, rendered with labels.
  Each contains at least one real mistake. There are no comments saying which
  line is wrong.
- **Running Scalabel:** `http://localhost:8686/`. Project storage is in
  `local-data/scalabel/projects/<project name>/`.

## Labelling conventions (confirmed by Roshan)

| Category | What it marks | How it is drawn |
|---|---|---|
| `curb_road_edge` | Asphalt meeting a raised concrete curb, sidewalk or median | One line along the road edge |
| `without_curb_road_edge` ("soft edge") | Asphalt fading into dirt, gravel or grass, with no curb | One line along the road edge |
| `solid_single_line` | A single solid painted line, **white or yellow** | One line on the paint |
| `dashed_white_line` | A dashed white line | One line through the dashes |
| `double_yellow_line` | Two parallel solid yellow lines. At this resolution they usually merge into one wide yellow band | **One** line down the middle of the band |
| `center_turn_lane` | Each side of a two-way turn lane: a solid yellow plus a parallel dashed yellow | **Two** lines, one per side, each on that side's **solid** yellow |

Verified examples: `102452_202460` (double yellow) and `101713` (centre turn
lane), both in `batch_3_images_606_shared_by_Jheel_TMI_locations`.

## Goals

1. **Find (no edits).** Scan every label in all 6 exports and produce an issue
   list grouped by issue type. Each issue has the frame, label id, category,
   confidence, a one-line reason and a raw/labelled crop.
2. **Fix by issue type.** For a type Roshan picks, write corrected labels into
   a **copy** of the export, and create a Scalabel project containing **only
   the frames that changed**, so Roshan can review them at `localhost:8686`.
3. **Final review.** After all rounds, produce one view of every edited frame
   so all edits can be checked together.

## Issue types to detect

**Geometry rule (applies to every check and every render):** a polyline's
`types` string marks each vertex. `L` is a point on the line. A `C` starts a
cubic Bezier: vertices i and i+1 are **control points (not on the line)** and
vertex i+2 is the end point. All sampling, scoring, crops and fixes must use
the real curve, via `poly_to_points()` in `scalabel/tools/draw_poly2d.py`
(reuse it, don't re-implement it). Fixes that move a line must move control
points and end points together, so the curve keeps its shape. An early spike
that joined control points with straight lines produced fake "wandering
line" issues.

Every check runs on image pixels near the line, inside the ROI only. The ROI
excludes the black padding.

1. **Curb ↔ soft edge.** Look at the strip just outside the road edge. Concrete
   (bright, low-saturation, uniform) means a curb. Dirt, gravel or vegetation
   (textured, brown or green) means a soft edge. Flag lines whose category
   disagrees.
2. **Solid ↔ dashed.** Measure the share of the line's length that sits on
   paint, and how regular the gaps are. Mostly continuous paint means solid.
   Regular on/off paint means dashed. Flag `solid_single_line` over dashes and
   `dashed_white_line` over continuous paint.
3. **Double yellow / centre turn lane structure.**
   - A `double_yellow_line` should sit on a yellow band noticeably wider than a
     single line.
   - A `center_turn_lane` side should have dashed yellow alongside its solid
     yellow, and a partner line on the other side of the lane.
   - Flag a single line where a pair is expected, a missing partner, and
     yellow-pattern mismatches.
4. **Off position.** For each line, sample the image across the line, perpendicular
   to each segment, about ±25 px, and score it for the paint it should sit on
   (bright, unsaturated paint for white; yellow paint for yellow) or the road
   boundary for edges. Two sub-kinds:
   - **4a. Shifted:** a clear paint or edge peak a few px or more from the
     line. Record the offset; the fix step moves the line onto it.
   - **4b. Not on any paint:** no clear peak anywhere near the line, so the line
     wanders across lanes, cuts a corner or runs off the road. Mark it "draw by
     hand", since it can't be fixed by a shift.

   Lessons from spikes on `97944_198438` (batch_3; Roshan's screenshot frame,
   item 24 of `28_samples_project_2026_10_15_19_00`):
   - **Check locally, not per whole line.** Score stretches of about 60 px
     along the line. A line that is on its paint for most of its length but
     drifts off at one corner averages out to "fine". The screenshot case,
     `FHFFZWLKhFszCCJZ`, a solid line about 11 px off its white paint beside a
     curb corner, was found only by the local check.
   - **Paint is a narrow stripe brighter than the road on both sides.** A bare
     "bright and colourless" score also matches the concrete curb band.
   - Offsets of 3 px or less are noise. Flag above about 5 px.
   - **First calibration (Roshan, 2026-10-17).** The local run flagged 6 lines
     on this frame:
     - Real: `AsxYZY` (6 px), `68JTnf` (22 px), `FHFFZW` (11 px, the screenshot
       line) and `NAJYkC` (6 px).
     - False alarm: `4x8ZIq` (25 px) and `mMZD5Y` (19 px).

     The size of the offset did not separate them. What did was **how much
     paint sits under the line itself.** The four real ones had none: the
     ridge score at the line was between −6 and −3. Both false alarms already
     sat on paint, with scores of 34 and 81, and had simply found a stronger
     neighbouring stripe. Rule to start from: flag only when the line has
     almost no paint under it (score below about 10) **and** a clear stripe
     exists within ±25 px.

5. **Bad shape.** Zigzags, sharp turn-backs (more than about 120° reversal),
   self-crossings and tiny stray fragments. Seen in `98973_199454`, Batch_2 TMI.
6. **Duplicates / overlaps.** Two labels on top of each other (same feature,
   same or different class), or one feature broken into many overlapping
   pieces.

Thresholds are the builder's to tune. The calibration bar is below.

## Calibration and acceptance

- Every one of the 8 reviewer-flagged frames must produce at least one issue
  that Roshan agrees is real.
- Spot check: in a random sample of 30 issues per type, at least about two
  thirds should be real. If a type falls short, raise its threshold, or show
  it only as low confidence, before using it to fix anything.
- Fix rounds must never change labels that were not flagged and accepted.

## Output: the review pages

Both are local HTML pages generated from the data (no server needed beyond a
static file).

- **View A, by issue type (triage and fixing).** One tab per issue type with a
  count. Cards sorted by confidence. Each card shows:
  - a raw/labelled crop with the flagged line drawn thicker
  - batch, frame, label id, category, confidence and reason
  - "this frame has N other issues"
  - an "Open in Scalabel" link
  - **real / false alarm** buttons

  Choices are saved to a JSON file the fix step reads. Only issues marked
  **real** are fixed. Unmarked issues are not fixed.
- **View B, by frame (final review).** One row per **edited** frame, listing
  every change made to it across all fix rounds, with before/after crops and a
  link to the frame in the review project.

## Fix workflow

- **Never modify the original exports or existing Scalabel projects.** Each
  round writes `<export>_fix_<issue-type>_v<N>.json` next to a working copy.
  Rounds stack: round 2 starts from round 1's output.
- **Fixes by type:**
  - Class errors: change the category.
  - Off position: shift the vertices by the measured offset.
  - Bad shape: remove the zigzag or turn-back vertices.
  - Duplicates: delete the redundant label.

  A centre turn lane or double yellow with a missing partner cannot be fixed by
  moving or relabelling, so it stays a flagged issue for Roshan to draw by hand.
- **Review project:** for each round, build an item list of only the changed
  frames, with labels, and create a new Scalabel project such as
  `<batch>_fix_<issue-type>_v<N>`. Roshan reviews and adjusts there. After
  review, Roshan's export of that project is the accepted version of those
  frames, and it is merged back over the working copy.
- **Final deliverable:** one corrected export per batch, merging all accepted
  rounds, plus View B.

## Non-goals

- Finding paint or edges that were never labelled at all.
- Training an ML model. Classic image heuristics only, given the time budget.
- Changing the Scalabel app itself.

## Anti-goals (a failure even if it "works")

- Silently changing labels Roshan has not seen. Every edit must land in a
  review project.
- Touching the original exports or projects.
- A flood of false alarms that takes longer to dismiss than reviewing by hand.

## Decided by Claude (Roshan can override)

- **Bulk accept.** View A has "mark all of this type above confidence X as
  real", as well as per-card buttons. This is safe because every edit still
  lands in a Scalabel review project before it counts.
- **Flag-only issues reach the deliverable.** Frames with issues the tool
  cannot fix (a missing partner line, accepted low-confidence issues) are
  included **unchanged** in that round's review project. Their cards carry a
  "draw by hand" note, so Roshan's manual fixes are merged like any other
  edit.
- **Rounds are sequential.** Each round waits for Roshan's review and export,
  which is merged into the working copy. The next issue type is then
  **re-detected on the merged copy**, so issue marks never point at stale
  labels. Triage happens per round, just before that round's fix. Detection is
  fast, so this costs little.

- Code lives in `tools/annotation_qa/` (Python, next to `tools/annotation_fix/`).
  It reuses `tools/annotation_fix/mask.py` for the ROI mask and
  `tools/requirements.txt` for dependencies: pillow, numpy and scipy, plus
  OpenCV if needed.
- Outputs (reports, crops, fixed JSON) go to
  `local-data/items/Full_Verfication/qa_2026_10_17/`, which is gitignored with
  `local-data/`.
- Review projects are created **without** the create form's "Auto-correct
  annotations" option, so the only changes Roshan sees are this tool's.

## Left to the builder

- Detection methods and thresholds for each issue type.
- How review projects are created: via the `/create` form, or by writing the
  project files the server reads, whichever round-trips cleanly with this
  fork's import/export.
- Crop size and zoom, and page styling.

## Build order (all six issue types are in scope)

Ship issue types one at a time, so Roshan can start triaging and reviewing
while the next detector is built:

1. Curb ↔ soft edge.
2. Off position (4a shifted, 4b not on paint). Moved up after Roshan reported it in review.
3. Bad shape (zigzag, turn-back, fragments).
4. Duplicates / overlaps.
5. Solid ↔ dashed.
6. Double yellow / centre turn lane structure.

View A comes with type 1. The fix pipeline and review-project creation come
with the first fix round. View B comes after the last round.
