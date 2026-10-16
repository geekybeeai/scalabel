# Annotation QA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python tool that finds likely lane and road-edge label mistakes in six Scalabel exports, lets Roshan triage them in a browser, applies the accepted fixes one issue type at a time into copies, and loads only the changed frames into new Scalabel review projects.

**Architecture:** A `tools/annotation_qa` package with five parts:
- shared loaders: export JSON, curve geometry, image scores
- one detector module per issue type, each returning `Issue` records
- a scan CLI that writes `issues.jsonl` and crops
- a tiny local review server (View A, plus View B at the end) that saves Roshan's marks
- a fix/project/merge pipeline that talks to the running Scalabel server through `POST /postProject`

**Tech Stack:** Python 3.12, numpy, scipy, opencv-python (cv2), pillow, pytest. Standard-library `http.server` for the review page. No new web framework.

**Spec:** `docs/superpowers/specs/2026-10-17-annotation-qa-design.md`

## Global Constraints

- **Never write to the original exports** in `local-data/items/Full_Verfication/Annotation_Reviewed_by_Roshan/2026_10_15_export/`, or to any existing project under `local-data/scalabel/projects/`.
- **All outputs** go under `local-data/items/Full_Verfication/qa_2026_10_17/` (the `OUT` dir).
- **Curve rule:** in a polyline's `types`, `L` is a point on the line. A `C` starts a cubic Bezier where vertices i and i+1 are control points and vertex i+2 is the end point. Every sample, crop and fix goes through `poly_to_points()` from `scalabel/tools/draw_poly2d.py`. Import it; never re-implement it.
- **A fix that moves a curve** moves its control points and end points together.
- **Categories (exact strings):** `curb_road_edge`, `without_curb_road_edge`, `solid_single_line`, `dashed_white_line`, `double_yellow_line`, `center_turn_lane`.
- **Conventions:**
  - `solid_single_line` is white **or** yellow.
  - `double_yellow_line` is ONE line down the middle of a wide yellow band.
  - `center_turn_lane` is TWO lines, one per side, each on that side's solid yellow.
- **Image paths:** a frame's `url` is relative to `local-data/`. Images are 5,000–20,000 px wide and up to about 190 MP. Never convert a whole image to float. Score only a label's padded bounding-box crop.
- **ROI:** pixels outside the ROI (black padding) are ignored. Use `tools/annotation_fix/mask.py` `load_roi_mask(path, cache_dir=OUT/"roi_cache")`.
- **Review projects** are created with `auto_correct` = `"false"`. They contain only changed or flagged frames and are named `<export stem>_fix_<issue_type>_v<N>`.
- **Position check:**
  - Score stretches of 20 samples × 3 px, about 60 px.
  - Paint score = ridge (centre minus the max of ±5 px sides).
  - Flag only when the ridge under the line is below 10 **and** a ridge of 25 or more exists within ±25 px.
  - Offsets of 3 px or less are never issues.
- **Calibration bar:**
  - Every one of the 8 reviewer-flagged frames yields at least one issue Roshan marks real.
  - In 30 random issues per type, at least 2/3 are real before that type is used for fixing.
- **Commits:** only when Roshan asks. Commit steps below are where a commit belongs, if he approves committing.

## Review Focus

1. **Missing or unreadable image.** Log it and skip the frame; the scan continues and the summary lists skipped frames. → test in Task 3.
2. **Line near or beyond the ROI edge.** Samples outside the ROI are dropped. A line with fewer than 5 valid samples yields no issue (no crash, no false "off position"). → test in Task 2.
3. **Labels with several `poly2d` entries, or closed polylines** (for example a curb loop around a median). Every entry is checked, and closed ones wrap their last segment. → test in Task 1.
4. **A curve-shift fix on a `LCCL…` line** keeps the curve's shape: the shifted curve's points all sit at the requested offset ± 1 px. → test in Task 6.
5. **The same frame name in two exports** (Random and TMI batches overlap). Issues, marks and merges are keyed by `(export_stem, frame_name, label_id)`, never by frame name alone. → test in Task 3 and Task 6.

---

## File Structure

