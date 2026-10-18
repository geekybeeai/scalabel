import json

import pytest

from annotation_qa import fix as fixmod
from annotation_qa.merge import MergeRefused, merge_reviewed
from annotation_qa.rounds import merge_markers

TAG = "exportA_fix_curb_soft_v1"


def _label(lid, cat):
    return {"id": lid, "category": cat, "attributes": {}, "manualShape": True,
            "poly2d": [{"vertices": [[0, 0], [9, 9]], "types": "LL", "closed": False}],
            "box2d": None, "box3d": None}


def _frame(name, labels, url=None):
    return {"name": name, "url": url or f"items/{name}", "labels": labels}


def _write(path, frames):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"frames": frames, "config": {}}))
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    orig = tmp_path / "orig"
    for stem in ("exportA", "exportB"):
        _write(orig / f"{stem}.json", [_frame("shared.png", [_label("1", "solid_single_line")]),
                                       _frame("other.png", [_label("2", "solid_single_line")])])
    monkeypatch.setattr(fixmod, "ORIGINALS_DIR", orig)
    out = tmp_path / "out"
    _write(out / "rounds" / f"{TAG}_items.json", [_frame("shared.png", [_label("1", "x")])])
    return orig, out, tmp_path


def _reviewed(tmp_path, frames):
    return _write(tmp_path / "reviewed.json", frames)


def test_merge_only_touches_round_frames_of_same_stem(env):
    orig, out, tmp = env
    rev = _reviewed(tmp, [_frame("shared.png", [_label("1", "curb_road_edge"),
                                                _label("9", "dashed_white_line")])])
    assert merge_reviewed("exportA", rev, out, round_tag=TAG).frames == 1
    a = {f["name"]: f for f in json.load(open(out / "work" / "exportA.json"))["frames"]}
    assert [l["id"] for l in a["shared.png"]["labels"]] == ["1", "9"]
    assert a["shared.png"]["labels"][0]["category"] == "curb_road_edge"
    assert a["other.png"]["labels"][0]["id"] == "2"
    assert not (out / "work" / "exportB.json").exists()
    b = json.load(open(orig / "exportB.json"))
    assert b["frames"][0]["labels"][0]["category"] == "solid_single_line"
    (m,) = merge_markers(out)
    assert m["tag"] == TAG and m["frames"] == ["shared.png"] and m["merged_frames"] == 1


def _refused(env, frames, stem="exportA", tag=TAG, match=""):
    orig, out, tmp = env
    rev = _reviewed(tmp, frames)
    with pytest.raises(MergeRefused, match=match):
        merge_reviewed(stem, rev, out, round_tag=tag)
    assert not (out / "work" / f"{stem}.json").exists() or json.load(
        open(out / "work" / f"{stem}.json")) == json.load(open(orig / f"{stem}.json"))
    assert merge_markers(out) == []


def test_refuses_tag_of_another_stem(env):
    _refused(env, [_frame("shared.png", [])], stem="exportB", match="not a round of exportB")


def test_refuses_missing_items_file(env):
    _refused(env, [_frame("shared.png", [])], tag="exportA_fix_curb_soft_v9", match="no items file")


def test_refuses_different_frame_set(env):
    _refused(env, [_frame("shared.png", []), _frame("other.png", [])], match="do not match")
    _refused(env, [_frame("other.png", [])], match="do not match")


def test_refuses_url_mismatch_other_batch(env):
    # same frame name, other batch's image (Random vs TMI share names)
    _refused(env, [_frame("shared.png", [], url="items/batch_3/shared.png")], match="urls differ")


def test_refuses_empty_reviewed_export(env):
    _refused(env, [], match="no frames")
