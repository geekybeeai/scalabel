import json

import cv2

from conftest import _road, hpoly

from annotation_qa import calibrate
from annotation_qa.__main__ import main

YELLOW = (30, 200, 230)


def _label(lid, cat, y):
    return {"id": lid, "category": cat, "attributes": {}, "manualShape": True,
            "poly2d": [hpoly(y)], "box2d": None, "box3d": None}


def _export(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    frames = []
    for n in range(3):
        img = _road()
        img[190:211, 10:390] = YELLOW  # wide double at y=200
        img[100:105, 10:390] = YELLOW  # narrow yellow single at y=102
        img[300:305, 10:390] = (230, 230, 230)  # white single at y=302
        cv2.imwrite(str(root / f"f{n}.png"), img)
        labels = [_label("D", "double_yellow_line", 200), _label("S", "solid_single_line", 102),
                  _label("W", "solid_single_line", 302),
                  _label("C1", "center_turn_lane", 50), _label("C2", "center_turn_lane", 90)]
        frames.append({"name": f"f{n}.png", "url": f"f{n}.png", "labels": labels})
    p = tmp_path / "exp.json"
    p.write_text(json.dumps({"frames": frames, "config": {}}))
    return p, root


def test_calibrate_writes_stats(tmp_path):
    exp, root = _export(tmp_path)
    out = tmp_path / "out"
    stats = calibrate.calibrate_yellow([exp], root, out)
    on_disk = json.loads((out / "calibration" / "yellow_stats.json").read_text())
    assert on_disk == stats
    assert stats["ctl_pair_distance_median"] == 40.0  # C1 y=50, C2 y=90
    assert stats["fwhm_double_median"] > stats["fwhm_single_yellow_median"] + 8
    mid = (stats["fwhm_double_median"] + stats["fwhm_single_yellow_median"]) / 2
    assert abs(stats["fwhm_threshold"] - mid) < 1e-9
    assert stats["width_usable"] is True
    assert stats["n_double_labels"] == 3 and stats["n_single_yellow_labels"] == 3
    assert stats["n_ctl_labels"] == 6 and stats["n_frames_measured"] == 3


def test_cli_calibrate_yellow(tmp_path):
    exp, root = _export(tmp_path)
    out = tmp_path / "out"
    assert main(["calibrate-yellow", "--exports", str(exp), "--root", str(root),
                 "--out", str(out)]) == 0
    assert (out / "calibration" / "yellow_stats.json").exists()
