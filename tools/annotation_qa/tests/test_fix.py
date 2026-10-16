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
                          _label("b", "solid_single_line", [[0, 50], [50, 50], [100, 50]]), curve]),
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
    assert labels[0]["poly2d"][0]["vertices"] == [[50, 50], [100, 50]]
    assert labels[0]["poly2d"][0]["types"] == "LL"
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


def _fixed(out, itype="position"):
    return json.load(open(out / "work" / f"exportA_fix_{itype}_v1.json"))


def test_bad_shift_skipped_not_aborting(env, capsys):
    orig, out, _ = env
    bad = _issue("exportA", "f1.png", "a", "position",
                 {"op": "shift", "poly_index": 0, "offsets": [1.0, 2.0]})  # wrong length
    good = _issue("exportA", "f3.png", "z", "position", {"op": "delete"})
    _setup(out, [bad, good], ["real", "real"])
    r = apply("position", out, 1)
    assert r.changed == {"exportA": ["f3.png"]}
    assert len(r.skipped) == 1 and "a" in r.skipped[0] and "offsets" in r.skipped[0]
    assert "skip" in capsys.readouterr().out.lower()
    assert [f["name"] for f in _fixed(out)["frames"]] == ["f1.png", "f2.png", "f3.png"]


def test_unknown_op_and_missing_label_skipped(env):
    orig, out, _ = env
    i1 = _issue("exportA", "f1.png", "a", "position", {"op": "explode"})
    i2 = _issue("exportA", "f1.png", "nope", "position", {"op": "delete"})
    i3 = _issue("exportA", "f3.png", "z", "position", {"op": "delete"})
    _setup(out, [i1, i2, i3], ["real"] * 3)
    r = apply("position", out, 1)
    assert r.changed == {"exportA": ["f3.png"]}
    assert len(r.skipped) == 2
    assert any("explode" in s for s in r.skipped) and any("nope" in s for s in r.skipped)


def test_two_issues_same_label_only_first_by_confidence(env):
    orig, out, _ = env
    lo = _issue("exportA", "f1.png", "a", "position", {"op": "delete"})
    lo.confidence = 0.3
    hi = _issue("exportA", "f1.png", "a", "position",
                {"op": "drop_vertices", "poly_index": 0, "indices": []})
    hi.confidence = 0.9
    hi.fix = {"op": "set_category", "to": "dashed_white_line"}
    _setup(out, [lo, hi], ["real", "real"])
    r = apply("position", out, 1)
    labels = {l["id"]: l for l in _fixed(out)["frames"][0]["labels"]}
    assert labels["a"]["category"] == "dashed_white_line"  # high-confidence one applied
    assert len(r.skipped) == 1 and "another fix already applied to this label" in r.skipped[0]


def _drop(lid, indices, poly_index=0):
    return _issue("exportA", "f1.png", lid, "position",
                  {"op": "drop_vertices", "poly_index": poly_index, "indices": indices})


@pytest.mark.parametrize("indices", [[5], [-1], [0, 1, 2], [1, 3]])
def test_drop_vertices_invalid_skipped(env, indices):
    # label "c1" is LCCL (indices 1,2 control; 3 end; 0 start); "b" has 3 L vertices
    orig, out, _ = env
    lid = "b" if indices in ([5], [-1], [0, 1, 2]) else "c1"
    _setup(out, [_drop(lid, indices)], ["real"])
    r = apply("position", out, 1)
    assert len(r.skipped) == 1 and r.changed == {}
    labels = {l["id"]: l for l in _fixed(out)["frames"][0]["labels"]}
    assert len(labels["b"]["poly2d"][0]["vertices"]) == 3
    assert labels["c1"]["poly2d"][0]["types"] == "LCCL"


def test_drop_vertices_never_below_two(env):
    orig, out, _ = env
    _setup(out, [_drop("b", [0, 1])], ["real"])
    r = apply("position", out, 1)
    assert len(r.skipped) == 1 and "fewer than 2" in r.skipped[0]


def test_drop_vertices_valid_l_vertex_around_curve(env, ):
    orig, out, frames = env
    # polyline L L C C L L: drop the leading pure-L vertex only
    lab = _label("m", "solid_single_line",
                 [[-20, 0], [0, 0], [30, 0], [60, 30], [90, 30], [120, 30]], "LLCCLL")
    frames[1]["labels"].append(lab)
    (orig / "exportA.json").write_text(json.dumps({"frames": frames, "config": {}}))
    i = _issue("exportA", "f2.png", "m", "position",
               {"op": "drop_vertices", "poly_index": 0, "indices": [0]})
    _setup(out, [i], ["real"])
    r = apply("position", out, 1)
    assert r.skipped == [] and r.changed == {"exportA": ["f2.png"]}
    m = [l for l in _fixed(out)["frames"][1]["labels"] if l["id"] == "m"][0]
    assert m["poly2d"][0]["types"] == "LCCLL"
