"""Combined fix rounds: several issue types composed per label."""
import json

import numpy as np
import pytest

from annotation_qa import fix as fixmod
from annotation_qa import verify
from annotation_qa.fix import FixRefused, apply
from annotation_qa.geometry import dense, resample
from annotation_qa.issues import write_issues, write_marks
from annotation_qa.rounds import round_files
from annotation_qa.viewb import frame_specs, ops_text
from test_fix import _issue, env  # noqa: F401  (env is a fixture)

SOFT, EDGE, PAINT = "curb_soft", "edge_position", "edge_on_paint"


def _shift_fix(label, off):
    pts, _ = resample(dense(label["poly2d"][0]))
    return {"op": "shift", "poly_index": 0, "offsets": np.full(len(pts), off).tolist()}


def _setup(out, issues, marks=None):
    by = {}
    for i in issues:
        by.setdefault(i.issue_type, []).append(i)
    for t, lst in by.items():
        write_issues(out / f"issues_{t}.jsonl", lst)
    write_marks(out / "marks.json", {i.key: (marks or ["real"] * len(issues))[k]
                                     for k, i in enumerate(issues)})


def _changes(out, tag):
    return json.loads((out / "rounds" / f"{tag}_changes.json").read_text())


def _label_after(out, tag, fname_idx, lid):
    work = json.loads((out / "work" / f"{tag}.json").read_text())
    return next(l for l in work["frames"][fname_idx]["labels"] if l["id"] == lid)


def test_compose_set_category_and_shift(env):
    orig, out, frames = env
    lab = frames[0]["labels"][0]  # "a": horizontal line y=0
    cat = _issue("exportA", "f1.png", "a", SOFT, {"op": "set_category", "to": "curb_road_edge"})
    sh = _issue("exportA", "f1.png", "a", PAINT, _shift_fix(lab, 8.0))
    cat.confidence, sh.confidence = 0.5, 0.9
    _setup(out, [cat, sh])
    r = apply(f"{SOFT},{PAINT}", out, 1, name="edges")
    assert r.tags == ["exportA_fix_edges_v1"] and r.skipped == []
    tag = r.tags[0]
    after = _label_after(out, tag, 0, "a")
    assert after["category"] == "curb_road_edge"
    ys = np.asarray(dense(after["poly2d"][0]))[:, 1]
    assert np.abs(np.abs(ys) - 8.0).max() < 1.5  # shifted by ~8 px (either normal side)
    rec = _changes(out, tag)["f1.png"]["a"]
    assert rec["issue_types"] == [SOFT, PAINT] and rec["ops"] == ["set_category", "shift"]
    assert "issue_type" not in rec
    assert rec["before"]["category"] == "solid_single_line"
    assert rec["after"]["category"] == "curb_road_edge"
    assert [x["tag"] for x in round_files(out)] == [tag]
    assert round_files(out)[0]["type"] == "edges"


def test_default_name_joins_types(env):
    orig, out, frames = env
    a = _issue("exportA", "f1.png", "a", SOFT, {"op": "set_category", "to": "curb_road_edge"})
    b = _issue("exportA", "f3.png", "z", PAINT, {"op": "delete"})
    _setup(out, [a, b])
    r = apply([SOFT, PAINT], out, 1)
    assert r.tags == [f"exportA_fix_{SOFT}+{PAINT}_v1"]
    assert (out / "rounds" / f"{r.tags[0]}_items.json").exists()


def test_delete_wins_and_is_recorded_alone(env):
    orig, out, frames = env
    lab = frames[0]["labels"][0]
    cat = _issue("exportA", "f1.png", "a", SOFT, {"op": "set_category", "to": "curb_road_edge"})
    dele = _issue("exportA", "f1.png", "a", PAINT, {"op": "delete"})
    cat.confidence, dele.confidence = 0.99, 0.1
    _setup(out, [cat, dele])
    r = apply(f"{SOFT},{PAINT}", out, 1)
    rec = _changes(out, r.tags[0])["f1.png"]["a"]
    assert rec["after"] is None and rec["ops"] == ["delete"] and rec["issue_types"] == [PAINT]
    assert len(r.skipped) == 1 and "deleted" in r.skipped[0]
    work = json.loads((out / "work" / f"{r.tags[0]}.json").read_text())
    assert "a" not in [l["id"] for l in work["frames"][0]["labels"]]
    assert lab  # untouched original object


def test_conflicting_geometry_keeps_higher_confidence(env):
    orig, out, frames = env
    lab = frames[0]["labels"][0]
    s1 = _issue("exportA", "f1.png", "a", EDGE, _shift_fix(lab, 6.0))
    s2 = _issue("exportA", "f1.png", "a", PAINT, _shift_fix(lab, -8.0))
    s1.confidence, s2.confidence = 0.4, 0.8
    _setup(out, [s1, s2])
    r = apply(f"{EDGE},{PAINT}", out, 1)
    assert len(r.skipped) == 1 and "conflicting geometry fix" in r.skipped[0]
    assert EDGE in r.skipped[0]
    rec = _changes(out, r.tags[0])["f1.png"]["a"]
    assert rec["issue_types"] == [PAINT] and rec["ops"] == ["shift"]


