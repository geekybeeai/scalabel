import json
from pathlib import Path

import numpy as np
import pytest

from conftest import hpoly, line_ctx, stripe_image

from annotation_qa.detectors import REGISTRY
from annotation_qa.detectors.position import detect
from annotation_qa.geometry import dense, resample, shift_poly

FIXTURE = Path(__file__).parent / "fixtures" / "position_marks_97944.json"
REPO = Path(__file__).resolve().parents[3]


def test_registered():
    assert REGISTRY["position"] is detect


def test_flags_line_12px_off_stripe(tmp_path):
    ctx = line_ctx(tmp_path, stripe_image(), [hpoly(212)])
    [i] = detect(ctx)
    assert i.issue_type == "position" and i.label_id == "L1"
    assert i.confidence > 0.5
    assert i.fix["op"] == "shift" and i.fix["poly_index"] == 0 and not i.hand_fix
    assert abs(i.anchor[1] - 212) < 3 and 50 <= i.anchor[0] <= 350
    f = i.features
    assert f["near"] < 10 and f["peak"] >= 25 and abs(f["off"]) == 12
    assert f["channel"] == "white" and f["coverage"] > 0.9
    # applying the shift lands the line on the stripe
    poly = ctx.frame["labels"][0]["poly2d"][0]
    moved = shift_poly(poly, np.array(i.fix["offsets"]))
    assert all(abs(y - 200) < 1.5 for _, y in moved["vertices"])


def test_line_on_paint_not_flagged(tmp_path):
    assert detect(line_ctx(tmp_path, stripe_image(), [hpoly(200)])) == []


def test_small_offset_not_an_issue(tmp_path):
    assert detect(line_ctx(tmp_path, stripe_image(), [hpoly(202)])) == []


def test_line_on_paint_with_stronger_neighbour_not_flagged(tmp_path):
    img = stripe_image(stripes=((200, 170), (212, 255)))
    assert detect(line_ctx(tmp_path, img, [hpoly(200)])) == []


def test_partial_drift_gets_partial_shift(tmp_path):
    poly = {"vertices": [[50, 200], [270, 200], [280, 215], [350, 215]],
            "types": "LLLL", "closed": False}
    img = stripe_image(stripes=())
    img[198:203, 10:390] = (0, 200, 220)  # yellow paint; CTL keeps the 2-stretch minimum (R26)
    ctx = line_ctx(tmp_path, img, [poly], category="center_turn_lane")
    [i] = detect(ctx)
    assert i.anchor[0] > 270 and i.features["coverage"] < 0.3
    assert i.fix["op"] == "shift" and not i.hand_fix
    moved = shift_poly(poly, np.array(i.fix["offsets"]))
    (_, y0), _, _, (_, y3) = moved["vertices"]
    assert abs(y0 - 200) < 1.5  # the on-paint part stays put
    assert abs(y3 - 200) < 1.5  # the drifted part lands on paint


def test_single_flagged_stretch_gives_no_issue(tmp_path):
    assert detect(line_ctx(tmp_path, stripe_image(), [hpoly(212, 50, 105)])) == []


def test_inconsistent_offsets_are_hand_fix(tmp_path):
    img = stripe_image(stripes=())
    img[198:203, 10:200] = (255, 255, 255)
    img[230:235, 200:390] = (255, 255, 255)
    [i] = detect(line_ctx(tmp_path, img, [hpoly(212)]))
    assert i.hand_fix and i.fix is None and "redraw" in i.reason


def test_only_painted_categories(tmp_path):
    ctx = line_ctx(tmp_path, stripe_image(), [hpoly(212)], category="curb_road_edge")
    assert detect(ctx) == []


def test_yellow_channel_for_double_yellow(tmp_path):
    img = stripe_image(stripes=())
    img[198:203, 10:390] = (0, 200, 220)  # BGR yellow
    [i] = detect(line_ctx(tmp_path, img, [hpoly(212)], category="double_yellow_line"))
    assert i.features["channel"] == "yellow"


def test_dashed_gaps_do_not_flag_line_on_paint(tmp_path):
    img = stripe_image(stripes=())
    for x in range(10, 390, 40):  # 20 px dash, 20 px gap
        img[198:203, x:x + 20] = (255, 255, 255)
    assert detect(line_ctx(tmp_path, img, [hpoly(200)], category="dashed_white_line")) == []
    [i] = detect(line_ctx(tmp_path, img, [hpoly(208)], category="dashed_white_line"))  # R26: <=10 px
    assert i.fix["op"] == "shift"


def test_multi_poly_label_one_issue_with_worst_poly_index(tmp_path):
    ctx = line_ctx(tmp_path, stripe_image(), [hpoly(200), hpoly(212, 60, 340)])
    [i] = detect(ctx)
    assert i.fix["poly_index"] == 1


def test_calibration_frame_97944(tmp_path):
    from annotation_qa.context import FrameContext
    from annotation_qa.data import Export
    from annotation_qa.imaging import FrameImage

    fx = json.loads(FIXTURE.read_text())
    img_path = REPO / "local-data" / fx["url"]
    if not img_path.exists():
        pytest.skip("local-data image missing")
    labels = [{"id": l["id"], "category": l["category"], "attributes": {},
               "poly2d": l["poly2d"]} for l in fx["labels"]]
    frame = {"name": fx["frame_name"], "url": fx["url"], "labels": labels}
    ctx = FrameContext(Export(Path("x.json"), [frame], {}), frame,
                       FrameImage(img_path, tmp_path / "roi_cache"))  # never the real OUT
    flagged = {i.label_id for i in detect(ctx)}
    # R26 (user-calibrated): NAJYkCK07OLlOTZF (solid) has < 5 flagged stretches and is dropped
    assert set(fx["real"]) - {"NAJYkCK07OLlOTZF"} <= flagged  # shift or hand fix, either is fine
    assert not (set(fx["false_alarm"]) & flagged)


