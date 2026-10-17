import numpy as np
import pytest

from conftest import GRASS, _road, edge_ctx_from_image, hpoly

from annotation_qa.detectors import REGISTRY, edge_on_paint
from annotation_qa.detectors.edge_on_paint import detect, keep_issue
from annotation_qa.geometry import dense, partial_shift, resample

SIZE = 800
ISLAND = (170, 170, 170)
YELLOW = (40, 200, 220)  # BGR


def _stripe(img, rows, x0, x1, colour=(255, 255, 255)):
    for r in rows:
        img[r, x0:x1] = colour


def _island_scene(paint_rows, x0=10, x1=SIZE - 10):
    """Raised island (concrete) above y=200 (curb at 199.5), asphalt below, a white
    edge line on the asphalt over ``paint_rows``."""
    img = _road(SIZE)
    img[10:200, x0:x1] = ISLAND
    _stripe(img, paint_rows, x0, x1)
    return img


def _ctx(tmp_path, img, polys, cat="curb_road_edge"):
    return edge_ctx_from_image(tmp_path, img, polys, cat)


def _moved(ctx, issue):
    poly = ctx.frame["labels"][0]["poly2d"][issue.fix["poly_index"]]
    P, _ = resample(dense(partial_shift(poly, np.array(issue.fix["offsets"]))))
    return P


def test_registered():
    assert REGISTRY["edge_on_paint"] is detect


@pytest.mark.parametrize("y", [206, 208])
def test_curb_on_white_line_6px_outside_the_island(tmp_path, y):
    # white line centred 6 px outside the curb (rows 204..208), asphalt gap 200..203
    ctx = _ctx(tmp_path, _island_scene(range(204, 209)), [hpoly(y, 50, SIZE - 50)])
    [i] = detect(ctx)
    assert i.issue_type == "edge_on_paint" and i.label_id == "L1" and not i.hand_fix
    assert i.fix["op"] == "shift" and i.fix["poly_index"] == 0
    f = i.features
    assert f["n_on_paint"] >= 5 and f["on_paint_frac"] >= 0.4 and f["channel"] == "white"
    assert f["road_side"] == 1  # road below (+normal of a left-to-right line)
    P = _moved(ctx, i)
    inner = P[(P[:, 0] > 150) & (P[:, 0] < SIZE - 150)]
    assert np.all(np.abs(inner[:, 1] - 199.5) <= 1.5), inner[:, 1]
    assert keep_issue(i)


def test_curb_on_paint_adjacent_to_the_island_ring(tmp_path):
    # island block with a 6 px white line right against its curb all round; the
    # closed outline is drawn on the outer edge of the paint
    img = _road(SIZE)
    img[200:600, 200:600] = ISLAND  # curb at 199.5 / 599.5
    for k in range(1, 7):
        img[200 - k, 200 - k:600 + k] = 255
        img[599 + k, 200 - k:600 + k] = 255
        img[200 - k:600 + k, 200 - k] = 255
        img[200 - k:600 + k, 599 + k] = 255
    ring = {"vertices": [[400, 194.5], [605, 194.5], [605, 605], [194.5, 605], [194.5, 194.5]],
            "types": "LLLLL", "closed": True}
    ctx = _ctx(tmp_path, img, [ring])
    [i] = detect(ctx)
    assert i.fix and i.fix["op"] == "shift"
    P = _moved(ctx, i)
    top = P[(np.abs(P[:, 1] - 200) < 10) & (P[:, 0] > 260) & (P[:, 0] < 540)]
    assert len(top) and np.all(np.abs(top[:, 1] - 199.5) <= 1.5), top[:, 1]


def test_curb_inside_the_paint_is_not_flagged(tmp_path):
    img = _island_scene(range(204, 209))
    assert detect(_ctx(tmp_path, img, [hpoly(200, 50, SIZE - 50)])) == []
    # paint right against the curb, line on the curb side of it
    img2 = _island_scene(range(200, 206))
    assert detect(_ctx(tmp_path, img2, [hpoly(200, 50, SIZE - 50)])) == []


def test_curb_crossing_a_stop_bar_and_crosswalk_is_not_flagged(tmp_path):
    img = _road(SIZE)
    img[10:200, 10:SIZE - 10] = ISLAND
    img[150:300, 100:130] = 255  # stop bar across the line
    for x in range(250, 650, 40):  # crosswalk bars across the line
        img[200:320, x:x + 18] = 255
    img[202:206, 650:780] = 255  # a short stretch of paint along the line
    assert detect(_ctx(tmp_path, img, [hpoly(200, 50, SIZE - 50)])) == []
    # a line painted diagonally across the curb
    img2 = _road(SIZE)
    img2[10:200, 10:SIZE - 10] = ISLAND
    for x in range(50, 750):
        y = 120 + int(0.2 * (x - 50))
        img2[y - 2:y + 3, x] = 255
    assert detect(_ctx(tmp_path, img2, [hpoly(200, 50, SIZE - 50)])) == []


