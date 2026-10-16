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
    ctx = line_ctx(tmp_path, stripe_image(), [poly])
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
    [i] = detect(line_ctx(tmp_path, img, [hpoly(212)], category="dashed_white_line"))
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
    assert set(fx["real"]) <= flagged  # shift or hand fix, either is fine
    assert not (set(fx["false_alarm"]) & flagged)


def test_peak_at_window_edge_is_not_an_issue(tmp_path):
    # the stripe is 26 px away: at offset -25 the profile is still rising, not a peak
    assert detect(line_ctx(tmp_path, stripe_image(), [hpoly(226)])) == []
    # 24 px away is a real local maximum inside the window
    [i] = detect(line_ctx(tmp_path, stripe_image(), [hpoly(224)]))
    assert abs(i.features["off"]) == 24
