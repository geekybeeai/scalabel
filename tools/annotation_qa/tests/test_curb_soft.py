import numpy as np

from conftest import GRASS, ROAD, _frame, _road, edge_ctx_from_image, hpoly, make_edge_ctx

from annotation_qa.detectors import REGISTRY
from annotation_qa.detectors.curb_soft import detect


def test_registered():
    assert REGISTRY["curb_soft"] is detect


def test_soft_label_over_concrete_flags_as_curb(scene_concrete_sidewalk_labelled_soft):
    [i] = detect(scene_concrete_sidewalk_labelled_soft)
    assert i.fix == {"op": "set_category", "to": "curb_road_edge"}
    assert i.confidence > 0.5 and not i.hand_fix
    assert i.issue_type == "curb_soft" and i.label_id == "L1"
    assert 50 <= i.anchor[0] <= 350 and abs(i.anchor[1] - 200) < 3


def test_curb_label_over_grass_flags_as_soft(scene_grass_labelled_curb):
    [i] = detect(scene_grass_labelled_curb)
    assert i.fix == {"op": "set_category", "to": "without_curb_road_edge"}
    assert i.confidence > 0.5


def test_correct_curb_no_issue(scene_concrete_sidewalk_labelled_curb):
    assert detect(scene_concrete_sidewalk_labelled_curb) == []


def test_correct_soft_no_issue(scene_grass_labelled_soft):
    assert detect(scene_grass_labelled_soft) == []


def test_half_and_half_edge_is_hand_fix(tmp_path):
    [i] = detect(make_edge_ctx(tmp_path, "curb_road_edge", "grass", "concrete", size=800))
    assert i.hand_fix and i.fix is None
    assert "split by hand" in i.reason


def test_road_side_detected_either_way(scene_concrete_sidewalk_labelled_soft):
    # reversing the polyline flips left/right; the verdict must not change
    ctx = scene_concrete_sidewalk_labelled_soft
    poly = ctx.frame["labels"][0]["poly2d"][0]
    poly["vertices"] = poly["vertices"][::-1]
    [i] = detect(ctx)
    assert i.fix["to"] == "curb_road_edge"


def test_other_categories_ignored(scene_concrete_sidewalk_labelled_soft):
    ctx = scene_concrete_sidewalk_labelled_soft
    ctx.frame["labels"][0]["category"] = "solid_single_line"
    assert detect(ctx) == []


def test_multi_poly_label_gives_one_issue(scene_concrete_sidewalk_labelled_soft):
    ctx = scene_concrete_sidewalk_labelled_soft
    lab = ctx.frame["labels"][0]
    lab["poly2d"].append({"vertices": [[60, 200], [340, 200]], "types": "LL", "closed": False})
    assert len(detect(ctx)) == 1


def test_unknown_when_gray_similar_is_skipped(tmp_path):
    ctx = make_edge_ctx(tmp_path, "curb_road_edge", "concrete", "concrete")
    img = ctx.image.bgr
    img[10:200, 10:390] = (112, 112, 112)  # nearly the road gray
    assert detect(ctx) == []


def _mulch(img, rows, cols, seed=1):
    rng = np.random.default_rng(seed)
    h, w = img[rows, cols].shape[:2]
    base = np.array((60, 85, 110))  # BGR: dark brown mulch, sat ~50
    img[rows, cols] = np.clip(base + rng.normal(0, 25, (h, w, 1)), 0, 255).astype(np.uint8)


def _ring(x0, y0, x1, y1, reverse=False):
    v = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    return {"vertices": v[::-1] if reverse else v, "types": "LLLL", "closed": True}


def test_planter_behind_curb_band_is_not_soft(tmp_path):
    img = _road()
    _mulch(img, slice(10, 195), slice(10, 390))
    img[195:200, 10:390] = (190, 190, 190)  # 5 px bright curb band
    assert detect(edge_ctx_from_image(tmp_path, img, [hpoly(200)])) == []


