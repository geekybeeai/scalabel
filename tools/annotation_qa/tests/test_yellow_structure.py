import json
import logging
from pathlib import Path

from conftest import _frame, _road, hpoly

from annotation_qa.context import FrameContext
from annotation_qa.data import Export
from annotation_qa.detectors import REGISTRY, yellow_structure
from annotation_qa.detectors.yellow_structure import detect

YELLOW = (30, 200, 230)  # BGR


def write_stats(tmp_path, threshold=12.0, ctl=40.0):
    d = tmp_path / "calibration"
    d.mkdir(exist_ok=True)
    (d / "yellow_stats.json").write_text(json.dumps({
        "ctl_pair_distance_median": ctl, "fwhm_double_median": 20.0,
        "fwhm_single_yellow_median": 4.0, "fwhm_threshold": threshold}))


def band(img, y0, y1, color=YELLOW):
    img[y0:y1, 10:390] = color
    return img


def dashes(img, y0, y1, on=40, off=30):
    for x in range(10, 390, on + off):
        img[y0:y1, x:min(x + on, 390)] = YELLOW
    return img


def ctx_of(tmp_path, img, labels):
    ls = [{"id": lid, "category": cat, "attributes": {}, "manualShape": True,
           "poly2d": [hpoly(y)], "box2d": None, "box3d": None} for lid, cat, y in labels]
    frame = {"name": "f.png", "url": "f.png", "labels": ls}
    return FrameContext(Export(Path("exp.json"), [frame], {}), frame, _frame(tmp_path, img))


def test_registered():
    assert REGISTRY["yellow_structure"] is detect


def test_wide_band_double_yellow_ok(tmp_path):
    write_stats(tmp_path)
    img = band(_road(), 190, 211)
    assert detect(ctx_of(tmp_path, img, [("L1", "double_yellow_line", 200)])) == []


def test_narrow_single_labelled_double_flagged(tmp_path):
    write_stats(tmp_path)
    img = band(_road(), 198, 203)
    [i] = detect(ctx_of(tmp_path, img, [("L1", "double_yellow_line", 200)]))
    assert i.issue_type == "yellow_structure" and i.reason == "single yellow? check class"
    assert i.fix == {"op": "set_category", "to": "solid_single_line"} and i.confidence == 0.5
    assert i.features["fwhm"] < i.features["threshold"] == 12.0


def test_wide_yellow_single_flagged_double(tmp_path):
    write_stats(tmp_path)
    img = band(_road(), 190, 211)
    [i] = detect(ctx_of(tmp_path, img, [("L1", "solid_single_line", 200)]))
    assert i.reason == "double yellow?"
    assert i.fix == {"op": "set_category", "to": "double_yellow_line"}


def test_narrow_yellow_single_and_wide_white_single_ok(tmp_path):
    write_stats(tmp_path)
    img = band(_road(), 198, 203)
    assert detect(ctx_of(tmp_path, img, [("L1", "solid_single_line", 200)])) == []
    (tmp_path / "w").mkdir()
    write_stats(tmp_path / "w")
    white = band(_road(), 190, 211, (230, 230, 230))
    assert detect(ctx_of(tmp_path / "w", white, [("L1", "solid_single_line", 200)])) == []


def _ctl_scene(second_sides=(-6, 6), ys=(180, 220), second=True):
    img = _road()
    for y, side in zip(ys, second_sides):
        band(img, y - 2, y + 3)
        if second:
            dashes(img, y + side - 2, y + side + 3)
    return img


def test_ctl_without_partner_flagged(tmp_path):
    write_stats(tmp_path)
    img = _ctl_scene()
    [i] = detect(ctx_of(tmp_path, img, [("A", "center_turn_lane", 180)]))
    assert i.reason == "missing other side of turn lane" and i.hand_fix and i.fix is None
    assert i.features["ctl_pair_distance_median"] == 40.0 and i.features["partner_distance"] is None


def test_ctl_pair_ok(tmp_path):
    write_stats(tmp_path)
    img = _ctl_scene()
    labels = [("A", "center_turn_lane", 180), ("B", "center_turn_lane", 220)]
    assert detect(ctx_of(tmp_path, img, labels)) == []


