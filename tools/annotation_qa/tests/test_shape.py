import json
from dataclasses import replace

import numpy as np

from conftest import edge_ctx_from_image, _road, stripe_image

from annotation_qa.detectors import REGISTRY
from annotation_qa.detectors.shape import _detect, detect, keep_issue
from annotation_qa.issues import Issue, read_issues, write_issues


def ctx_for(tmp_path, polys, category="without_curb_road_edge", img=None):
    return edge_ctx_from_image(tmp_path, _road() if img is None else img, polys, category)


def poly(verts, types=None, closed=False):
    return {"vertices": verts, "types": types or "L" * len(verts), "closed": closed}


def test_registered():
    assert REGISTRY["shape"] is detect


def test_zigzag_vertex_dropped(tmp_path):
    # L L(spike, 12 px arm) L
    p = poly([[50, 100], [250, 100], [238, 102]])
    [i] = _detect(ctx_for(tmp_path, [p]))
    assert i.issue_type == "shape" and i.label_id == "L1"
    assert i.fix == {"op": "drop_vertices", "poly_index": 0, "indices": [1]}
    assert not i.hand_fix
    assert i.features["kind"] == "turn_back" and 140 < i.features["angle"] < 175
    assert abs(i.confidence - (i.features["angle"] - 120) / 60) < 1e-6
    assert i.features["arm_after"] < 25 < i.features["arm_before"]
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
    [i] = _detect(ctx_for(tmp_path, [poly([[100, 100], [118, 100]])]))
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
    p = poly([[50, 200], [100, 150], [150, 150], [200, 200], [178, 206]], "LCCLL")
    [i] = _detect(ctx_for(tmp_path, [p]))
    assert i.features["kind"] == "turn_back" and i.hand_fix and i.fix is None


def test_most_severe_per_label_one_issue(tmp_path):
    bow = poly([[50, 50], [250, 150], [250, 50], [50, 150]])
    zig = poly([[50, 300], [250, 300], [238, 302]])
    out = detect(ctx_for(tmp_path, [bow, zig]))
    assert len(out) == 1 and out[0].features["kind"] == "self_cross"
    assert "turn_back" in out[0].reason or "turn-back" in out[0].reason


def test_drop_index_refers_to_raw_vertices_with_curve(tmp_path):
    # L C C L L(turn-back) L : the turn-back vertex is raw index 4
    p = poly([[40, 200], [80, 150], [120, 150], [160, 200], [300, 200], [290, 202]], "LCCLLL")
    [i] = _detect(ctx_for(tmp_path, [p]))
    assert i.fix == {"op": "drop_vertices", "poly_index": 0, "indices": [4]}


def test_long_armed_v_not_flagged(tmp_path):
    # gore tip: 150 deg reversal, both arms >= 60 px
    v = poly([[50, 100], [300, 100], [60, 160]])
    assert detect(ctx_for(tmp_path, [v])) == []
    v2 = poly([[50, 100], [120, 100], [50, 120]])  # arms 70 px and 73 px
    assert detect(ctx_for(tmp_path, [v2])) == []


def test_spike_on_closed_ring_is_turn_back(tmp_path):
    ring = poly([[50, 50], [250, 50], [240, 52], [250, 250], [50, 250]], closed=True)
    [i] = _detect(ctx_for(tmp_path, [ring]))
    assert i.features["kind"] == "turn_back" and i.features["angle"] > 120


def _short(y=100):
    return poly([[100, y], [120, y]])


def test_fragment_over_painted_dash_not_flagged(tmp_path):
    img = stripe_image(stripes=((100, 255),))
    assert detect(ctx_for(tmp_path, [_short()], "solid_single_line", img)) == []


def test_fragment_on_bare_asphalt_deleted(tmp_path):
    [i] = _detect(ctx_for(tmp_path, [_short()], "solid_single_line"))
    assert i.fix == {"op": "delete"} and i.features["ridge"] < 15


def test_curb_fragment_deleted_plain_rule(tmp_path):
    img = stripe_image(stripes=((100, 255),))
    [i] = _detect(ctx_for(tmp_path, [_short()], "curb_road_edge", img))
    assert i.fix == {"op": "delete"} and "ridge" not in i.features


def _stored(kind, label="L1", **extra):
    feats = {"kind": kind, **extra}
    return Issue("shape", "exp", "f.png", label, "solid_single_line", 0.9, "r", (0.0, 0.0),
                 None, False, features=feats)


def test_keep_issue_only_self_cross():
    assert keep_issue(_stored("self_cross"))
    assert not keep_issue(_stored("fragment", length=12.0))
    assert not keep_issue(_stored("turn_back", angle=150.0))
    assert not keep_issue(_stored(None))


def test_detect_emits_only_self_crossings(tmp_path):
    bow = poly([[50, 50], [250, 150], [250, 50], [50, 150]])
    zig = poly([[50, 300], [250, 300], [238, 302]])
    frag = poly([[100, 100], [118, 100]])
    assert detect(ctx_for(tmp_path, [zig])) == []
    assert detect(ctx_for(tmp_path, [frag])) == []
    [i] = detect(ctx_for(tmp_path, [bow]))
    assert i.features["kind"] == "self_cross"
    # the internal pass still sees the turn-back and fragment kinds
    assert [j.features["kind"] for j in _detect(ctx_for(tmp_path, [zig]))] == ["turn_back"]


def test_refilter_shape_keeps_self_crosses_and_tops_up(tmp_path):
    from annotation_qa.__main__ import main
    cross = [_stored("self_cross", label=f"C{k}") for k in range(12)]
    other = [_stored("fragment", label=f"F{k}", length=10.0) for k in range(8)] + \
            [_stored("turn_back", label=f"T{k}", angle=150.0) for k in range(8)]
    iss = cross + other
    write_issues(tmp_path / "issues_shape.jsonl", iss)
    old_keys = [cross[0].key, cross[1].key, other[0].key]  # other[0] will be filtered out
    (tmp_path / "calibration").mkdir()
    (tmp_path / "calibration" / "sample_shape.json").write_text(
        json.dumps({"keys": old_keys}))
    (tmp_path / "marks.json").write_text(json.dumps({cross[0].key: "real", other[0].key: "false"}))
    assert main(["refilter", "--type", "shape", "--out", str(tmp_path)]) == 0
    kept = read_issues(tmp_path / "issues_shape.jsonl")
    assert len(kept) == 12 and all(i.features["kind"] == "self_cross" for i in kept)
    sample = json.loads((tmp_path / "calibration" / "sample_shape.json").read_text())["keys"]
    assert len(sample) == 12  # all surviving keys: 12 self-crosses, fewer than 30
    assert cross[0].key in sample and cross[1].key in sample
    assert other[0].key not in sample
    assert all(k in {i.key for i in kept} for k in sample)
    assert len(list((tmp_path / "backup").glob("issues_shape.*.jsonl"))) == 1
