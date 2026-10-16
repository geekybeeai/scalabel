from pathlib import Path

import numpy as np

from conftest import _road, _frame

from annotation_qa.context import FrameContext
from annotation_qa.data import Export
from annotation_qa.detectors import REGISTRY
from annotation_qa.detectors.duplicates import detect


def hline(y, x0, x1):
    return {"vertices": [[x0, y], [x1, y]], "types": "LL", "closed": False}


def ctx_for(tmp_path, labels):
    full = [{"id": i, "category": c, "attributes": {}, "manualShape": True,
             "poly2d": polys, "box2d": None, "box3d": None} for i, c, polys in labels]
    frame = {"name": "f.png", "url": "f.png", "labels": full}
    return FrameContext(Export(Path("exp.json"), [frame], {}), frame, _frame(tmp_path, _road()))


def test_registered():
    assert REGISTRY["duplicates"] is detect


def test_exact_duplicate_shorter_deleted(tmp_path):
    ctx = ctx_for(tmp_path, [("A", "solid_single_line", [hline(100, 20, 380)]),
                             ("B", "solid_single_line", [hline(101, 50, 300)])])
    [i] = detect(ctx)
    assert i.issue_type == "duplicates" and i.label_id == "B"
    assert i.related_label_ids == ["A"] and i.fix == {"op": "delete"} and not i.hand_fix
    assert i.features["overlap"] >= 0.99 and i.features["kind"] == "exact_duplicate"


def test_different_class_same_place_hand_fix(tmp_path):
    ctx = ctx_for(tmp_path, [("A", "curb_road_edge", [hline(100, 20, 380)]),
                             ("B", "solid_single_line", [hline(102, 50, 300)])])
    [i] = detect(ctx)
    assert i.label_id == "B" and i.hand_fix and i.fix is None
    assert i.related_label_ids == ["A"] and "two classes on one feature" in i.reason
    assert i.features["other_category"] == "curb_road_edge"


def test_overlapping_pieces_hand_fix(tmp_path):
    ctx = ctx_for(tmp_path, [("A", "solid_single_line", [hline(100, 20, 300)]),
                             ("B", "solid_single_line", [hline(100, 200, 380)])])
    [i] = detect(ctx)  # B is shorter (180 vs 280); 100 of 180 overlaps
    assert i.label_id == "B" and i.hand_fix and "overlapping pieces; join by hand" in i.reason
    assert 0.3 <= i.features["overlap"] < 0.8


def test_different_class_partial_overlap_ignored(tmp_path):
    ctx = ctx_for(tmp_path, [("A", "curb_road_edge", [hline(100, 20, 300)]),
                             ("B", "solid_single_line", [hline(100, 200, 380)])])
    assert detect(ctx) == []


def test_parallel_lines_8px_apart_not_duplicate(tmp_path):
    ctx = ctx_for(tmp_path, [("A", "double_yellow_line", [hline(100, 20, 380)]),
                             ("B", "solid_single_line", [hline(108, 20, 380)])])
    assert detect(ctx) == []


def test_pair_reported_once(tmp_path):
    ctx = ctx_for(tmp_path, [("A", "solid_single_line", [hline(100, 20, 380)]),
                             ("B", "solid_single_line", [hline(100, 20, 380)])])
    issues = detect(ctx)
    assert len(issues) == 1 and {issues[0].label_id, issues[0].related_label_ids[0]} == {"A", "B"}


def test_one_issue_per_label_most_severe_pair(tmp_path):
    ctx = ctx_for(tmp_path, [("A", "solid_single_line", [hline(100, 20, 380)]),
                             ("C", "curb_road_edge", [hline(101, 20, 380)]),
                             ("B", "solid_single_line", [hline(100, 50, 300)])])
    by = {i.label_id: i for i in detect(ctx)}
    assert by["B"].related_label_ids == ["A"] and by["B"].fix == {"op": "delete"}
    assert by["B"].features["other_pairs"][0]["label_id"] == "C"
    assert len(by) == len(detect(ctx))


def test_multi_poly_label_and_tiny_skipped(tmp_path):
    ctx = ctx_for(tmp_path, [("A", "solid_single_line", [hline(100, 20, 200), hline(100, 210, 380)]),
                             ("B", "solid_single_line", [hline(100, 50, 150), hline(100, 250, 300)]),
                             ("T", "solid_single_line", [hline(100, 50, 58)])])
    [i] = detect(ctx)
    assert i.label_id == "B" and i.related_label_ids == ["A"]


def test_stub_crossing_a_line_not_overlap(tmp_path):
    # 20 px stub leaving the line at 30 deg: ~40% of it is within 4 px, but not along it
    stub = {"vertices": [[200, 100], [217.3, 110]], "types": "LL", "closed": False}
    ctx = ctx_for(tmp_path, [("A", "solid_single_line", [hline(100, 20, 380)]),
                             ("B", "solid_single_line", [stub])])
    assert detect(ctx) == []