def test_ctl_partner_at_wrong_distance_flagged(tmp_path):
    write_stats(tmp_path)
    img = _ctl_scene(ys=(150, 220), second_sides=(-6, 6))  # 70 px apart, median 40
    labels = [("A", "center_turn_lane", 150), ("B", "center_turn_lane", 220)]
    got = detect(ctx_of(tmp_path, img, labels))
    assert {i.label_id for i in got} == {"A", "B"}
    assert all(i.reason == "missing other side of turn lane" for i in got)
    assert abs(got[0].features["nearest_distance"] - 70) < 1


def test_ctl_without_dashed_beside_flagged(tmp_path):
    write_stats(tmp_path)
    img = _ctl_scene(second=False)
    labels = [("A", "center_turn_lane", 180), ("B", "center_turn_lane", 220)]
    got = detect(ctx_of(tmp_path, img, labels))
    assert len(got) == 2 and all(
        i.reason == "no dashed yellow beside it; is this a turn lane?" and i.hand_fix for i in got)
    assert got[0].features["first_ridge"] >= 25 and got[0].features["second_ridge"] < 15


def test_ctl_on_bare_road_flagged_no_paint(tmp_path):
    write_stats(tmp_path)
    labels = [("A", "center_turn_lane", 180), ("B", "center_turn_lane", 220)]
    got = detect(ctx_of(tmp_path, _road(), labels))
    assert len(got) == 2 and all("no solid yellow" in i.reason for i in got)


def test_missing_stats_skips_width_and_partner_but_keeps_paint(tmp_path, caplog):
    yellow_structure.clear_cache()
    with caplog.at_level(logging.WARNING, logger="annotation_qa.yellow_structure"):
        narrow = band(_road(), 198, 203)
        assert detect(ctx_of(tmp_path, narrow, [("L1", "double_yellow_line", 200)])) == []
        # lone CTL with paint ok: partner check skipped
        img = _ctl_scene()
        assert detect(ctx_of(tmp_path, img, [("A", "center_turn_lane", 180)])) == []
        # paint check still runs
        got = detect(ctx_of(tmp_path, _road(), [("A", "center_turn_lane", 180)]))
        assert [i.reason for i in got] == ["no solid yellow paint under the line"]
    warns = [r for r in caplog.records if "yellow_stats" in r.getMessage()]
    assert len(warns) == 1


def test_explicit_stats_path_setter(tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    write_stats(other, threshold=12.0)
    yellow_structure.set_stats_path(other / "calibration" / "yellow_stats.json")
    try:
        img = band(_road(), 198, 203)
        [i] = detect(ctx_of(tmp_path, img, [("L1", "double_yellow_line", 200)]))
        assert i.fix["to"] == "solid_single_line"
    finally:
        yellow_structure.set_stats_path(None)


def test_one_issue_per_label(tmp_path):
    write_stats(tmp_path)
    img = _ctl_scene(second=False)
    got = detect(ctx_of(tmp_path, img, [("A", "center_turn_lane", 180)]))
    assert [i.label_id for i in got] == ["A"]  # paint beats partner


def test_width_skipped_when_doubles_not_wider_than_singles(tmp_path):
    write_stats(tmp_path)
    p = tmp_path / "calibration" / "yellow_stats.json"
    p.write_text(json.dumps({**json.loads(p.read_text()), "width_usable": False}))
    img = band(_road(), 198, 203)
    assert detect(ctx_of(tmp_path, img, [("L1", "double_yellow_line", 200)])) == []


def test_ctl_on_dark_road_negative_ridge_does_not_crash(tmp_path):
    write_stats(tmp_path)
    img = _road()
    img[170:190, 10:390] = (30, 200, 230)  # wide yellow beside, none under the line: ridge < 0 near it
    img[195:206, 10:390] = (60, 60, 60)
    got = detect(ctx_of(tmp_path, img, [("A", "center_turn_lane", 200)]))
    assert [i.reason for i in got] == ["no solid yellow paint under the line"]
