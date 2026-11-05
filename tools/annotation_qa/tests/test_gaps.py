from pathlib import Path

import numpy as np

from conftest import _road, _frame

from annotation_qa.context import FrameContext
from annotation_qa.data import Export
from annotation_qa.detectors import REGISTRY
from annotation_qa.detectors.gaps import detect, detect_all, joined_poly, keep_issue
from annotation_qa.issues import Issue
from annotation_qa.fix import types_problem


def hline(y, x0, x1):
    return {"vertices": [[x0, y], [x1, y]], "types": "LL", "closed": False}


def ctx_for(tmp_path, labels):
    full = [{"id": i, "category": c, "attributes": {}, "manualShape": True,
             "poly2d": polys, "box2d": None, "box3d": None} for i, c, polys in labels]
    frame = {"name": "f.png", "url": "f.png", "labels": full}
    return FrameContext(Export(Path("exp.json"), [frame], {}), frame, _frame(tmp_path, _road()))


def test_registered():
    assert REGISTRY["gaps"] is detect


def test_gap_between_two_pieces_is_joined_into_the_longer(tmp_path):
    ctx = ctx_for(tmp_path, [("A", "solid_single_line", [hline(100, 20, 200)]),
                             ("B", "solid_single_line", [hline(100, 230, 300)])])
    [i] = detect_all(ctx)  # B (70 px) is shorter than A (180 px): B carries the issue
    assert i.issue_type == "gaps" and i.label_id == "B" and i.related_label_ids == ["A"]
    assert not i.hand_fix and i.fix["op"] == "join_into" and i.fix["target"] == "A"
    assert i.fix["poly_index"] == 0 and i.fix["closed"] is False
    assert np.allclose(i.fix["vertices"], [[20, 100], [200, 100], [230, 100], [300, 100]])
    assert i.fix["types"] == "LLLL" and types_problem(i.fix["types"], 4, False) is None
    assert abs(i.features["gap_px"] - 30) < 0.01 and i.features["lateral_px"] < 0.01
    assert i.features["join"] == "gap" and np.allclose(i.features["join_splices"], [[200, 100], [230, 100]])
    assert abs(i.anchor[0] - 215) < 0.01 and abs(i.anchor[1] - 100) < 0.01


def test_join_keeps_the_target_direction(tmp_path):
    # A (the target) runs right-to-left, so its START is at the gap: A keeps its direction and
    # the joined line is B (turned to end at the gap) followed by A
    a = {"vertices": [[200, 100], [20, 100]], "types": "LL", "closed": False}
    ctx = ctx_for(tmp_path, [("A", "solid_single_line", [a]),
                             ("B", "solid_single_line", [hline(100, 230, 300)])])
    [i] = detect_all(ctx)
    assert np.allclose(i.fix["vertices"], [[300, 100], [230, 100], [200, 100], [20, 100]])


def test_curves_survive_the_join():
    curve = {"vertices": [[20, 100], [80, 60], [140, 60], [200, 100]], "types": "LCCL",
             "closed": False}
    new = joined_poly(curve, "end", hline(100, 230, 260), "start")  # curve, then the piece
    assert new["types"] == "LCCLLL" and types_problem(new["types"], 6, False) is None
    assert np.allclose(new["vertices"][:4], curve["vertices"])
    rev = joined_poly(hline(100, 230, 260), "start", curve, "end")  # target = the straight piece
    assert rev["types"] == "LCCLLL" and np.allclose(rev["vertices"][-2:], [[230, 100], [260, 100]])


def test_not_a_gap(tmp_path):
    cases = [
        [("A", "solid_single_line", [hline(100, 20, 200)]),  # different classes
         ("B", "dashed_white_line", [hline(100, 230, 300)])],
        [("A", "solid_single_line", [hline(100, 20, 200)]),  # too far
         ("B", "solid_single_line", [hline(100, 290, 380)])],
        [("A", "solid_single_line", [hline(100, 20, 200)]),  # side by side, 15 px apart
         ("B", "solid_single_line", [hline(115, 210, 300)])],
        [("A", "solid_single_line", [hline(100, 20, 200)]),  # at right angles
         ("B", "solid_single_line", [{"vertices": [[215, 110], [215, 300]], "types": "LL",
                                      "closed": False}])],
    ]
    for labels in cases:
        assert detect_all(ctx_for(tmp_path, labels)) == [], labels


def test_each_end_pairs_once_and_a_label_carries_one_issue(tmp_path):
    # B sits between A and C: B has a gap at both ends; it carries only its closer gap
    ctx = ctx_for(tmp_path, [("A", "solid_single_line", [hline(100, 20, 200)]),
                             ("B", "solid_single_line", [hline(100, 210, 260)]),
                             ("C", "solid_single_line", [hline(100, 300, 390)])])
    issues = {i.label_id: i for i in detect_all(ctx)}
    assert set(issues) == {"B"}
    assert issues["B"].fix["target"] == "A" and abs(issues["B"].features["gap_px"] - 10) < 0.01
    assert [g["label_id"] for g in issues["B"].features["other_gaps"]] == ["C"]


