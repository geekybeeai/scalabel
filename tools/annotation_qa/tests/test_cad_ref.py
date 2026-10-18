import json

import cv2
import numpy as np

from annotation_qa import cad_ref
from annotation_qa.cad_ref import CadRef, line_length, norm
from annotation_qa.detectors.curb_soft import CURB, SOFT, keep_issue, decide
from annotation_qa.issues import Issue, read_issues, write_issues


def _rgba(h=300, w=900):
    return np.zeros((h, w, 4), np.uint8)


def _green(img, x0, y0, x1, y1):
    img[y0:y1, x0:x1] = (0, 200, 0, 255)  # BGRA


def test_long_dashes_counted_block_letters_not():
    img = _rgba()
    for k in range(10):  # 10 dashes 60x3
        _green(img, 20 + k * 80, 100, 80 + k * 80, 103)
    assert line_length(img, "green") >= 600
    txt = _rgba()
    for k in range(40):  # chunky letters 12x14
        _green(txt, 10 + k * 20, 50, 22 + k * 20, 64)
    _green(txt, 10, 150, 40, 185)  # fat blob, L=35 but not elongated
    assert line_length(txt, "green") == 0


def test_red_and_alpha_and_color_separation():
    img = _rgba()[..., [2, 1, 0, 3]].copy()  # line_length takes RGBA
    img[100:103, 10:700] = (220, 0, 0, 255)
    assert line_length(img, "red") > 600 and line_length(img, "green") == 0
    img2 = _rgba()
    img2[100:103, 10:700] = (0, 200, 0, 0)  # transparent
    assert line_length(img2, "green") == 0


def test_norm_matching():
    assert norm("SLC 1_verification_z20.png") == norm("SLC_1.png") == "slc_1"
    assert norm("100466_200674_input.png") == norm("100466-200674.png")


def _cad_dir(tmp_path, with_green=True):
    d = tmp_path / "cad" / "Batch_1"
    d.mkdir(parents=True)
    img = _rgba(200, 1000)
    if with_green:
        _green(img, 10, 100, 900, 104)
    cv2.imwrite(str(d / "SLC_1.png"), img)
    return tmp_path / "cad"


def test_flag_match_none_and_cache(tmp_path, monkeypatch):
    cd = _cad_dir(tmp_path)
    out = tmp_path / "out"
    c = CadRef(out, cd)
    assert c("SLC 1_verification_z20.png") is True
    assert c("nothing.png") is None
    assert c.green_len("SLC_1.png") > 800
    cache = json.loads((out / "cad_cache.json").read_text())
    assert len(cache) == 1
    monkeypatch.setattr(cad_ref, "line_length", lambda *a: 1 / 0)  # must come from cache
    assert CadRef(out, cd)("SLC_1.png") is True
    # changed mtime invalidates
    p = cd / "Batch_1" / "SLC_1.png"
    img = _rgba(200, 1000)
    monkeypatch.undo()
    cv2.imwrite(str(p), img)
    import os
    os.utime(p, (1, 1))
    assert CadRef(out, cd)("SLC_1.png") is False


def _iss(cat, frame="SLC_1.png", hand=False, **f):
    feats = dict(n_votes=6, curb_votes=0, near_band_votes=0, share=0.5)
    feats.update(f)
    return Issue(issue_type="curb_soft", export_stem="e", frame_name=frame, label_id="L",
                 category=cat, confidence=1, reason="r", anchor=(0, 0),
                 fix=None if hand else {"op": "set_category", "to": "x"}, hand_fix=hand,
                 features=feats)


