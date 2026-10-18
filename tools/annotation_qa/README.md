# annotation_qa: find and fix lane / road-edge label mistakes

Spec: `docs/superpowers/specs/2026-10-17-annotation-qa-design.md`. Run everything from `tools/`:

```bash
cd tools
OUT=../local-data/items/Full_Verfication/qa_2026_10_17
EXP=../local-data/items/Full_Verfication/Annotation_Reviewed_by_Roshan/2026_10_15_export
```

The originals in `$EXP` and the existing Scalabel projects are never written. Fixes go to copies in `$OUT/work/`.

## 1. Scan (find, no edits)

```bash
python3 -m annotation_qa calibrate-yellow --exports $EXP --out $OUT   # once, BEFORE yellow_structure
python3 -m annotation_qa scan --exports $EXP --out $OUT \
  --types curb_soft,position,shape,duplicates,solid_dashed,yellow_structure   # about 2.7 h for all
```

- A scan into `$OUT` replaces that type's results: `issues_<type>.jsonl`. While it runs it writes `issues_<type>.jsonl.partial`, and it only replaces the old file when it finishes, so an interrupted run keeps the old results.
- `--subset reviewer+random20` (the 8 reviewer frames plus 20 random frames per export) needs its own `--out`.
- `--workers N` detects frames in N processes (each loads its own image; about 1 GB each for the largest frames). Issues, crops and the summary are the same as with 1 worker. The full `edge_position` scan takes about 3.5 min with 6 workers.
- The exit code is 1 when the scan logged errors. The errors are printed and also kept in `$OUT/scan_summary.json`; its `counts` keep the last count of every type not scanned in this run.

## 2. Triage one type

```bash
python3 -m annotation_qa review --out $OUT        # http://localhost:8790/
```

1. Open `/calibrate?type=T` and answer YES/NO for the fixed sample of 30 (drawn once and kept in `calibration/sample_<type>.json`, so a later `scan --since-merge` does not change it). Each tab and the calibrate page show `answered k/n, real r, precision p` and **PASS / FAIL / NOT CALIBRATED**.
   - PASS means precision of at least 2/3 with at least 20 answered. A type with fewer than 20 issues needs all of them answered.
2. On the type's tab, mark cards **Real** or **False alarm**. You can also bulk-mark everything at or above a confidence as real; the page asks you to confirm the count first.
3. Only issues marked real are fixed.
   Every card, the calibrate cards and the zoom lightbox also have a comment box (autosaved to `$OUT/comments.json`); read them at `/comments/view` or with `python3 -m annotation_qa comments --out $OUT`.

## 3. Fix, then review in Scalabel

```bash
python3 -m annotation_qa fix --type T --version 1 --out $OUT --create-projects
```

- This writes `$OUT/work/<stem>_fix_T_v1.json`, `$OUT/rounds/<stem>_fix_T_v1_{items,changes}.json`, and one Scalabel project per batch, named `<stem>_fix_T_v1` (with auto-correct off and task size 50).
- A project only holds the frames that changed, plus "draw by hand" frames, which are left unchanged for you to fix.
- `fix` **refuses** and writes nothing when:
  - the type is not PASS;
  - an earlier round of that batch is not merged;
  - the round tag already exists (use the next `--version`);
  - the issues were detected on an older working copy (re-scan first, step 5).

  `--force` overrides each of these.
- If creating a project fails, the server's answer is printed and the other batches carry on. Re-create one with `python3 -m annotation_qa project --round <stem>_fix_T_v1 --out $OUT`. Names that already exist are skipped.

Review each project at `http://localhost:8686/dashboard?project_name=<stem>_fix_T_v1`. Fix what is wrong, then export the project's labels to a JSON file.

After `verify`, `python3 -m annotation_qa exclude --round <tag> --verdict bad` records the verifier-rejected auto-fixes in `OUT/excluded.json`. The next `fix --version <N+1>` skips them (listed as skipped, their frames stay out of the round unless another fix touches them) and View A marks them "auto-fix rejected", so you don't undo them by hand.

### Combined rounds (review each frame once)

```
python3 -m annotation_qa fix --type curb_soft,edge_on_paint --version 1 [--name edges] --out $OUT
```

- Several types in one round, tagged `<stem>_fix_<name>_v<N>` (`--name` defaults to the types joined by `+`, e.g. `..._fix_edges_v1`). Every listed type must PASS its calibration (or `--force`); the guards run per stem over the whole round. Only issues marked real are applied.
- Per label the fixes are composed: set_category first, then the geometry fix. A delete wins over everything and is recorded alone. Two geometry fixes of different types on one label keep the higher-confidence one; the other is listed as skipped ("conflicting geometry fix"). Inside one type the old "first fix by confidence only" rule still holds.
- `changes.json` records `issue_types` and `ops` (parallel lists) next to before/after/anchor; single-type rounds also keep `issue_type`.
- `verify` gives a combined change the worst verdict of its parts. View B shows the ops ("soft edge → CURB + moved 6 px") and draws the before side in its old class colour and the after side in its new one.
- `project --round <tag>` and `merge` work as for any round.

