import json

import pytest

from annotation_qa import fix as fixmod
from annotation_qa.merge import merge_reviewed


def _label(lid, cat):
    return {"id": lid, "category": cat, "attributes": {}, "manualShape": True,
            "poly2d": [{"vertices": [[0, 0], [9, 9]], "types": "LL", "closed": False}],
            "box2d": None, "box3d": None}


def _frame(name, labels):
    return {"name": name, "url": name, "labels": labels}


def test_merge_only_touches_same_stem(tmp_path, monkeypatch):
    orig = tmp_path / "orig"
    orig.mkdir()
    for stem in ("exportA", "exportB"):
        (orig / f"{stem}.json").write_text(json.dumps({"frames": [
            _frame("shared.png", [_label("1", "solid_single_line")]),
            _frame("other.png", [_label("2", "solid_single_line")])], "config": {}}))
    monkeypatch.setattr(fixmod, "ORIGINALS_DIR", orig)
    out = tmp_path / "out"
    reviewed = tmp_path / "reviewed.json"
    reviewed.write_text(json.dumps({"frames": [
        _frame("shared.png", [_label("1", "curb_road_edge"), _label("9", "dashed_white_line")]),
        _frame("absent.png", [])], "config": {}}))
    n = merge_reviewed("exportA", reviewed, out)
    assert n == 1
    a = {f["name"]: f for f in json.load(open(out / "work" / "exportA.json"))["frames"]}
    assert [l["id"] for l in a["shared.png"]["labels"]] == ["1", "9"]
    assert a["shared.png"]["labels"][0]["category"] == "curb_road_edge"
    assert a["other.png"]["labels"][0]["id"] == "2"
    assert not (out / "work" / "exportB.json").exists()
    b = json.load(open(orig / "exportB.json"))
    assert b["frames"][0]["labels"][0]["category"] == "solid_single_line"