def test_rules_without_cad():
    assert not keep_issue(_iss(CURB, hand=True))
    assert not keep_issue(_iss(SOFT, hand=True, near_band_votes=3, share=0.9))
    # soft -> curb
    assert keep_issue(_iss(SOFT, near_band_votes=1, share=0.75))
    assert not keep_issue(_iss(SOFT, near_band_votes=0, share=0.9))
    assert not keep_issue(_iss(SOFT, near_band_votes=2, share=0.7))
    # curb -> soft
    assert keep_issue(_iss(CURB, n_votes=7))
    assert not keep_issue(_iss(CURB, n_votes=6))
    assert not keep_issue(_iss(CURB, curb_votes=1, n_votes=9))
    assert not keep_issue(_iss(CURB, near_band_votes=1, n_votes=9))
    assert not keep_issue(_iss(CURB, n_votes=4))


def test_soft_curb_drop_boundaries():
    class Cad:
        def __init__(self, gl, flag=None):
            self.gl, self.flag = gl, flag

        def __call__(self, f):
            return self.flag

        def green_len(self, f):
            return self.gl
    ok = dict(near_band_votes=3, share=0.9)
    assert decide(_iss(SOFT, **ok), Cad(8000)) == (True, None)  # not > 8000
    assert decide(_iss(SOFT, **ok), Cad(8001)) == (False, None)
    assert decide(_iss(SOFT, near_band_votes=4, share=0.9), Cad(20000)) == (True, None)
    assert decide(_iss(SOFT, near_band_votes=3, share=0.9), Cad(None)) == (True, None)
    # drop beats the CAD rescue
    assert decide(_iss(SOFT, near_band_votes=0, share=0.5), Cad(9000, False)) == (False, None)
    assert decide(_iss(SOFT, near_band_votes=0, share=0.5), Cad(100, False)) == (True, "cad_rescue")


def test_cad_rules_and_features():
    cad = lambda f: {"a": False, "b": True}.get(f)  # noqa: E731
    assert decide(_iss(CURB, "a", n_votes=7), cad) == (False, "cad_drop")
    assert decide(_iss(CURB, "b", n_votes=7), cad) == (True, None)
    assert decide(_iss(CURB, "zz", n_votes=7), cad) == (True, None)
    i = _iss(SOFT, "a")
    assert decide(i, cad) == (True, "cad_rescue")
    assert i.features["cad_soft"] is False and i.features["cad_green_len"] is None
    assert decide(_iss(SOFT, "b"), cad) == (False, None)
    assert decide(_iss(SOFT, "zz"), cad) == (False, None)
    assert decide(_iss(SOFT, "a", hand=True), cad) == (False, None)  # hand never rescued
    assert decide(_iss(SOFT, "b", near_band_votes=1, share=0.8), cad) == (True, None)


def test_refilter_curb_soft(tmp_path, monkeypatch):
    from annotation_qa.__main__ import main
    cd = _cad_dir(tmp_path, with_green=False)  # SLC_1: CAD has no soft edge
    monkeypatch.setattr(cad_ref, "CAD_DIR", cd)
    out = tmp_path / "out"
    out.mkdir()
    iss = [_iss(CURB, "SLC_1.png", n_votes=7), _iss(CURB, "other.png", n_votes=7), _iss(SOFT, "SLC_1.png"),
           _iss(SOFT, "other.png"), _iss(CURB, "other.png", hand=True),
           _iss(CURB, "other.png", n_votes=6)]
    for k, i in enumerate(iss):
        i.label_id = f"L{k}"
    write_issues(out / "issues_curb_soft.jsonl", iss)
    (out / "calibration").mkdir()
    (out / "calibration" / "sample_curb_soft.json").write_text(
        json.dumps({"keys": [i.key for i in iss]}))
    assert main(["refilter", "--type", "curb_soft", "--out", str(out)]) == 0
    kept = read_issues(out / "issues_curb_soft.jsonl")
    assert [i.label_id for i in kept] == ["L1", "L2"]
    assert kept[0].features["cad_soft"] is None and kept[1].features["cad_soft"] is False
    assert "cad_green_len" in kept[1].features
    assert (out / "cad_cache.json").exists()
    sample = json.loads((out / "calibration" / "sample_curb_soft.json").read_text())["keys"]
    assert sorted(sample) == sorted(i.key for i in kept)