```
tools/annotation_qa/
  __init__.py
  __main__.py          # `python -m annotation_qa <scan|review|fix|merge|viewb>`
  data.py              # Export load/save, frame iteration, image path resolve
  geometry.py          # dense curve sampling, normals, vertex shifting
  imaging.py           # LabelCrop: ROI-aware paint/surface scores for one label
  issues.py            # Issue dataclass, JSONL store, marks store
  detectors/__init__.py   # REGISTRY: {issue_type: detect_fn}
  detectors/curb_soft.py
  detectors/position.py
  detectors/shape.py
  detectors/duplicates.py
  detectors/solid_dashed.py
  detectors/yellow_structure.py
  scan.py              # run detectors over exports, render crops
  render.py            # crop rendering (raw | labelled, flagged line thick magenta)
  review_server.py     # View A + View B pages, POST /marks
  fix.py               # apply accepted fixes -> working copy, changed frames
  scalabel_project.py  # build subset item file, POST /postProject
  merge.py             # merge a reviewed export back into the working copy
  tests/conftest.py    # synthetic road images and label builders
  tests/test_*.py
tools/requirements.txt # + opencv-python-headless>=4.8
```

Run tests from `tools/`: `cd tools && python3 -m pytest annotation_qa/tests -q`. Imports are `from annotation_qa.x import y`, matching `annotation_fix`.

---

### Task 1: Exports and curve geometry

**Files:**
- Create: `tools/annotation_qa/__init__.py`, `tools/annotation_qa/data.py`, `tools/annotation_qa/geometry.py`, `tools/annotation_qa/tests/conftest.py`, `tools/annotation_qa/tests/test_geometry.py`, `tools/annotation_qa/tests/test_data.py`
- Modify: `tools/requirements.txt` (add `opencv-python-headless>=4.8`)

**Interfaces:**
- Produces:
  - `data.Export(path: Path, frames: list[dict], config: dict)` with `.stem -> str`
  - `data.load_export(path: Path) -> Export`
  - `data.save_export(exp: Export, path: Path) -> None`. Writes `{"frames", "config"}` and refuses to overwrite anything under the originals dir by raising `ValueError`.
  - `data.iter_polys(frame: dict) -> Iterator[tuple[dict, int, dict]]`, yielding `(label, poly_index, poly)`
  - `data.image_path(frame: dict, root: Path) -> Path`
  - `geometry.dense(poly: dict) -> np.ndarray (N,2)`, a thin wrapper over `poly_to_points` that imports `scalabel/tools/draw_poly2d.py` by inserting its directory into `sys.path`
  - `geometry.resample(pts: np.ndarray, step: float = 3.0) -> tuple[np.ndarray, np.ndarray]`, returning `(P (M,2), Nrm (M,2))` with unit normals `(-ty, tx)`, or `(None, None)` if the length is under 15 px
  - `geometry.length(pts) -> float`
  - `geometry.shift_poly(poly: dict, offsets: np.ndarray) -> dict`. `offsets` holds one signed perpendicular shift per resampled point of `resample(dense(poly))`, and the result is a new poly whose vertices (control points included) are moved.

- [ ] **Step 1: Write failing tests**

```python
# test_geometry.py
def test_dense_follows_bezier_not_control_points():
    poly = {"vertices": [[0,0],[0,100],[100,100],[100,0]], "types": "LCCL", "closed": False}
    pts = dense(poly)
    assert pts[0].tolist() == [0,0] and pts[-1].tolist() == [100,0]
    assert np.abs(pts[:,1]).max() == pytest.approx(75, abs=1)   # bezier peak, not 100

def test_closed_poly_wraps():
    poly = {"vertices": [[0,0],[10,0],[10,10]], "types": "LLL", "closed": True}
    assert dense(poly)[-1].tolist() == [0,0]

def test_resample_short_line_returns_none():
    assert resample(np.array([[0,0],[10,0]]))[0] is None

def test_shift_poly_moves_curve_uniformly():
    poly = {"vertices": [[0,0],[0,100],[100,100],[100,0]], "types": "LCCL", "closed": False}
    P, N = resample(dense(poly))
    moved = shift_poly(poly, np.full(len(P), 8.0))
    Q = dense(moved)
    d = cKDTree(Q).query(P + 8.0*N)[0]
    assert d.max() < 1.5

# test_data.py
def test_iter_polys_yields_every_entry(two_poly_label_frame):
    assert len(list(iter_polys(two_poly_label_frame))) == 2

def test_save_export_refuses_originals(tmp_path, small_export):
    with pytest.raises(ValueError):
        save_export(small_export, ORIGINALS_DIR / "x.json")
```

- [ ] **Step 2:** Run `cd tools && python3 -m pytest annotation_qa/tests -q`. Expected: FAIL (ImportError).
- [ ] **Step 3: Implement `data.py` and `geometry.py`** with the signatures above. In `shift_poly`:
  - An `L` vertex takes the offset × normal of its nearest resampled point.
  - The control points of a `C` segment take the mean of the offsets and normals of the resampled points between that segment's start and end vertices.
  - `ORIGINALS_DIR = Path("local-data/items/Full_Verfication/Annotation_Reviewed_by_Roshan/2026_10_15_export").resolve()`
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit (if approved): `feat(annotation-qa): export loading and curve geometry`