def test_mulch_without_curb_band_is_soft(tmp_path):
    img = _road()
    _mulch(img, slice(10, 200), slice(10, 390))
    [i] = detect(edge_ctx_from_image(tmp_path, img, [hpoly(200)]))
    assert i.fix["to"] == "without_curb_road_edge"


def test_closed_island_with_curb_band_not_flagged(tmp_path):
    for reverse in (False, True):
        img = _road()
        _mulch(img, slice(100, 300), slice(100, 300))
        for sl in ((slice(100, 106), slice(100, 300)), (slice(294, 300), slice(100, 300)),
                   (slice(100, 300), slice(100, 106)), (slice(100, 300), slice(294, 300))):
            img[sl] = (190, 190, 190)
        ctx = edge_ctx_from_image(tmp_path, img, [_ring(100, 100, 300, 300, reverse)])
        assert detect(ctx) == [], reverse


def test_closed_island_with_grass_at_grade_flagged(tmp_path):
    for reverse in (False, True):
        img = _road()
        _outside(img, slice(100, 300), slice(100, 300))
        ctx = edge_ctx_from_image(tmp_path, img, [_ring(100, 100, 300, 300, reverse)])
        [i] = detect(ctx)
        assert i.fix["to"] == "without_curb_road_edge", reverse


def _outside(img, rows, cols):
    rng = np.random.default_rng(0)
    h, w = img[rows, cols].shape[:2]
    img[rows, cols] = np.clip(np.array(GRASS) + rng.normal(0, 12, (h, w, 1)), 0, 255).astype(np.uint8)


def test_pink_painted_island_never_soft(tmp_path):
    img = _road()
    rng = np.random.default_rng(3)
    pink = np.array((150, 60, 220)) + rng.normal(0, 14, (200, 200, 1))
    img[100:300, 100:300] = np.clip(pink, 0, 255).astype(np.uint8)
    assert detect(edge_ctx_from_image(tmp_path, img, [_ring(100, 100, 300, 300)])) == []


def test_deep_shadow_stretch_is_skipped(tmp_path):
    img = _road()
    img[10:390, 10:390] = (45, 45, 45)
    _outside(img, slice(10, 200), slice(10, 390))
    img[10:200, 10:390] = (img[10:200, 10:390] * 0.6).astype(np.uint8)
    assert detect(edge_ctx_from_image(tmp_path, img, [hpoly(200)])) == []


def test_mixed_rule_needs_four_votes(tmp_path):
    # 400 px canvas -> 2 stretches (1 curb, 1 soft): neither flag nor hand_fix
    assert detect(make_edge_ctx(tmp_path, "curb_road_edge", "grass", "concrete", size=400)) == []


def test_step_ignores_black_padding_near_roi_edge(tmp_path):
    from annotation_qa.detectors.curb_soft import _sharp_step, _stretches

    img = _road()
    img[10:197, 10:390] = 0  # ROI ends 3 px outside the line
    ctx = edge_ctx_from_image(tmp_path, img, [hpoly(200)])
    sub, _ = _stretches(hpoly(200))[0]
    assert not _sharp_step(ctx, sub, side=1)


def test_step_detects_real_kerb(tmp_path):
    from annotation_qa.detectors.curb_soft import _sharp_step, _stretches

    img = _road()
    img[10:199, 10:390] = (170, 170, 170)
    ctx = edge_ctx_from_image(tmp_path, img, [hpoly(200)])
    sub, _ = _stretches(hpoly(200))[0]
    assert _sharp_step(ctx, sub, side=1)


def test_magenta_tinted_ground_is_not_dirt(tmp_path):
    # washed-out pink paint (R-B 40, sat 50, G below (R+B)/2) next to open asphalt
    img = _road()
    rng = np.random.default_rng(4)
    pink = np.array((130, 120, 170)) + rng.normal(0, 18, (190, 380, 1))
    img[10:200, 10:390] = np.clip(pink, 0, 255).astype(np.uint8)
    assert detect(edge_ctx_from_image(tmp_path, img, [hpoly(200)])) == []


