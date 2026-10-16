import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from annotation_qa import fix as fixmod
from annotation_qa.fix import apply, working_copy
from annotation_qa.geometry import dense, resample
from annotation_qa.issues import Issue, write_issues, write_marks


def _label(lid, cat, verts, types=None):
    return {"id": lid, "category": cat, "attributes": {}, "manualShape": True,
            "poly2d": [{"vertices": verts, "types": types or "L" * len(verts), "closed": False}],
            "box2d": None, "box3d": None}


def _issue(stem, frame, lid, itype, fix=None, hand=False):
    return Issue(itype, stem, frame, lid, "solid_single_line", 0.9, "r", (0.0, 0.0), fix, hand)


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _frame(name, labels):
    return {"name": name, "url": "items/" + name, "videoName": "", "timestamp": 0,
            "attributes": {}, "sensor": -1, "labels": labels}


@pytest.fixture
def env(tmp_path, monkeypatch):
    orig = tmp_path / "orig"
    orig.mkdir()
    curve = _label("c1", "curb_road_edge", [[0, 0], [30, 0], [60, 30], [90, 30]], "LCCL")
    frames = [
        _frame("f1.png", [_label("a", "solid_single_line", [[0, 0], [100, 0]]),
                          _label("b", "solid_single_line", [[0, 50], [100, 50]]), curve]),
        _frame("f2.png", [_label("h", "dashed_white_line", [[0, 9], [100, 9]])]),
        _frame("f3.png", [_label("z", "solid_single_line", [[0, 80], [100, 80]])]),
    ]
    (orig / "exportA.json").write_text(json.dumps({"frames": frames, "config": {"k": 1}}))
    monkeypatch.setattr(fixmod, "ORIGINALS_DIR", orig)
    out = tmp_path / "out"
    out.mkdir()
    return orig, out, frames


def _setup(out, issues, marks):
    write_issues(out / "issues_curb_soft.jsonl", [i for i in issues if i.issue_type == "curb_soft"])
    write_issues(out / "issues_position.jsonl", [i for i in issues if i.issue_type == "position"])
    write_marks(out / "marks.json", {i.key: m for i, m in zip(issues, marks)})


def test_apply_only_real_marked(env):
    orig, out, _ = env
    i1 = _issue("exportA", "f1.png", "a", "curb_soft", {"op": "set_category", "to": "curb_road_edge"})
    i2 = _issue("exportA", "f3.png", "z", "curb_soft", {"op": "delete"})
    _setup(out, [i1, i2], ["real", "false"])
    r = apply("curb_soft", out, 1)
    assert r.changed == {"exportA": ["f1.png"]}
    assert r.hand == {}
    fixed = json.load(open(out / "work" / "exportA_fix_curb_soft_v1.json"))
    by = {f["name"]: f for f in fixed["frames"]}
    assert by["f1.png"]["labels"][0]["category"] == "curb_road_edge"
    assert by["f1.png"]["labels"][0]["id"] == "a"
    assert len(by["f3.png"]["labels"]) == 1 and len(fixed["frames"]) == 3
    items = json.load(open(out / "rounds" / "exportA_fix_curb_soft_v1_items.json"))
    assert [f["name"] for f in items["frames"]] == ["f1.png"] and items["config"] == {"k": 1}
    ch = json.load(open(out / "rounds" / "exportA_fix_curb_soft_v1_changes.json"))
    rec = ch["f1.png"]["a"]
    assert rec["before"]["category"] == "solid_single_line"
    assert rec["after"]["category"] == "curb_road_edge"
    assert (out / "work" / "exportA.json").exists()


def test_originals_untouched(env):
    orig, out, _ = env
    i1 = _issue("exportA", "f1.png", "a", "curb_soft", {"op": "delete"})
    _setup(out, [i1], ["real"])
    before = _sha(orig / "exportA.json")
    apply("curb_soft", out, 1)
    assert _sha(orig / "exportA.json") == before


def test_delete_and_drop_vertices(env):
    orig, out, _ = env
    i1 = _issue("exportA", "f1.png", "a", "curb_soft", {"op": "delete"})
    i2 = _issue("exportA", "f1.png", "b", "curb_soft",
                {"op": "drop_vertices", "poly_index": 0, "indices": [0]})
    _setup(out, [i1, i2], ["real", "real"])
    apply("curb_soft", out, 1)
    fixed = json.load(open(out / "work" / "exportA_fix_curb_soft_v1.json"))
    labels = fixed["frames"][0]["labels"]
    assert [l["id"] for l in labels] == ["b", "c1"]
    assert labels[0]["poly2d"][0]["vertices"] == [[100, 50]]
    assert labels[0]["poly2d"][0]["types"] == "L"
    ch = json.load(open(out / "rounds" / "exportA_fix_curb_soft_v1_changes.json"))
    assert ch["f1.png"]["a"]["after"] is None


def test_hand_fix_frame_included_unchanged(env):
    orig, out, frames = env
    i = _issue("exportA", "f2.png", "h", "position", None, hand=True)
    _setup(out, [i], ["real"])
    r = apply("position", out, 1)
    assert r.hand == {"exportA": ["f2.png"]} and r.changed == {}
    items = json.load(open(out / "rounds" / "exportA_fix_position_v1_items.json"))
    assert len(items["frames"]) == 1
    assert items["frames"][0]["labels"] == frames[1]["labels"]


def test_shift_fix_on_curve_keeps_shape(env):
    orig, out, frames = env
    poly = frames[0]["labels"][2]["poly2d"][0]
    pts, nrm = resample(dense(poly))
    offs = np.full(len(pts), 4.0)
    i = _issue("exportA", "f1.png", "c1", "position",
               {"op": "shift", "poly_index": 0, "offsets": offs.tolist()})
    _setup(out, [i], ["real"])
    r = apply("position", out, 1)
    assert r.changed == {"exportA": ["f1.png"]}
    fixed = json.load(open(out / "work" / "exportA_fix_position_v1.json"))
    new = fixed["frames"][0]["labels"][2]["poly2d"][0]
    assert new["types"] == "LCCL"
    target = pts + offs[:, None] * nrm
    d = dense(new)
    dist = np.min(np.linalg.norm(d[None, :, :] - target[:, None, :], axis=2), axis=1)
    assert dist.max() < 1.5


def test_working_copy_copies_once(env):
    orig, out, _ = env
    p = working_copy("exportA", out)
    assert p == out / "work" / "exportA.json"
    p.write_text(json.dumps({"frames": [], "config": {}}))
    working_copy("exportA", out)
    assert json.load(open(p))["frames"] == []
