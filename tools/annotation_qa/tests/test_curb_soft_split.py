import numpy as np

from conftest import GRASS, _road, edge_ctx_from_image, hpoly, make_edge_ctx

from annotation_qa.detectors import REGISTRY
from annotation_qa.detectors import curb_soft_split as css
from annotation_qa.detectors.curb_soft_split import (VOTE_KEYS, SplitParams, detect,
                                                     line_road_side, load_model,
                                                     stretch_features, stretch_votes)

# a transparent stand-in for the fitted models: P(curb) follows the curb_soft vote, and the
# line gate passes every candidate -- so these tests check the run / cut mechanics
TOY = {"stretch": {"names": ["vote_curb", "vote_soft"], "mean": [0.0, 0.0], "std": [1.0, 1.0],
                   "w": [0.0, 5.0, -5.0]},
       "line": {"names": ["contrast"], "mean": [0.0], "std": [1.0], "w": [5.0, 0.0]}}
P = SplitParams(t_run=0.5, t_line=0.5)


def test_registered():
    assert REGISTRY["curb_soft_split"] is detect


def _grass(img, rows, x0, x1):
    rng = np.random.default_rng(0)
    h = img[rows, x0:x1].shape[0]
    img[rows, x0:x1] = np.clip(np.array(GRASS) + rng.normal(0, 12, (h, x1 - x0, 1)),
                               0, 255).astype(np.uint8)


def test_half_grass_half_concrete_one_cut_near_boundary(tmp_path):
    for size in (800, 1600):
        ctx = make_edge_ctx(tmp_path, "curb_road_edge", "grass", "concrete", size=size)
        [i] = detect(ctx, P, TOY)
        assert i.issue_type == "curb_soft_split" and i.label_id == "L1" and not i.hand_fix
        f = i.fix
        assert f["op"] == "split" and f["poly_index"] == 0
        assert f["categories"] == ["without_curb_road_edge", "curb_road_edge"]
        boundary = size / 2 - 50  # arc length of x = size/2 along the line from x = 50
        assert len(f["cuts"]) == 1 and abs(f["cuts"][0] - boundary) <= P.stretch_px
        assert abs(i.anchor[1] - 200) < 1 and abs(i.anchor[0] - size / 2) <= P.stretch_px
        assert [pc["category"] for pc in i.features["pieces"]] == f["categories"]


def test_reversed_line_gives_mirrored_split(tmp_path):
    ctx = make_edge_ctx(tmp_path, "curb_road_edge", "grass", "concrete", size=800)
    poly = ctx.frame["labels"][0]["poly2d"][0]
    poly["vertices"] = poly["vertices"][::-1]
    [i] = detect(ctx, P, TOY)
    assert i.fix["categories"] == ["curb_road_edge", "without_curb_road_edge"]
    assert abs(i.fix["cuts"][0] - 350) <= P.stretch_px


def test_short_opposite_run_ignored(tmp_path):
    img = _road(1600)
    img[10:200, 10:1590] = (170, 170, 170)  # concrete sidewalk ...
    _grass(img, slice(10, 200), 700, 830)  # ... with a 130 px grass patch
    ctx = edge_ctx_from_image(tmp_path, img, [hpoly(200, 50, 1550)])
    assert detect(ctx, P, TOY) == []


def test_whole_line_opposite_is_not_a_split(tmp_path):
    ctx = make_edge_ctx(tmp_path, "curb_road_edge", "grass", "grass", size=800)
    assert detect(ctx, P, TOY) == []  # a whole-label class change is curb_soft's job


def test_soft_label_with_concrete_part(tmp_path):
    ctx = make_edge_ctx(tmp_path, "without_curb_road_edge", "grass", "concrete", size=800)
    [i] = detect(ctx, P, TOY)
    assert i.fix["categories"] == ["without_curb_road_edge", "curb_road_edge"]


def test_line_road_side_from_lane_lines(tmp_path):
    ctx = make_edge_ctx(tmp_path, "curb_road_edge", "concrete", "concrete", size=800)
    lab = ctx.frame["labels"][0]
    poly = lab["poly2d"][0]
    assert line_road_side(ctx.frame, lab, poly) is None  # no lane lines
    lane = {"id": "LN", "category": "dashed_white_line", "attributes": {},
            "poly2d": [hpoly(300, 50, 750)]}
    ctx.frame["labels"].append(lane)
    assert line_road_side(ctx.frame, lab, poly) == 1  # road below: the +normal side
    poly["vertices"] = poly["vertices"][::-1]
    assert line_road_side(ctx.frame, lab, poly) == 0


def test_shipped_model_matches_features(tmp_path):
    model = load_model()
    assert model is not None and set(model) >= {"stretch", "line", "params"}
    ctx = make_edge_ctx(tmp_path, "curb_road_edge", "grass", "concrete", size=800)
    st = stretch_votes(ctx, ctx.frame["labels"][0]["poly2d"][0], 90, features=True)
    keys = set(st[0]["f"]) | set(VOTE_KEYS)
    assert set(model["stretch"]["names"]) <= keys
    assert set(model["line"]["names"]) <= set(css.LINE_KEYS)
    assert css.params_from_model(model).stretch_px == model["params"]["stretch_px"]
    assert stretch_features(ctx, {"vertices": [[60, 200], [61, 200]], "types": "LL",
                                  "closed": False}) is None  # too short to judge
    detect(ctx)  # the shipped model runs end to end
