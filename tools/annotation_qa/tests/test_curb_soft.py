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


def test_half_and_half_edge_is_hand_fix(scene_half_grass_half_concrete):
    [i] = detect(scene_half_grass_half_concrete)
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
    from conftest import make_edge_ctx

    ctx = make_edge_ctx(tmp_path, "curb_road_edge", "concrete", "concrete")
    img = ctx.image.bgr
    img[10:200, 10:390] = (112, 112, 112)  # nearly the road gray
    assert detect(ctx) == []
