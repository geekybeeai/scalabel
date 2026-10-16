import numpy as np

from conftest import edge_ctx_from_image, _road

from annotation_qa.detectors import REGISTRY
from annotation_qa.detectors.shape import detect


def ctx_for(tmp_path, polys, category="without_curb_road_edge"):
    return edge_ctx_from_image(tmp_path, _road(), polys, category)


def poly(verts, types=None, closed=False):
    return {"vertices": verts, "types": types or "L" * len(verts), "closed": closed}


def test_registered():
    assert REGISTRY["shape"] is detect


def test_zigzag_vertex_dropped(tmp_path):
    # L L(turn-back, ~160 deg) L : the 98973_199454 pattern
    p = poly([[50, 100], [250, 100], [100, 120]])
    [i] = detect(ctx_for(tmp_path, [p]))
    assert i.issue_type == "shape" and i.label_id == "L1"
    assert i.fix == {"op": "drop_vertices", "poly_index": 0, "indices": [1]}
    assert not i.hand_fix
    assert i.features["kind"] == "turn_back" and 140 < i.features["angle"] < 175
    assert abs(i.confidence - (i.features["angle"] - 120) / 60) < 1e-6
    assert abs(i.anchor[0] - 250) < 12 and abs(i.anchor[1] - 100) < 12


def test_gentle_curve_not_flagged(tmp_path):
    p = poly([[50, 200], [120, 120], [220, 120], [290, 200]], "LCCL")
    assert detect(ctx_for(tmp_path, [p])) == []


def test_right_angle_and_closed_rectangle_not_flagged(tmp_path):
    corner = poly([[50, 50], [200, 50], [200, 200]])
    rect = poly([[50, 50], [200, 50], [200, 200], [50, 200]], closed=True)
    assert detect(ctx_for(tmp_path, [corner])) == []
    assert detect(ctx_for(tmp_path, [rect])) == []


def test_tiny_fragment_deleted(tmp_path):
    [i] = detect(ctx_for(tmp_path, [poly([[100, 100], [118, 100]])]))
    assert i.fix == {"op": "delete"} and not i.hand_fix
    assert i.confidence == 0.6 and i.features["kind"] == "fragment"
    assert i.features["length"] < 30


def test_line_of_30px_not_fragment(tmp_path):
    assert detect(ctx_for(tmp_path, [poly([[100, 100], [135, 100]])])) == []


def test_self_cross_hand_fix(tmp_path):
    bow = poly([[50, 50], [250, 150], [250, 50], [50, 150]])  # X shape
    [i] = detect(ctx_for(tmp_path, [bow]))
    assert i.hand_fix and i.fix is None
    assert i.features["kind"] == "self_cross"
    assert abs(i.anchor[0] - 150) < 3 and abs(i.anchor[1] - 100) < 3


def test_closed_figure_eight_flagged_simple_ring_not(tmp_path):
    eight = poly([[50, 50], [250, 150], [250, 50], [50, 150]], closed=True)
    [i] = detect(ctx_for(tmp_path, [eight]))
    assert i.features["kind"] == "self_cross"


def test_turn_back_on_bezier_end_is_hand_fix(tmp_path):
    # curve whose end vertex is a sharp reversal into an L segment
    p = poly([[50, 200], [100, 150], [150, 150], [200, 200], [60, 215]], "LCCLL")
    [i] = detect(ctx_for(tmp_path, [p]))
    assert i.features["kind"] == "turn_back" and i.hand_fix and i.fix is None


def test_most_severe_per_label_one_issue(tmp_path):
    bow = poly([[50, 50], [250, 150], [250, 50], [50, 150]])
    zig = poly([[50, 300], [250, 300], [100, 320]])
    out = detect(ctx_for(tmp_path, [bow, zig]))
    assert len(out) == 1 and out[0].features["kind"] == "self_cross"
    assert "turn_back" in out[0].reason or "turn-back" in out[0].reason


def test_drop_index_refers_to_raw_vertices_with_curve(tmp_path):
    # L C C L L(turn-back) L : the turn-back vertex is raw index 4
    p = poly([[40, 200], [80, 150], [120, 150], [160, 200], [300, 200], [180, 215]], "LCCLLL")
    [i] = detect(ctx_for(tmp_path, [p]))
    assert i.fix == {"op": "drop_vertices", "poly_index": 0, "indices": [4]}
