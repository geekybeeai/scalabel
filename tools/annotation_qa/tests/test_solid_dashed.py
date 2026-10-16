import numpy as np

from conftest import _road, hpoly, line_ctx

from annotation_qa.detectors import REGISTRY
from annotation_qa.detectors.solid_dashed import detect


def paint(img, spans, cy=200):
    for dy, v in zip(range(-2, 3), (200, 230, 255, 230, 200)):
        for a, b in spans:
            img[cy + dy, a:b] = (v, v, v)
    return img


def dashes(on=40, off=30, x0=10, x1=390):
    return [(x, min(x + on, x1)) for x in range(x0, x1, on + off)]


def test_registered():
    assert REGISTRY["solid_dashed"] is detect


def test_solid_label_over_dashes_flagged(tmp_path):
    img = paint(_road(), dashes())
    [i] = detect(line_ctx(tmp_path, img, [hpoly(200)], "solid_single_line"))
    assert i.issue_type == "solid_dashed" and i.label_id == "L1"
    assert i.fix == {"op": "set_category", "to": "dashed_white_line"} and not i.hand_fix
    f = i.features
    assert 0.15 <= f["on_fraction"] <= 0.75 and f["n_off_runs"] >= 3 and f["on_cv"] <= 0.5
    assert 50 <= i.anchor[0] <= 350 and abs(i.anchor[1] - 200) < 3


def test_dashed_label_over_solid_flagged(tmp_path):
    img = paint(_road(), [(10, 390)])
    [i] = detect(line_ctx(tmp_path, img, [hpoly(200)], "dashed_white_line"))
    assert i.fix == {"op": "set_category", "to": "solid_single_line"}
    assert i.features["on_fraction"] >= 0.85


def test_matching_labels_not_flagged(tmp_path):
    assert detect(line_ctx(tmp_path, paint(_road(), dashes()), [hpoly(200)], "dashed_white_line")) == []
    assert detect(line_ctx(tmp_path, paint(_road(), [(10, 390)]), [hpoly(200)], "solid_single_line")) == []


def test_car_occluding_solid_not_dashed(tmp_path):
    img = paint(_road(), [(10, 390)])
    img[170:230, 130:190] = (20, 20, 20)  # dark car over the paint
    img[170:230, 250:300] = (30, 30, 30)
    assert detect(line_ctx(tmp_path, img, [hpoly(200)], "solid_single_line")) == []


def test_short_line_skipped(tmp_path):
    img = paint(_road(), dashes(on=20, off=20))
    assert detect(line_ctx(tmp_path, img, [hpoly(200, 50, 160)], "solid_single_line")) == []


def test_line_beside_its_paint_is_position_not_class(tmp_path):
    img = paint(_road(), dashes(), cy=212)  # dashes 12 px away from the label
    assert detect(line_ctx(tmp_path, img, [hpoly(200)], "solid_single_line")) == []


def test_yellow_solid_over_yellow_dashes_not_flagged(tmp_path):
    img = _road()
    for a, b in dashes():
        img[198:203, a:b] = (30, 200, 230)
    assert detect(line_ctx(tmp_path, img, [hpoly(200)], "solid_single_line")) == []