### Task 2: Image scores per label

**Files:**
- Create: `tools/annotation_qa/imaging.py`, `tools/annotation_qa/tests/test_imaging.py`
- Modify: `tools/annotation_qa/tests/conftest.py` (synthetic image builders)

**Interfaces:**
- Consumes: `geometry.dense`, `geometry.resample`; `annotation_fix.mask.load_roi_mask`
- Produces:
  - `imaging.FrameImage(path: Path, roi_cache: Path)`
    - loads the BGR uint8 image once, lazily, plus its ROI mask
    - `.crop(x0, y0, x1, y1) -> np.ndarray`
  - `imaging.Profile` (dataclass):
    - `offsets: np.ndarray`, integers −R..R
    - `white: np.ndarray (M, 2R+1)` and `yellow: np.ndarray (M, 2R+1)`, the ridge scores per resampled point
    - `valid: np.ndarray[bool] (M,)`, all samples inside the ROI and the canvas
    - `P`, `Nrm`
  - `imaging.profile(img: FrameImage, poly: dict, R: int = 25, side_gap: int = 5) -> Profile | None`. Returns None when there are fewer than 5 valid samples.
  - `imaging.side_strips(img: FrameImage, poly: dict, near: int = 6, far: int = 20) -> tuple[StripStats, StripStats] | None`, giving the left and right strips
  - `StripStats(gray, sat, green, brown, texture: float, n: int)`, where:
    - `green = G - (R+B)/2`
    - `brown = R - B`
    - `texture` = the std of gray inside the strip

  Scores are computed only on a crop of the poly's bounding box padded by `R + side_gap + 2`, cast to float32 there and nowhere else:
  - `white = gray - 0.8*sat`
  - `yellow = (R+G)/2 - B`
  - both blurred 3×3
  - ridge = centre − max(left side, right side)

- [ ] **Step 1: Write failing tests** (synthetic 400×400 images built in conftest)

```python
def test_ridge_peaks_on_white_stripe(road_with_white_stripe):   # stripe 5px wide at y=200
    img, poly_on, poly_off12 = road_with_white_stripe
    pr = profile(img, poly_off12)
    assert pr.offsets[pr.white[pr.valid].mean(0).argmax()] in (-12, 12)

def test_concrete_band_is_not_a_ridge(road_with_concrete_band):  # 30px bright band
    img, poly = road_with_concrete_band
    pr = profile(img, poly)
    assert pr.white[pr.valid].mean(0).max() < 10

def test_profile_none_outside_roi(road_mostly_black):
    img, poly = road_mostly_black
    assert profile(img, poly) is None

def test_side_strips_green_vs_concrete(road_with_grass_left_concrete_right):
    img, poly = road_with_grass_left_concrete_right
    left, right = side_strips(img, poly)
    assert left.green > right.green + 15 and right.gray > left.gray
```

- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement `imaging.py` as specified. Sample with `cv2.remap` or rounded indexing on the crop. The ROI check uses `RoiMask.mask` at the rounded full-image coordinates.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit (if approved): `feat(annotation-qa): ROI-aware paint ridge and side-strip scores`

### Task 3: Issue model, scan CLI and crops

**Files:**
- Create: `tools/annotation_qa/issues.py`, `tools/annotation_qa/detectors/__init__.py`, `tools/annotation_qa/scan.py`, `tools/annotation_qa/render.py`, `tools/annotation_qa/__main__.py`, `tools/annotation_qa/tests/test_scan.py`