def test_peak_at_window_edge_is_not_an_issue(tmp_path):
    # the stripe is 26 px away: at offset -25 the profile is still rising, not a peak
    assert detect(line_ctx(tmp_path, stripe_image(), [hpoly(226)])) == []
    # 24 px away is a real local maximum inside the window
    [i] = detect(line_ctx(tmp_path, stripe_image(), [hpoly(224)]))
    assert abs(i.features["off"]) == 24


# ---- R26: calibrated minimums for solid / dashed ----
def _issue(cat, n, off):
    from annotation_qa.issues import Issue
    return Issue("position", "e", "f", "L1", cat, 0.5, "r", (0, 0), None, True,
                 features={"n_flagged_stretches": n, "off": off})


def test_keep_issue_rules():
    from annotation_qa.detectors.position import keep_issue
    assert not keep_issue(_issue("solid_single_line", 4, 12))
    assert keep_issue(_issue("solid_single_line", 5, 12))
    assert not keep_issue(_issue("dashed_white_line", 5, 12))
    assert keep_issue(_issue("dashed_white_line", 5, 8))
    assert not keep_issue(_issue("dashed_white_line", 4, 8))
    assert keep_issue(_issue("center_turn_lane", 2, 12))
    assert not keep_issue(_issue("double_yellow_line", 1, 12))


def test_detect_solid_needs_five_stretches(tmp_path):
    # 20-pt stretches at stride 10: x 50..105 gives few, 50..350 gives many
    short = detect(line_ctx(tmp_path, stripe_image(), [hpoly(212, 50, 200)],
                            category="solid_single_line"))
    assert short == [] or short[0].features["n_flagged_stretches"] >= 5
    [i] = detect(line_ctx(tmp_path, stripe_image(), [hpoly(212)], category="solid_single_line"))
    assert i.features["n_flagged_stretches"] >= 5


def test_detect_solid_four_stretches_dropped(tmp_path):
    from annotation_qa.detectors.position import keep_issue
    img = stripe_image(stripes=())
    img[198:203, 10:390] = (0, 200, 220)
    for x1 in range(100, 400, 5):
        r = detect(line_ctx(tmp_path, img, [hpoly(212, 50, x1)], category="center_turn_lane"))
        if r and r[0].features["n_flagged_stretches"] == 4:
            assert keep_issue(r[0])  # CTL minimum stays 2
            from dataclasses import replace
            assert not keep_issue(replace(r[0], category="solid_single_line"))
            return
    pytest.fail("no 4-stretch length found")


def test_detect_dashed_far_off_dropped_near_kept(tmp_path):
    assert detect(line_ctx(tmp_path, stripe_image(), [hpoly(212)], category="dashed_white_line")) == []
    [i] = detect(line_ctx(tmp_path, stripe_image(), [hpoly(208)], category="dashed_white_line"))
    assert abs(i.features["off"]) == 8 and i.features["n_flagged_stretches"] >= 5


def test_ctl_two_stretches_still_flagged(tmp_path):
    img = stripe_image(stripes=())
    img[198:203, 10:390] = (0, 200, 220)
    for x1 in range(70, 400, 5):
        r = detect(line_ctx(tmp_path, img, [hpoly(212, 50, x1)], category="center_turn_lane"))
        if r and r[0].features["n_flagged_stretches"] == 2:
            return
    pytest.fail("no CTL issue with exactly 2 stretches")


def test_keep_issue_matches_detect(tmp_path):
    from dataclasses import asdict
    from annotation_qa.detectors.position import keep_issue
    from annotation_qa.issues import Issue
    for cat in ("solid_single_line", "dashed_white_line", "center_turn_lane"):
        for y in (208, 212):
            for i in detect(line_ctx(tmp_path, stripe_image(), [hpoly(y)], category=cat)):
                stored = Issue(**{**asdict(i), "anchor": tuple(i.anchor)})
                assert keep_issue(stored)


def test_refilter_cli(tmp_path):
    import json as J
    from annotation_qa.__main__ import main
    from annotation_qa.issues import read_issues, write_issues
    iss = [_issue("solid_single_line", n, 12) for n in (3, 5, 6)]
    for k, i in enumerate(iss):
        i.label_id = f"L{k}"
    iss.append(_issue("dashed_white_line", 6, 14))
    iss[-1].label_id = "Ld"
    write_issues(tmp_path / "issues_position.jsonl", iss)
    keys = [i.key for i in iss]
    (tmp_path / "calibration").mkdir()
    (tmp_path / "calibration" / "sample_position.json").write_text(J.dumps({"keys": keys}))
    (tmp_path / "marks.json").write_text(J.dumps({keys[0]: "real", keys[1]: "false"}))
    assert main(["refilter", "--type", "position", "--out", str(tmp_path)]) == 0
    kept = read_issues(tmp_path / "issues_position.jsonl")
    assert [i.label_id for i in kept] == ["L1", "L2"]
    assert len(list((tmp_path / "backup").glob("issues_position.*.jsonl"))) == 1
    sample = J.loads((tmp_path / "calibration" / "sample_position.json").read_text())["keys"]
    assert sorted(sample) == sorted(i.key for i in kept)  # L1 answered, L2 topped up