## 4. Merge each batch's reviewed export

```bash
python3 -m annotation_qa merge --stem <stem> --reviewed <exported.json> --round <stem>_fix_T_v1 --out $OUT
```

`merge` refuses, and writes nothing, unless the export's frames and urls are exactly that round's frames. The Random and TMI batches share frame names, so this check matters. Your export replaces those frames' labels in `$OUT/work/<stem>.json`.

## 5. Re-detect on the merged copy, then do the next type

```bash
python3 -m annotation_qa scan --since-merge --exports $EXP --out $OUT --types <next types>
```

- This only re-scans the frames merged since each type's last scan (minutes, not hours). It replaces just those frames' issues and records the new working-copy sha1, so `fix` accepts them.
- Then go back to step 2 for the next type. Order of the spec: curb_soft, position, shape, duplicates, solid_dashed, yellow_structure.

## 6. Final review and deliverable

```bash
python3 -m annotation_qa bar --out $OUT                 # each reviewer frame has a real-marked issue?
python3 -m annotation_qa viewb --out $OUT --prerender   # http://localhost:8790/viewb
python3 -m annotation_qa final --out $OUT               # $OUT/final/<stem>_qa_2026_10_17.json
```

- View B lists every edited frame. For merged rounds, "after" is what will ship.
- View B **Revert** (button, or `X` on the current/focused change) marks a tool change to undo without opening Scalabel. Nothing is written to Scalabel: marks go to `OUT/reverts.json` and `merge` applies them. A label still equal to the tool's result gets the original category and geometry back (a tool-deleted label is re-added); a label you edited or deleted in Scalabel keeps your version (warning printed). The merge marker records `reverted_applied` / `reverted_conflicts`; `final` counts applied reverts as no change. Reverting is only possible before the round is merged.
- View B shows where you are: each card says "Image i of N" (position in the current round/filter/order) and "Scalabel frame k" ("task t, frame k" for multi-task projects), and a sticky bar shows "Reviewed r / N" with a progress bar. `J` marks the current frame reviewed and moves on (or tick "reviewed" on the card); `U` / **Resume** jumps to the first unreviewed image; "show: not yet reviewed" hides the rest. Progress is kept in `OUT/viewb_progress.json` (`POST /viewb/progress`, `GET /viewb/progress?round=`); merged rounds always count as reviewed.
- It flags "changed in review" or "reverted", and lists labels added or deleted in review.
- `final` writes one export per batch:
  - batches with no rounds are copied as they are (0 changes);
  - a batch with an unmerged round is left out, with a loud `NOT EXPORTED` message.

## Review UI: lightbox, keys, prerender, deep link

- View A groups cards by frame (most issues first) under a header with a numbered overview of the whole frame; every card has a locator thumbnail. `/calibrate` keeps its fixed order.
- Click a crop to open the lightbox (high-detail render from `/zoom`, cached in `OUT/zoom/`). `Esc` closes, left/right arrows move, `R` toggles raw only, `O` opens the issue in Scalabel. On `/calibrate`, `Y` = YES and `N` = NO, with or without the lightbox, then it advances.
- Ahead of time: `python3 -m annotation_qa prerender-zoom --out $OUT [--calibration-only]` (one image decode per frame; also makes the locator thumbnails).
- "Open in Scalabel" opens a new tab with `&item=<frame in task>&label=<label id>`; a Scalabel build that does not know these params ignores them.

## Notes

- This branch imports the **untracked** `scalabel/tools/draw_poly2d.py` (it holds the curve sampling). Keep that file.
- **The yellow width check is off on this data.** `calibration/yellow_stats.json` has `width_usable=false`: the double-yellow median FWHM is 10 px against 8 px for single yellow (threshold 9). The centre-turn-lane pair-distance median is 59.6 px.
- The test project `zz_annotation_qa_test_1frame` was created in Scalabel during the build. You can delete it.
- The full scan of 2026-10-17 started before scan metadata existed, so it wrote no `issues_<type>.meta.json` files.
  - `fix` treats those issues as detected on the originals. That is correct until the first merge.
  - After you merge, run step 5 before fixing the next type.
  - Position issues of that scan whose peak is exactly 25 px off sit at the edge of the search window and are not real peaks. Newer scans drop them. The controller prunes the old ones from `issues_position.jsonl`.