def test_bright_concrete_is_not_taken_for_road(tmp_path):
    img = _road()
    img[10:390, 10:390] = (215, 215, 215)  # bright walkway as the 'road' side
    _mulch(img, slice(10, 200), slice(10, 390))
    assert detect(edge_ctx_from_image(tmp_path, img, [hpoly(200)])) == []


def test_single_voting_stretch_gives_no_issue(tmp_path):
    # 200 px line = one 150 px+ stretch: grass beyond a curb label is not enough evidence
    img = _road()
    _outside(img, slice(10, 200), slice(10, 390))
    ctx = edge_ctx_from_image(tmp_path, img, [hpoly(200, 100, 280)])
    assert detect(ctx) == []


def test_confidence_scales_with_votes(tmp_path):
    img = _road()
    _outside(img, slice(10, 200), slice(10, 390))
    [two] = detect(edge_ctx_from_image(tmp_path, img, [hpoly(200, 50, 350)]))
    assert two.confidence <= 0.5 + 1e-9


def _split_ctx(tmp_path, left_votes):
    """800 px curb-labelled edge: concrete under the first ``left_votes`` x-bands, grass after."""
    img = _road(800)
    cut = 50 + 150 * left_votes
    img[10:200, 10:cut] = (170, 170, 170)
    _outside(img, slice(10, 200), slice(cut, 790))
    return edge_ctx_from_image(tmp_path, img, [hpoly(200, 50, 750)])


def test_three_one_split_is_flagged_not_hand_fix(tmp_path):
    # 1 concrete + 3 grass stretches on a curb label -> flag (flag rule before mixed rule)
    [i] = detect(_split_ctx(tmp_path, 1))
    assert not i.hand_fix and i.fix["to"] == "without_curb_road_edge"


def test_outside_strip_shadow_is_skipped(tmp_path):
    img = _road()
    _outside(img, slice(10, 200), slice(10, 390))
    img[10:200, 10:390] = (img[10:200, 10:390] * 0.3).astype(np.uint8)  # outside gray < 60
    assert detect(edge_ctx_from_image(tmp_path, img, [hpoly(200)])) == []


def test_clay_dirt_is_soft(tmp_path):
    img = _road()
    rng = np.random.default_rng(5)
    clay = np.array((100, 112, 150)) + rng.normal(0, 22, (190, 380, 1))  # reddish clay, green -13
    img[10:200, 10:390] = np.clip(clay, 0, 255).astype(np.uint8)
    [i] = detect(edge_ctx_from_image(tmp_path, img, [hpoly(200, 50, 350)]))
    assert i.fix["to"] == "without_curb_road_edge"


def test_painted_edge_line_is_not_a_kerb_step(tmp_path):
    from annotation_qa.detectors.curb_soft import _sharp_step, _stretches

    img = _road()
    img[196:200, 10:390] = (230, 230, 230)  # 4 px white paint just outside the line, road beyond
    img[10:196, 10:390] = ROAD
    ctx = edge_ctx_from_image(tmp_path, img, [hpoly(200)])
    sub, _ = _stretches(hpoly(200))[0]
    assert not _sharp_step(ctx, sub, side=1)



def test_features_report_votes(scene_concrete_sidewalk_labelled_soft):
    [i] = detect(scene_concrete_sidewalk_labelled_soft)
    f = i.features
    assert f["n_votes"] >= 2 and f["curb_votes"] == f["n_votes"] and f["soft_votes"] == 0
    assert f["share"] == 1.0
    for k in ("outside_gray", "outside_sat", "outside_green", "outside_brown",
              "outside_texture", "near_band_votes", "step_votes"):
        assert k in f