**Interfaces:**
- Consumes: Tasks 1–2
- Produces:
  - **The `Issue` dataclass** (JSON-serialisable):

    ```python
    @dataclass
    class Issue:
        issue_type: str        # "curb_soft" | "position" | "shape" | "duplicates" | "solid_dashed" | "yellow_structure"
        export_stem: str
        frame_name: str
        label_id: str
        category: str
        confidence: float      # 0..1
        reason: str            # one line, human readable
        anchor: tuple[float, float]   # image x, y for the crop centre
        fix: dict | None       # {"op": "set_category", "to": str} | {"op": "shift", "offsets": list[float]}
                               # | {"op": "delete"} | {"op": "drop_vertices", "indices": list[int]} | None
        hand_fix: bool         # True -> cannot auto-fix; frame goes to review unchanged
        related_label_ids: list[str] = field(default_factory=list)
        @property
        def key(self) -> str: return f"{self.export_stem}|{self.frame_name}|{self.label_id}|{self.issue_type}"
    ```
  - `issues.write_issues(path, list[Issue])` and `issues.read_issues(path) -> list[Issue]`, as JSONL
  - `issues.read_marks(path) -> dict[str, str]`, mapping key to `"real"` or `"false"`. A missing file gives `{}`.
  - `issues.write_marks(path, dict)`
  - `detectors.REGISTRY: dict[str, Callable[[FrameContext], list[Issue]]]`
  - `scan.FrameContext(export: Export, frame: dict, image: FrameImage)`
  - `scan.run_scan(exports: list[Path], root: Path, out: Path, types: list[str]) -> ScanSummary(frames: int, skipped: list[str], counts: dict[str, int])`. It:
    - writes `out/issues_<type>.jsonl`
    - writes crops to `out/crops/<type>/<sha1(key)[:12]>.jpg`
  - `render.issue_crop(image: FrameImage, frame: dict, issue: Issue, half: int = 90, zoom: int = 3) -> np.ndarray`:
    - raw on the left, a gap of 8 px, labelled on the right
    - the flagged label in magenta `(255,0,255)` BGR at width 3, others in `draw_poly2d.COLORS` at width 2
    - `issue.related_label_ids` drawn in cyan at width 3
  - **CLI:** `python3 -m annotation_qa scan --exports <dir|files> --root local-data --out OUT --types curb_soft,position`

- [ ] **Step 1: Write failing tests**

```python
def test_scan_skips_missing_image_and_continues(tmp_path, export_with_one_missing_image, fake_detector):
    s = run_scan([export_with_one_missing_image], ROOT, tmp_path, ["fake"])
    assert len(s.skipped) == 1 and s.counts["fake"] == 1

def test_issue_key_distinguishes_exports():
    a = Issue("shape","exportA","f.png","L1","solid_single_line",.9,"r",(0,0),None,False)
    b = replace(a, export_stem="exportB")
    assert a.key != b.key

def test_issues_roundtrip(tmp_path):
    ...  # write then read equals the original list

def test_crop_highlights_label_in_magenta(synthetic_frame_ctx, an_issue):
    crop = issue_crop(synthetic_frame_ctx.image, synthetic_frame_ctx.frame, an_issue)
    right = crop[:, crop.shape[1]//2 + 4:]
    assert ((right == [255,0,255]).all(-1)).sum() > 50
```

`fake_detector` registers `REGISTRY["fake"]`, which returns one issue per frame.

- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement. `run_scan` loads one `FrameImage` per frame, runs the requested detectors, renders a crop per issue, and frees the image before the next frame. Unreadable images go to `skipped` with a logged warning.
  - **Scan the latest working copy.** For each export, scan `OUT/work/<stem>.json` when it exists (after an earlier merged round) instead of the original. This way every round re-detects on the merged copy, as the spec requires. Add `test_scan_prefers_working_copy`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit (if approved): `feat(annotation-qa): issue model, scan CLI and crops`

### Task 4: Review server (View A)

**Files:**
- Create: `tools/annotation_qa/review_server.py`, `tools/annotation_qa/tests/test_review_server.py`

**Interfaces:**
- Consumes: `issues.read_issues`, `read_marks`, `write_marks`; crops from Task 3
- Produces: `python3 -m annotation_qa review --out OUT [--port 8790]`. This serves `http://localhost:8790/`.
  - `GET /` renders View A:
    - one tab per issue type present, labelled with its count
    - cards sorted by confidence, descending
    - each card shows: crop, export stem, frame, label id, category, confidence, reason, "this frame has N other issues", "draw by hand" badge when `hand_fix`
    - an "Open in Scalabel" link: `http://localhost:8686/label?project_name=<export stem>&task_index=0`, plus the frame number in the card text
    - **Real** and **False alarm** buttons
    - per tab: "Mark all ≥ [confidence input] as real"
  - `GET /crops/...` serves the crop files
  - `POST /marks` takes a JSON body `{key: "real"|"false"}`, merges it into `OUT/marks.json`, and responds `200 {"saved": n}`
  - `GET /marks` returns the current marks, so the page restores state on reload

- [ ] **Step 1: Write failing tests.** Start the server on port 0 in a thread, then:

```python
def test_post_marks_merges_and_persists(server, out_dir):
    post(server, "/marks", {"k1": "real"}); post(server, "/marks", {"k2": "false"})
    assert read_marks(out_dir/"marks.json") == {"k1": "real", "k2": "false"}

def test_index_lists_tabs_with_counts(server):   # out_dir has 3 shape + 2 position issues
    html = get(server, "/")
    assert "shape (3)" in html and "position (2)" in html

def test_bulk_threshold_marks_only_above(server):
    post(server, "/marks/bulk", {"issue_type": "shape", "min_confidence": 0.8})
    assert set(read_marks(...)) == {keys of shape issues with confidence >= 0.8}
```

- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement it with `http.server.ThreadingHTTPServer`. The page is a single HTML string with inline CSS and JS. Buttons `fetch('/marks', {method:'POST'})`. Bind only to `127.0.0.1`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit (if approved): `feat(annotation-qa): View A review server with marks`

### Task 5: Detector, curb ↔ soft edge

**Files:**
- Create: `tools/annotation_qa/detectors/curb_soft.py`, `tools/annotation_qa/tests/test_curb_soft.py`
- Modify: `tools/annotation_qa/detectors/__init__.py` (register `curb_soft`)

**Interfaces:**
- Consumes: `imaging.side_strips`, `scan.FrameContext`, `Issue`
- Produces: `curb_soft.detect(ctx) -> list[Issue]` for labels in `{curb_road_edge, without_curb_road_edge}`. The fix is `{"op":"set_category","to":<other>}`.

The algorithm, which the tests do not fully determine:
1. Process each poly in 150 px stretches, so mixed edges are judged per stretch.
2. **Find the road side.** It's the strip that is darker, has lower saturation and has lower texture. If the two strips' gray values differ by less than 8, the stretch is "unknown" and is skipped.
3. **Score the outside strip `o`:**
   - `curb_score = +1` if `o.gray - road.gray > 25 and o.sat < 30 and o.texture < 18`
   - `soft_score = +1` if `o.green > 8 or (o.brown > 15 and o.texture > 18)`
   - Also add +0.5 to `curb_score` if the gray profile across the line shows a step of more than 30 within 4 px (a sharp kerb).
4. **Combine.** A stretch votes curb if `curb_score > soft_score`, and soft if the reverse.
5. **Flag** when at least 60% of the valid stretches vote against the label's category. Confidence = (vote share − 0.5) × 2, clipped to 0..1.
6. **Mixed edges.** If both kinds hold 40% or more of the stretches, report `hand_fix=True` with the reason "edge changes type partway; split by hand".

- [ ] **Step 1: Write failing tests** on synthetic scenes (conftest):

```python
def test_soft_label_over_concrete_flags_as_curb(scene_concrete_sidewalk_labelled_soft):
    [i] = detect(scene_concrete_sidewalk_labelled_soft)
    assert i.fix == {"op":"set_category","to":"curb_road_edge"} and i.confidence > 0.5

def test_curb_label_over_grass_flags_as_soft(scene_grass_labelled_curb): ...

def test_correct_curb_no_issue(scene_concrete_sidewalk_labelled_curb):
    assert detect(scene_concrete_sidewalk_labelled_curb) == []

def test_half_and_half_edge_is_hand_fix(scene_half_grass_half_concrete): ...
```

- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement the algorithm above.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5: Real-data run.** Run `python3 -m annotation_qa scan --exports <the 6 exports> --root local-data --out OUT --types curb_soft`. Report the counts per export, and check that the reviewer frames are covered:
  - `208199`, `207851`, `MLC 1`, `100865_201054`, `565`, `860`, `105193_204737`, `100292_200553`
  - a quick script lists which of these 8 have at least one curb_soft issue