def test_carrier_with_several_polys_is_a_hand_fix(tmp_path):
    ctx = ctx_for(tmp_path, [("A", "solid_single_line", [hline(100, 20, 200)]),
                             ("B", "solid_single_line", [hline(100, 230, 300), hline(150, 230, 300)])])
    [i] = detect_all(ctx)
    assert i.label_id == "B" and i.hand_fix and i.fix is None
    assert "several polys" in i.features["join_refused"]


def test_features_record_what_crosses_the_gap(tmp_path):
    ctx = ctx_for(tmp_path, [("A", "solid_single_line", [hline(100, 20, 200)]),
                             ("B", "solid_single_line", [hline(100, 240, 300)]),
                             ("X", "curb_road_edge", [{"vertices": [[220, 40], [220, 160]],
                                                       "types": "LL", "closed": False}])])
    [i] = detect_all(ctx)
    assert i.features["crossed_by"] == ["curb_road_edge"]


def test_closed_rings_are_ignored(tmp_path):
    ring = {"vertices": [[20, 20], [100, 20], [100, 80], [20, 80]], "types": "LLLL", "closed": True}
    ctx = ctx_for(tmp_path, [("A", "solid_single_line", [ring]),
                             ("B", "solid_single_line", [hline(20, 110, 200)])])
    assert detect_all(ctx) == []


def _rec(piece, target_before, target_after, gap, splices):
    snap = lambda poly, cat="solid_single_line": {"category": cat, "poly2d": [poly]}
    return {"before": snap(piece), "after": None,
            "join": {"target": "A", "poly_index": 0, "before": snap(target_before),
                     "after": snap(target_after), "mode": "gap", "splices": splices, "gaps": [gap]}}


def test_verify_accepts_a_straight_gap_join():
    from annotation_qa.verify import _join
    a, b = hline(100, 20, 200), hline(100, 230, 300)
    new = joined_poly(a, "end", b, "start")
    v = _join(None, _rec(b, a, new, 30.0, [[200, 100], [230, 100]]))
    assert v["verdict"] == "ok", v
    assert any("30" in r and "gap" in r for r in v["reasons"])


def test_verify_flags_a_join_that_moved_the_line():
    from annotation_qa.verify import _join
    a, b = hline(100, 20, 200), hline(100, 230, 300)
    bad = {"vertices": [[20, 100], [200, 100], [230, 120], [300, 120]], "types": "LLLL", "closed": False}
    v = _join(None, _rec(b, a, bad, 30.0, [[200, 100], [230, 100]]))
    assert v["verdict"] == "check"


def _iss(cat, **f):
    base = {"gap_px": 20.0, "crossed_by": [], "bridge_in_roi": 1.0}
    base.update(f)
    return Issue("gaps", "s", "f.png", "B", cat, 0.7, "r", (0.0, 0.0), None, True, ["A"], base)


def test_keep_issue_pre_filter():
    assert not keep_issue(_iss("curb_road_edge", gap_px=5))  # practically touching
    assert not keep_issue(_iss("curb_road_edge", crossed_by=["solid_single_line"]))  # intersection
    assert not keep_issue(_iss("curb_road_edge", bridge_in_roi=0.6))  # image ends there
    assert not keep_issue(_iss("curb_road_edge", gap_px=55))  # driveway / median end
    assert keep_issue(_iss("curb_road_edge", gap_px=22))  # e.g. a curb under a parked car
    assert keep_issue(_iss("solid_single_line", gap_px=19, gap_paint=103, end_paint=110))  # paint goes on
    assert not keep_issue(_iss("solid_single_line", gap_px=44, gap_paint=2, end_paint=60))  # real break
    assert not keep_issue(_iss("solid_single_line", gap_px=58, gap_paint=90, end_paint=80))  # crosswalk paint
    assert not keep_issue(_iss("solid_single_line", gap_px=30, gap_paint=None, end_paint=None))
    assert not keep_issue(_iss("dashed_white_line", gap_px=30, gap_paint=3, end_paint=2))  # no paint at all


def test_detect_applies_the_pre_filter(tmp_path):
    # a 30 px curb gap with nothing crossing it is kept; a 3 px one is not
    ctx = ctx_for(tmp_path, [("A", "curb_road_edge", [hline(100, 20, 200)]),
                             ("B", "curb_road_edge", [hline(100, 230, 300)]),
                             ("C", "curb_road_edge", [hline(200, 20, 200)]),
                             ("D", "curb_road_edge", [hline(200, 203, 300)])])
    assert {i.label_id for i in detect_all(ctx)} == {"B", "D"}
    kept = detect(ctx)
    assert [i.label_id for i in kept] == ["B"] and kept[0].features["bridge_in_roi"] == 1.0