def test_soft_edge_on_yellow_edge_line(tmp_path):
    img = _road(SIZE)
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 12, (190, SIZE - 20, 1))
    img[10:200, 10:SIZE - 10] = np.clip(np.array(GRASS) + noise, 0, 255).astype(np.uint8)
    _stripe(img, range(200, 206), 10, SIZE - 10, YELLOW)  # yellow line against the grass
    ctx = _ctx(tmp_path, img, [hpoly(204, 50, SIZE - 50)], cat="without_curb_road_edge")
    [i] = detect(ctx)
    assert i.category == "without_curb_road_edge" and i.features["channel"] == "yellow"
    P = _moved(ctx, i)
    inner = P[(P[:, 0] > 150) & (P[:, 0] < SIZE - 150)]
    assert np.all(np.abs(inner[:, 1] - 199.5) <= 1.5), inner[:, 1]
    ok = _ctx(tmp_path, img, [hpoly(200, 50, SIZE - 50)], cat="without_curb_road_edge")
    assert detect(ok) == []


def test_roi_edge_is_safe(tmp_path):
    img = _road(SIZE)
    img[:190] = 0  # black padding (outside the ROI) above y=190
    _stripe(img, range(193, 198), 10, SIZE - 10)
    for y in (195, 200):
        assert detect(_ctx(tmp_path, img, [hpoly(y, 50, SIZE - 50)])) == []


def test_lane_line_far_from_the_line_and_other_categories(tmp_path):
    img = _island_scene(range(212, 217))  # paint 14 px from the curb: not under the line
    assert detect(_ctx(tmp_path, img, [hpoly(200, 50, SIZE - 50)])) == []
    img2 = _island_scene(range(204, 209))
    assert detect(_ctx(tmp_path, img2, [hpoly(206, 50, SIZE - 50)], "solid_single_line")) == []


def test_too_few_stretches_and_keep_issue(tmp_path, monkeypatch):
    img = _island_scene(range(204, 209))
    ctx = _ctx(tmp_path, img, [hpoly(206, 50, 170)])  # 4 stretches
    assert detect(ctx) == []
    monkeypatch.setattr(edge_on_paint, "MIN_ON", 1)
    [raw] = detect(ctx)
    assert raw.features["n_on_paint"] == 4
    monkeypatch.setattr(edge_on_paint, "MIN_ON", 5)
    assert not keep_issue(raw)
    [i] = detect(_ctx(tmp_path, img, [hpoly(206, 50, SIZE - 50)]))
    i.features["on_paint_frac"] = 0.3
    assert not keep_issue(i)
    i.features["on_paint_frac"], i.features["median_off"] = 0.9, 2.5
    assert not keep_issue(i)


def test_lane_line_without_curb_beyond_is_not_flagged(tmp_path):
    # a lane line with asphalt on both sides for > 15 px: no curb to move to
    img = _road(SIZE)
    img[10:150, 10:SIZE - 10] = ISLAND
    _stripe(img, range(198, 203), 10, SIZE - 10)
    assert detect(_ctx(tmp_path, img, [hpoly(202, 50, SIZE - 50)])) == []


def test_concrete_band_as_bright_as_the_sidewalk_is_not_paint(tmp_path):
    # a bright gutter band (200) between asphalt and an equally bright sidewalk, with a
    # dark joint line behind it: the line on the band is on the curb, not on paint
    img = _road(SIZE)
    img[10:196, 10:SIZE - 10] = (200, 200, 200)
    img[196:198, 10:SIZE - 10] = (60, 60, 60)
    img[198:205, 10:SIZE - 10] = (205, 205, 205)
    assert detect(_ctx(tmp_path, img, [hpoly(202, 50, SIZE - 50)])) == []


def _with_lanes(ctx, ys):
    """Add a solid_single_line label at each y (the frame's painted lanes)."""
    for k, y in enumerate(ys):
        ctx.frame["labels"].append({"id": f"lane{k}", "category": "solid_single_line",
                                    "attributes": {}, "manualShape": True,
                                    "poly2d": [hpoly(y, 20, SIZE - 20)], "box2d": None,
                                    "box3d": None})
    return ctx


