import numpy as np
import pytest

from conftest import GRASS, _road, edge_ctx_from_image, hpoly

from annotation_qa.detectors import REGISTRY, edge_position
from annotation_qa.detectors.edge_position import detect, keep_issue
from annotation_qa.geometry import shift_poly

SIZE = 800
CONCRETE = (170, 170, 170)


def _curb_scene(paint_row=None):
    """Concrete sidewalk above y=200 (edge at 199.5), asphalt (110) below."""
    img = _road(SIZE)
    img[10:200, 10:SIZE - 10] = CONCRETE
    if paint_row is not None:  # 5 px white edge line with a bright core
        for dy, v in zip(range(-2, 3), (200, 230, 255, 230, 200)):
            img[paint_row + dy, 10:SIZE - 10] = (v, v, v)
    return img


def _ctx(tmp_path, img, y, cat="curb_road_edge", x0=50, x1=SIZE - 50):
    return edge_ctx_from_image(tmp_path, img, [hpoly(y, x0, x1)], cat)


def _moved_ys(ctx, issue):
    poly = ctx.frame["labels"][0]["poly2d"][0]
    moved = shift_poly(poly, np.array(issue.fix["offsets"]))
    return [y for _, y in moved["vertices"]]


def test_registered():
    assert REGISTRY["edge_position"] is detect


def test_curb_8px_off_is_shifted_onto_boundary(tmp_path):
    ctx = _ctx(tmp_path, _curb_scene(), 208)
    [i] = detect(ctx)
    assert i.issue_type == "edge_position" and i.label_id == "L1" and not i.hand_fix
    assert i.fix["op"] == "shift" and i.fix["poly_index"] == 0
    f = i.features
    assert f["n_flagged_stretches"] >= 5 and f["best"] >= 1.8 * max(f["near"], 0)
    assert abs(f["off"]) == pytest.approx(8.5, abs=1) and f["road_side"] == 1
    assert all(abs(y - 199.5) <= 1.5 for y in _moved_ys(ctx, i))


def test_curb_on_the_sidewalk_side_shifts_back(tmp_path):
    ctx = _ctx(tmp_path, _curb_scene(), 188)
    [i] = detect(ctx)
    assert all(abs(y - 199.5) <= 1.5 for y in _moved_ys(ctx, i))


def test_curb_on_boundary_not_flagged(tmp_path):
    assert detect(_ctx(tmp_path, _curb_scene(), 200)) == []


def test_small_offset_not_flagged(tmp_path):
    assert detect(_ctx(tmp_path, _curb_scene(), 203)) == []


@pytest.mark.parametrize("y", [210, 216])
def test_white_edge_paint_does_not_attract_the_shift(tmp_path, y):
    # paint at y=210 on the road, 8 px of asphalt between it and the curb at 199.5
    ctx = _ctx(tmp_path, _curb_scene(paint_row=210), y)
    [i] = detect(ctx)
    assert i.fix and i.fix["op"] == "shift"
    assert all(abs(yy - 199.5) <= 1.5 for yy in _moved_ys(ctx, i))


def test_curb_next_to_paint_on_boundary_not_flagged(tmp_path):
    assert detect(_ctx(tmp_path, _curb_scene(paint_row=210), 200)) == []


def test_soft_edge_asphalt_to_grass(tmp_path):
    img = _road(SIZE)
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 12, (190, SIZE - 20, 1))
    img[10:200, 10:SIZE - 10] = np.clip(np.array(GRASS) + noise, 0, 255).astype(np.uint8)
    ctx = _ctx(tmp_path, img, 209, cat="without_curb_road_edge")
    [i] = detect(ctx)
    assert i.category == "without_curb_road_edge" and i.fix["op"] == "shift"
    assert all(abs(y - 199.5) <= 2 for y in _moved_ys(ctx, i))
    ctx2 = _ctx(tmp_path, img, 200, cat="without_curb_road_edge")
    assert detect(ctx2) == []


def test_roi_edge_is_not_a_boundary(tmp_path):
    img = _road(SIZE)
    img[:190] = 0  # black padding (outside the ROI) above y=190
    assert detect(_ctx(tmp_path, img, 200)) == []
    assert detect(_ctx(tmp_path, img, 196)) == []


def test_fewer_than_five_flagged_stretches_gives_no_issue(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, _curb_scene(), 208, x0=50, x1=170)  # 4 stretches
    assert detect(ctx) == []
    monkeypatch.setattr(edge_position, "MIN_FLAGGED_STRETCHES", 1)
    [raw] = detect(ctx)
    assert raw.features["n_flagged_stretches"] == 4
    monkeypatch.setattr(edge_position, "MIN_FLAGGED_STRETCHES", 5)
    assert not keep_issue(raw)


def test_keep_issue_matches_detect(tmp_path):
    ctx = _ctx(tmp_path, _curb_scene(), 208)
    [i] = detect(ctx)
    assert keep_issue(i)
    i.features["best"] = edge_position.FLOOR - 1
    assert not keep_issue(i)


def test_inconsistent_offsets_are_hand_fix(tmp_path):
    img = _road(SIZE)
    img[10:191, 10:SIZE // 2] = CONCRETE  # edge 9.5 px above the line on the left half ...
    img[10:183, SIZE // 2:SIZE - 10] = CONCRETE  # ... and 17.5 px on the right half
    [i] = detect(_ctx(tmp_path, img, 200))
    assert i.hand_fix and i.fix is None and "redraw" in i.reason


def test_only_road_edges(tmp_path):
    assert detect(_ctx(tmp_path, _curb_scene(), 208, cat="solid_single_line")) == []


def _verdict(ctx, before_y, after_y, issue=None):
    from annotation_qa import verify
    snap = lambda y: {"category": "curb_road_edge", "poly2d": [hpoly(y, 50, SIZE - 50)]}  # noqa: E731
    rec = {"before": snap(before_y), "after": snap(after_y)}
    return verify.verify_frame_change(ctx.image, issue, rec, ctx.frame, "L1")


def test_verifier_scores_edge_shift_on_the_boundary(tmp_path):
    ctx = _ctx(tmp_path, _curb_scene(), 208)
    [i] = detect(ctx)
    v = _verdict(ctx, 208, 199.5, i)
    assert v["verdict"] == "ok" and v["channel"] == "edge", v
    assert v["after_score"] >= max(40, v["before_score"] + 20)


def test_verifier_rejects_edge_shift_off_the_boundary(tmp_path):
    ctx = _ctx(tmp_path, _curb_scene(), 208)
    v = _verdict(ctx, 208, 220)
    assert v["verdict"] == "bad", v


def test_grass_is_never_the_road_side(tmp_path):
    # strongly blue-tinted asphalt (brighter and more saturated than the grass) would
    # out-vote the grass on gray and saturation
    img = _road(SIZE)
    img[10:SIZE - 10, 10:SIZE - 10] = (170, 110, 70)
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 12, (190, SIZE - 20, 1))
    img[10:200, 10:SIZE - 10] = np.clip(np.array(GRASS) + noise, 0, 255).astype(np.uint8)
    ctx = _ctx(tmp_path, img, 209, cat="without_curb_road_edge")
    [i] = detect(ctx)
    assert i.features["road_side"] == 1
    assert all(abs(y - 199.5) <= 2 for y in _moved_ys(ctx, i))