- The position fix moves only the flagged part of a line. It adds break vertices, splits curves where needed, and checks that the result is within 3.5 px of the paint. When that check fails, the frame goes to the round as "draw by hand". On the full scan of 2026-10-17 this happened for 5 of 1,005 shift fixes. The old shift missed its target by more than 2 px on 998 of them.

## What each check flags, and its known false alarms

- **curb_soft**: curb vs soft edge, judged from the strip just outside the edge (a near concrete band or a sharp step means curb; dirt or grass means soft). About 1/3 to 1/2 of soft→curb flags are real. Beige sand, the back edge of a sidewalk and concrete aprons at grade read as curb. Planters, islands, rings and deep shadow are handled, but check them.
- **curb_soft_split**: a road edge that is part curb and part soft edge. Each open edge line is judged in 90 px stretches. The road side is set once per line, from the lane lines nearby or else by majority. A stretch model turns the curb_soft vote and the across-line appearance into P(curb). Runs of the opposite class are kept when they cover at least 25% of the line and at least 200 px, and the label's own class keeps at least 200 px. A line model then gates the candidate on run contrast, length and the CAD green length and share. The fix is `{"op":"split","poly_index","cuts":[arc px],"categories":[...]}`. The first piece keeps the label id. The other pieces get new 16-character ids. All pieces are L polylines that share their cut points. Both models are fitted on Roshan's curb_soft_v2 review and stored in `detectors/curb_soft_split_model.json`. Re-run or refit with `python3 -m annotation_qa.eval.split_eval [--write-model]`, which writes `OUT/eval/curb_soft_split_report.md`. Measured out of fold, the best setting reaches only about 47% precision at 5% recall (17 proposals, 8 right); no setting reaches 70%. Image evidence alone barely separates his curb and soft pieces (stretch AUC about 0.66), so verify marks every split "check".
- **edge_position**: `curb_road_edge` / `without_curb_road_edge` that are not on the road boundary (asphalt -> curb, or asphalt -> dirt/grass). Per 60 px stretch it finds the boundary nearest the road within ±20 px (painted lines on the road are ignored, occluded stretches skipped) and flags it when that boundary is at least 1.8x stronger than the one under the line and more than 3 px away. It needs at least 5 flagged stretches in runs of 3 or more. Offsets that disagree, or a shift `partial_shift` cannot represent, become "draw by hand". The shift verdict scores the boundary strength under the line, not paint. Known: on the 2026-10-17 scan about 3/4 of the flags are lines on the BACK edge of a 4-12 px concrete curb/gutter band; the shift moves them to the asphalt seam. Refilter on `features.median_off` if that is not wanted. Lines closer than about 8 px to a boundary are not flagged.
- **edge_on_paint**: a `curb_road_edge` / `without_curb_road_edge` drawn ON a painted white/yellow line instead of the curb beyond it (the user's rule: on a road divider the outline goes INSIDE the white lines). Per 60 px stretch: paint ridge >= 25 at the line (or up to 4 px beyond it), running along the line (crosswalks / stop bars are skipped), the line not already on the curb side of the paint, the stripe at most 9 px wide and 30 whiter than what lies beyond (else it is a concrete curb band), and a curb beyond it (the stripe's far edge, or the first non-road cells past an asphalt gap). The road side comes from the ring (an island outline, also an open one whose ends almost meet), else the lane-line labels, else two agreeing appearance cues. Off a divider (no lanes on the far side) a white stripe right against the curb is not judged. Reported with >= 5 such stretches, >= 40 % of the judged ones and a median move > 4 px. Consistent moves become a `shift` onto the curb (refused when the moved outline would fold over itself), the rest "draw by hand". The shift verdict scores how much of the moved part is still on paint and that it moved away from the road.
- **position**: painted lines only; road edges are checked by `edge_position`. It flags only when at least 2 stretches of about 60 px have no paint under the line (ridge below 10) and a stripe (ridge 25 or more) lies within ±25 px. It misses drifts shorter than 2 stretches. Offsets that disagree between stretches become "draw by hand".
- **shape**: spikes (turn-backs with an arm under 25 px), self-crossings, and painted fragments under 30 px with no paint under them. Long-armed V gore tips are treated as valid. Self-crossing can flag intentional out-and-back outlines.
- **duplicates**: two labels within 4 px and running the same direction (within about 20°). It misses duplicates that cross at a larger angle. In a chain, the longest piece is kept.
- **solid_dashed**: a solid label over dashes (white only) or a dashed label over continuous paint. It needs at least 4 dashes and skips lines that are more than 25% unknown or have dark gaps. Junction and stop-bar gaps are the usual false "dashed". Yellow solid/dashed mix-ups are never flagged.
- **yellow_structure**: a centre turn lane with no partner on the other side, or no dashed yellow beside it (judged only when the line is on yellow paint). The double-yellow width check is off (see Notes). Very faded yellow is not flagged.