def test_median_curb_on_its_adjacent_paint(tmp_path):
    # a median (lanes on both sides): white line right against the curb, the edge drawn
    # on the road-side edge of the paint; the bright stripe sits in the road-side
    # 6..24 px strip but the road is still the darker side beyond it
    img = _island_scene(range(200, 207))
    ctx = _with_lanes(_ctx(tmp_path, img, [hpoly(208, 50, SIZE - 50)]), [320, 90])
    [i] = detect(ctx)
    assert i.features["road_side"] == 1 and i.features["divider"]
    P = _moved(ctx, i)
    inner = P[(P[:, 0] > 150) & (P[:, 0] < SIZE - 150)]
    assert np.all(np.abs(inner[:, 1] - 199.5) <= 1.5), inner[:, 1]


def test_verifier_scores_on_paint_shift(tmp_path):
    from annotation_qa import verify
    img = _island_scene(range(204, 209))
    ctx = _ctx(tmp_path, img, [hpoly(206, 50, SIZE - 50)])
    [i] = detect(ctx)

    def v(after_y):
        snap = lambda y: {"category": "curb_road_edge", "poly2d": [hpoly(y, 50, SIZE - 50)]}  # noqa: E731
        rec = {"before": snap(206), "after": snap(after_y)}
        return verify.verify_frame_change(ctx.image, i, rec, ctx.frame, "L1")

    good = v(199.5)
    assert good["verdict"] == "ok" and good["channel"] == "on_paint", good
    assert v(212)["verdict"] == "bad"  # moved the wrong way: still on / off paint toward traffic


def test_white_band_against_a_sidewalk_curb_is_not_judged(tmp_path):
    # no lanes beyond the curb (not a divider): a white stripe right against the kerb
    # may be the kerb's own top, so it is left alone
    img = _island_scene(range(200, 207))
    ctx = _with_lanes(_ctx(tmp_path, img, [hpoly(208, 50, SIZE - 50)]), [320])
    assert detect(ctx) == []


def test_lane_labels_decide_the_road_side(tmp_path):
    # brown dirt as bright as the asphalt: appearance cannot tell the road; the lane
    # label below can. White line on the asphalt 6 px from the dirt edge.
    img = _road(SIZE)
    img[10:200, 10:SIZE - 10] = (70, 100, 140)  # BGR brown, gray ~109
    _stripe(img, range(204, 209), 10, SIZE - 10)
    plain = _ctx(tmp_path, img, [hpoly(206, 50, SIZE - 50)], "without_curb_road_edge")
    assert detect(plain) == []  # side undecided
    ctx = _with_lanes(_ctx(tmp_path, img, [hpoly(206, 50, SIZE - 50)],
                           "without_curb_road_edge"), [330])
    [i] = detect(ctx)
    assert i.features["road_side"] == 1
    P = _moved(ctx, i)
    inner = P[(P[:, 0] > 150) & (P[:, 0] < SIZE - 150)]
    assert np.all(np.abs(inner[:, 1] - 199.5) <= 1.5), inner[:, 1]


def test_open_island_outline_whose_ends_meet_is_a_ring(tmp_path):
    # the island of the ring test drawn as ONE open line (ends 20 px apart, as in the
    # user's corrected median): the outside is road although no lane label says so
    img = _road(SIZE)
    img[200:600, 200:600] = ISLAND
    for k in range(1, 7):
        img[200 - k, 200 - k:600 + k] = 255
        img[599 + k, 200 - k:600 + k] = 255
        img[200 - k:600 + k, 200 - k] = 255
        img[200 - k:600 + k, 599 + k] = 255
    line = {"vertices": [[410, 194.5], [605, 194.5], [605, 605], [194.5, 605], [194.5, 194.5],
                         [390, 194.5]], "types": "LLLLLL", "closed": False}
    ctx = _ctx(tmp_path, img, [line])
    [i] = detect(ctx)
    assert i.features["divider"] and i.fix and i.fix["op"] == "shift"
    P = _moved(ctx, i)
    top = P[(np.abs(P[:, 1] - 200) < 10) & (P[:, 0] > 450) & (P[:, 0] < 540)]
    assert len(top) and np.all(np.abs(top[:, 1] - 199.5) <= 1.5), top[:, 1]


def test_crossings():
    square = {"vertices": [[0, 0], [100, 0], [100, 100], [0, 100]], "types": "LLLL",
              "closed": True}
    bow = {"vertices": [[0, 0], [100, 100], [100, 0], [0, 100]], "types": "LLLL",
           "closed": True}
    assert edge_on_paint._crossings(square) == 0 and edge_on_paint._crossings(bow) == 1