def test_gate_requires_every_type(env, monkeypatch):
    orig, out, frames = env
    asked = []

    def gate(issues, marks, t, o):
        asked.append(t)
        return None if t == SOFT else f"{t} calibration FAIL"
    monkeypatch.setattr(fixmod, "calibration_refusal", gate)
    a = _issue("exportA", "f1.png", "a", SOFT, {"op": "set_category", "to": "curb_road_edge"})
    b = _issue("exportA", "f3.png", "z", PAINT, {"op": "delete"})
    _setup(out, [a, b])
    with pytest.raises(FixRefused, match=f"{PAINT} calibration FAIL"):
        apply(f"{SOFT},{PAINT}", out, 1)
    assert asked == [SOFT, PAINT]
    r = apply(f"{SOFT},{PAINT}", out, 1, force=True)
    assert r.forced and r.tags


def test_only_real_marked_in_combined(env):
    orig, out, frames = env
    a = _issue("exportA", "f1.png", "a", SOFT, {"op": "set_category", "to": "curb_road_edge"})
    b = _issue("exportA", "f1.png", "a", PAINT, {"op": "delete"})
    _setup(out, [a, b], ["real", "false"])
    r = apply(f"{SOFT},{PAINT}", out, 1)
    rec = _changes(out, r.tags[0])["f1.png"]["a"]
    assert rec["ops"] == ["set_category"] and rec["after"]["category"] == "curb_road_edge"


def test_single_type_round_unchanged(env):
    orig, out, frames = env
    a = _issue("exportA", "f1.png", "a", SOFT, {"op": "set_category", "to": "curb_road_edge"})
    _setup(out, [a])
    r = apply(SOFT, out, 1)
    assert r.tags == [f"exportA_fix_{SOFT}_v1"]
    rec = _changes(out, r.tags[0])["f1.png"]["a"]
    assert rec["issue_type"] == SOFT and rec["issue_types"] == [SOFT] and rec["ops"] == ["set_category"]


def test_verify_worst_of_parts(monkeypatch):
    before = {"category": "without_curb_road_edge", "poly2d": []}
    after = {"category": "curb_road_edge", "poly2d": []}
    rec = {"before": before, "after": after, "issue_types": [SOFT, PAINT],
           "ops": ["set_category", "shift"]}
    monkeypatch.setattr(verify, "_set_category",
                        lambda i, r: {"verdict": "ok", "reasons": ["class fine"]})
    for geo, want in (("bad", "bad"), ("check", "check"), ("ok", "ok")):
        monkeypatch.setattr(verify, "_shift",
                            lambda *a, g=geo: {"verdict": g, "reasons": [f"geo {g}"]})
        v = verify.verify_combined(None, {}, rec, {}, "a")
        assert v["verdict"] == want
        assert any("class fine" in r for r in v["reasons"]) and any(f"geo {geo}" in r for r in v["reasons"])
    monkeypatch.setattr(verify, "_set_category",
                        lambda i, r: {"verdict": "check", "reasons": ["weak"]})
    monkeypatch.setattr(verify, "_shift", lambda *a: {"verdict": "ok", "reasons": ["geo"]})
    assert verify.verify_combined(None, {}, rec, {}, "a")["verdict"] == "check"


def test_ops_text_and_highlight_colours(env):
    line = lambda y: [{"vertices": [[0, y], [100, y]], "types": "LL", "closed": False}]  # noqa: E731
    b = {"category": "without_curb_road_edge", "poly2d": line(10)}
    a = {"category": "curb_road_edge", "poly2d": line(16)}
    assert ops_text(b, a) == "soft edge → CURB + moved 6 px"
    assert ops_text(b, {**a, "poly2d": line(10)}) == "soft edge → CURB"
    assert ops_text({**b, "category": "curb_road_edge"}, a) == ""

    orig, out, frames = env
    fr = {"name": "f1.png", "url": "items/f1.png", "labels": [{"id": "a", **a}]}
    tag = "exportA_fix_edges_v1"
    (out / "rounds").mkdir(exist_ok=True)
    (out / "rounds" / f"{tag}_changes.json").write_text(json.dumps(
        {"f1.png": {"a": {"before": b, "after": a, "issue_types": [SOFT, PAINT],
                          "ops": ["set_category", "shift"]}}}))
    (out / "rounds" / f"{tag}_items.json").write_text(json.dumps({"frames": [fr], "config": {}}))
    specs = frame_specs(out, "exportA", "f1.png")
    # both sides use the class colour of their own side (no magenta)
    assert specs[(tag, "a", "before")]["issue"].highlight_category == "without_curb_road_edge"
    assert specs[(tag, "a", "after")]["issue"].highlight_category == "curb_road_edge"


def test_exclude_combined_round_keys_match_issues(env):
    from annotation_qa.exclude import exclude_round, read_excluded
    orig, out, frames = env
    lab = frames[0]["labels"][0]
    cat = _issue("exportA", "f1.png", "a", SOFT, {"op": "set_category", "to": "curb_road_edge"})
    sh = _issue("exportA", "f1.png", "a", PAINT, _shift_fix(lab, 8.0))
    _setup(out, [cat, sh])
    tag = apply(f"{SOFT},{PAINT}", out, 1, name="edges").tags[0]
    assert tag == "exportA_fix_edges_v1"
    (out / "rounds" / f"{tag}_verify.json").write_text(json.dumps(
        {"f1.png": {"a": {"verdict": "bad", "reasons": ["moved wrong"]}}}))
    assert exclude_round(out, tag, "bad") == 2  # one key per real issue type, not "edges"
    assert set(read_excluded(out)) == {cat.key, sh.key}
    assert all(v["round"] == tag for v in read_excluded(out).values())
    assert exclude_round(out, tag, "bad") == 0  # idempotent