- [ ] **Step 6: Calibration with Roshan.**
  - Sample 30 issues at random.
  - Show them as YES/NO cards in the brainstorm visual companion (Roshan's preferred format).
  - Record the answers to `OUT/calibration/curb_soft.json`.
  - If fewer than 20 of 30 are real, tune the thresholds in step 3 and repeat.
- [ ] **Step 7:** Commit (if approved): `feat(annotation-qa): curb/soft-edge detector`

### Task 6: Fix pipeline, review project and merge

**Files:**
- Create: `tools/annotation_qa/fix.py`, `tools/annotation_qa/scalabel_project.py`, `tools/annotation_qa/merge.py`, `tools/annotation_qa/tests/test_fix.py`, `tools/annotation_qa/tests/test_merge.py`, `tools/annotation_qa/tests/test_scalabel_project.py`

**Interfaces:**
- Consumes: `Issue`, `read_marks`, `data.load_export` / `save_export`, `geometry.shift_poly`
- Produces:
  - `fix.working_copy(export_stem: str, out: Path) -> Path`. Returns `OUT/work/<stem>.json`, copying it from the original on first use.
  - `fix.apply(issue_type: str, out: Path, version: int) -> FixResult(changed: dict[str, list[str]], hand: dict[str, list[str]])`, both keyed by export stem and holding lists of frame names. It:
    - applies only the issues marked `"real"` that have a fix
    - writes `OUT/work/<stem>_fix_<type>_v<N>.json` (the full working export after the fix)
    - writes `OUT/rounds/<stem>_fix_<type>_v<N>_items.json`, a subset of changed plus hand frames in export format `{frames, config}`
    - writes `OUT/rounds/<...>_changes.json`, recording per frame and label the before/after poly and category, for View B
  - `scalabel_project.create(items_file: Path, project_name: str, server: str = "http://localhost:8686") -> str`. It POSTs the multipart form to `/postProject` and returns the project URL.

    The fields to send, copied from an existing project's `project.json`:

    | Field | Value |
    |---|---|
    | `project_name` | the project name |
    | `item_type` | `image` |
    | `label_type` | `polyline2d` |
    | `page_title` | `2D Lane` |
    | `task_size` | the number of frames |
    | `instructions` | `https://doc.scalabel.ai/instructions/segmentation.html` |
    | `demo_mode` | `false` |
    | `auto_correct` | `false` |
    | `item_file` | the subset file |

    Before coding, check the exact field set the `/create` page sends in `app/src/components/create_project.tsx`. Include a categories file if the form requires one, using the six categories in `project.json` order: `center_turn_lane, solid_single_line, curb_road_edge, dashed_white_line, double_yellow_line, without_curb_road_edge`.
  - `merge.merge_reviewed(export_stem: str, reviewed_export: Path, out: Path) -> int`. Replaces the labels of each frame in the working copy whose `name` matches a frame in the reviewed export, and returns the count. It merges only into the working copy of the same `export_stem`.
  - **CLI:**
    - `python3 -m annotation_qa fix --type curb_soft --out OUT --version 1 [--create-projects]`. This prints each project URL.
    - `python3 -m annotation_qa merge --stem <stem> --reviewed <export.json> --out OUT`

- [ ] **Step 1: Write failing tests**

```python
def test_apply_only_real_marked(out_with_two_issues_one_real):
    r = apply("curb_soft", OUT, 1)
    assert r.changed == {"exportA": ["f1.png"]}

def test_originals_untouched(out_with_two_issues_one_real):
    before = sha(ORIGINAL); apply("curb_soft", OUT, 1); assert sha(ORIGINAL) == before

def test_hand_fix_frame_included_unchanged(out_with_hand_issue_marked_real):
    r = apply("position", OUT, 1)
    items = json.load(open(OUT/"rounds"/"exportA_fix_position_v1_items.json"))
    assert r.hand == {"exportA": ["f2.png"]} and items["frames"][0]["labels"] == original_labels("f2.png")

def test_shift_fix_on_curve_keeps_shape(out_with_curve_shift_issue):
    ...  # dense(fixed) within 1.5px of dense(orig)+offset*normal

def test_merge_only_touches_same_stem(two_working_copies_sharing_frame_name):
    merge_reviewed("exportA", reviewed, OUT)
    assert labels_of("exportB","shared.png") == original

def test_create_posts_expected_fields(fake_http_server):
    create(items_file, "x_fix_curb_soft_v1", server=fake_http_server.url)
    f = fake_http_server.last_fields
    assert f["auto_correct"] == "false" and f["label_type"] == "polyline2d" and "item_file" in fake_http_server.last_files
```

- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement. Ops:
  - `set_category` changes `label.category`.
  - `shift` uses `geometry.shift_poly`.
  - `delete` removes the label.
  - `drop_vertices` removes the given vertex indices. Only `L` vertices are ever listed; the detector guarantees this.

  Keep label ids unchanged.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5: Live check.** Create a project from a 1-frame items file against `localhost:8686`. Open it in the browser and confirm that the frame loads with its labels and that curves render the same as in the original project. Then delete nothing; tell Roshan the test project's name.
- [ ] **Step 6: First real round with Roshan.** Run `fix --type curb_soft --version 1 --create-projects`. Roshan reviews in Scalabel and exports. Run `merge` per stem.
- [ ] **Step 7:** Commit (if approved): `feat(annotation-qa): fix rounds, review projects and merge`

### Task 7: Detector, off position

**Files:**
- Create: `tools/annotation_qa/detectors/position.py`, `tools/annotation_qa/tests/test_position.py`, `tools/annotation_qa/tests/fixtures/position_marks_97944.json`
- Modify: `detectors/__init__.py` (register `position`)

**Interfaces:**
- Consumes: `imaging.profile`
- Produces: `position.detect(ctx) -> list[Issue]` for the painted categories `solid_single_line`, `dashed_white_line`, `double_yellow_line`, `center_turn_lane`. Road edges are excluded until a boundary score is calibrated.

The algorithm:
1. **Pick the paint.** Use the yellow ridge for `double_yellow_line` and `center_turn_lane`. Use the white ridge for `dashed_white_line`. For `solid_single_line`, use whichever channel has the larger peak at offset 0.
2. **Score each stretch.** Stretches are 20 samples long, with a stride of 10. For each one:
   - `near` = the max ridge within ±3
   - `peak`, `off` = the max ridge within ±25, and its offset
3. **Flag a stretch** when `near < 10 and peak >= 25 and abs(off) > 3`.
4. **Report one issue per label** if any stretch is flagged:
   - `anchor` = the worst stretch's centre
   - `confidence = clip((peak - near) / 80, 0, 1)`
5. **Choose the fix:**
   - If the flagged stretches have a consistent offset (std ≤ 3 px) and cover 30% or more of the line, the fix is `{"op": "shift", "offsets": ...}`. The offsets are per resampled point: the stretch offset inside flagged stretches, 0 elsewhere, smoothed with a 15-sample moving average.
   - Otherwise `hand_fix=True`, with the reason "not on paint; redraw".
6. **Dashed lines.** Ridge values are averaged over each stretch, so gaps between dashes lower `near`. For `dashed_white_line`, use the 80th percentile across the stretch's samples instead of the mean.

- [ ] **Step 1: Write failing tests**

```python
def test_flags_line_12px_off_stripe(road_with_white_stripe): ...
def test_line_on_paint_with_stronger_neighbour_not_flagged(two_stripes_line_on_weaker): ...
def test_partial_drift_found_locally(line_on_paint_then_drifts_15px): ...
def test_calibration_frame_97944(real_data):   # skip if local-data missing
    # labels from the 28_samples saved state, item 23 (fixture stores the labels + marks)
    flagged = {i.label_id for i in detect(ctx_97944)}
    assert {"AsxYZYLlosqpAAsV","68JTnfH7D5WfkC3Z","FHFFZWLKhFszCCJZ","NAJYkCK07OLlOTZF"} <= flagged
    assert not ({"4x8ZIqxTjHKtpdmw","mMZD5YRi6uKZwfyr"} & flagged)
```

Build the fixture from `local-data/scalabel/projects/28_samples_project_2026_10_15_19_00/saved/000000/2026-10-16_19-53-18.json` → `task.items[23]`:
- labels → shapes
- `pointType` of `line` maps to `L`; anything else maps to `C`
- category index → name via `task.config.categories`

- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Real-data scan with `--types position`, then a 30-issue YES/NO calibration with Roshan (same as Task 5, step 6), then `fix --type position --version 1`.
- [ ] **Step 6:** Commit (if approved): `feat(annotation-qa): local paint-position detector`

### Task 8: Detector, bad shape

**Files:** `detectors/shape.py`, `tests/test_shape.py`; register `shape`.

**Interfaces:** `shape.detect(ctx) -> list[Issue]` for all categories.

What to flag:

| Problem | Condition | Fix |
|---|---|---|
| Turn-back | At a vertex, the direction change on the dense curve, measured over 10 px each side, is above 120° | `drop_vertices` for that vertex if it is an `L` vertex; otherwise `hand_fix` |
| Self-crossing | Two non-adjacent dense segments intersect (an open line, or a closed line that isn't a simple loop) | `hand_fix` |
| Fragment | Dense length under 30 px | `{"op": "delete"}`; confidence 0.6 |

Confidence for a turn-back is `(angle - 120) / 60`.

- [ ] **Step 1: Tests:**

```python
test_zigzag_vertex_dropped    # the 98973_199454 pattern: L L(turn-back) L
test_gentle_curve_not_flagged # LCCL arc
test_tiny_fragment_deleted
test_self_cross_hand_fix
```

- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Scan, then 30-issue calibration, then a fix round.
- [ ] **Step 6:** Commit (if approved).

### Task 9: Detector, duplicates and overlaps

**Files:** `detectors/duplicates.py`, `tests/test_duplicates.py`; register `duplicates`.

**Interfaces:** `duplicates.detect(ctx) -> list[Issue]`. It looks at pairs within a frame, using a `scipy.spatial.cKDTree` over each label's resampled points.

- `overlap(A, B)` = the fraction of A's samples within 4 px of B.
- **Same category, overlap(shorter, longer) ≥ 0.8:** issue on the shorter label, with `{"op": "delete"}` and `related_label_ids=[longer]`.
- **Different categories, overlap ≥ 0.8:** `hand_fix`, with the reason "two classes on one feature".
- **Same category, 0.3 ≤ overlap < 0.8:** `hand_fix`, with the reason "overlapping pieces; join by hand".

Report each pair once.

- [ ] **Step 1: Tests:**

```python
test_exact_duplicate_shorter_deleted
test_different_class_same_place_hand_fix
test_parallel_lines_8px_apart_not_duplicate   # double-yellow vs neighbour solid
test_pair_reported_once
```

- [ ] **Steps 2–6:** Same cycle as Task 8.

### Task 10: Detector, solid ↔ dashed

**Files:** `detectors/solid_dashed.py`, `tests/test_solid_dashed.py`; register `solid_dashed`.

**Interfaces:** `solid_dashed.detect(ctx) -> list[Issue]` for `solid_single_line` and `dashed_white_line`.

1. Along the line, take the per-sample ridge at offset 0 from the chosen channel (Task 7's rule). A sample is "on" when it is 15 or more. Drop samples darker than gray 50 (shadows and cars).
2. **Dashed pattern:** at least 3 off-runs of 12 px or more, with the coefficient of variation of the on-run lengths ≤ 0.5, and an on-fraction between 0.15 and 0.75.
3. **Solid:** an on-fraction of 0.85 or more.
4. **Flag** when the pattern contradicts the label. The fix is `set_category`. Lines under 120 px are skipped, since too short to tell.

- [ ] **Step 1: Tests:**

```python
test_solid_label_over_dashes_flagged
test_dashed_label_over_solid_flagged
test_car_occluding_solid_not_dashed
test_short_line_skipped
```

- [ ] **Steps 2–6:** Same cycle as Task 8.

### Task 11: Detector, double yellow and centre turn lane structure

**Files:** `detectors/yellow_structure.py`, `tests/test_yellow_structure.py`; register `yellow_structure`.

**Interfaces:** `yellow_structure.detect(ctx) -> list[Issue]`. All issues are `hand_fix=True` except where noted.

- **Double yellow:** the yellow band width at the line is the full width at half maximum (FWHM) of the yellow profile at offset 0. Calibrate the threshold per run: compute the median FWHM of `double_yellow_line` labels and of yellow `solid_single_line` labels across all exports, and set the threshold to their midpoint.
  - A `double_yellow_line` narrower than the threshold gets reason "single yellow? check class", with fix `set_category` → `solid_single_line` at confidence 0.5.
  - A yellow `solid_single_line` wider than the threshold gets reason "double yellow?", with fix `set_category` → `double_yellow_line`.
- **Centre turn lane partner:** each `center_turn_lane` label needs another `center_turn_lane` label with a direction difference under 15° whose perpendicular distance is within ±35% of the median CTL pair distance (computed across all exports at run start). If none is found, flag "missing other side of turn lane".
- **Centre turn lane paint:** the yellow ridge must be 25 or more at the line (within ±3 px), and a second yellow ridge must lie 3–10 px to one side. If the second ridge is missing, flag "no dashed yellow beside it; is this a turn lane?".

- [ ] **Step 1: Tests:**

```python
test_wide_band_double_yellow_ok
test_narrow_single_labelled_double_flagged
test_ctl_without_partner_flagged
test_ctl_pair_ok
```

- [ ] **Steps 2–6:** Same cycle as Task 8.

### Task 12: View B, the final review of all edited frames

**Files:**
- Modify: `review_server.py` (add `GET /viewb`)
- Create: `tests/test_viewb.py`

**Interfaces:**
- Consumes: every `OUT/rounds/*_changes.json`, plus `merge` results
- Produces: `GET /viewb`, with one row per edited frame (keyed by `(export_stem, frame_name)`) sorted by change count. Each row has:
  - every change across rounds: round, issue type, label id, before → after category or "moved N px" or "deleted"
  - before/after crops for each change, rendered on demand to `OUT/viewb/` using `render.issue_crop`
  - a link to that frame's review project
  - a "draw by hand" list for hand-fix frames, with a status of done or not done. A frame counts as done once its merged labels differ from the pre-round labels.

  Also `python3 -m annotation_qa viewb --out OUT` opens it.
- [ ] **Step 1: Tests:**

```python
test_frame_changed_in_two_rounds_listed_once_with_both_changes
test_frames_sorted_by_change_count
test_hand_fix_status_done_after_merge
```

- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 4b: Calibration bar check.** Run `python3 -m annotation_qa bar --out OUT`, which lists the 8 reviewer frames:
  - `208199`, `207851`, `MLC 1`, `100865_201054`, `565`, `860`, `105193_204737`, `100292_200553`

  For each one it shows whether at least one issue of any type there is marked `real`. Report any frame that has none to Roshan before the final step.
- [ ] **Step 5: Final deliverable.** Run `python3 -m annotation_qa final --out OUT`. This copies each fully merged `OUT/work/<stem>.json` to `OUT/final/<stem>_qa_2026_10_17.json` and prints a per-export count of the changes made.
- [ ] **Step 6:** Commit (if approved).
