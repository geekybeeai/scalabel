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
    # calibration is tested on its own (test_calibration_gate_*); pass it here
    monkeypatch.setattr(fixmod, "calibration_refusal", lambda *a: None)
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
    assert isinstance(rec["anchor"], list) and len(rec["anchor"]) == 2
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
    assert not (out / "rounds").exists() or not list((out / "rounds").iterdir())  # I3
    labels = {l["id"]: l for l in json.load(open(out / "work" / "exportA.json"))["frames"][0]["labels"]}
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


# ---- I2: partial shifts ----
def _smoothed(m, lo, hi, off, width=15):
    """Detector-style offsets: ``off`` on resampled rows [lo, hi), box-smoothed."""
    per = np.zeros(m)
    per[lo:hi] = off
    pad = width // 2
    return np.convolve(np.pad(per, pad, mode="edge"), np.ones(width) / width, mode="valid")


def _long_line_env(env, offsets_fn):
    orig, out, frames = env
    frames[2]["labels"].append(_label("long", "solid_single_line", [[0, 300], [600, 300]]))
    (orig / "exportA.json").write_text(json.dumps({"frames": frames, "config": {}}))
    poly = frames[2]["labels"][-1]["poly2d"][0]
    pts, nrm = resample(dense(poly))
    offs = offsets_fn(len(pts))
    i = _issue("exportA", "f3.png", "long", "position",
               {"op": "shift", "poly_index": 0, "offsets": offs.tolist()})
    _setup(out, [i], ["real"])
    return out, pts, nrm, offs


def _new_long(out):
    f3 = _fixed(out)["frames"][2]
    return next(l for l in f3["labels"] if l["id"] == "long")["poly2d"][0]


def _residual(new, pts, nrm, offs):
    from annotation_qa.geometry import _dist_to_polyline
    return _dist_to_polyline(pts + offs[:, None] * nrm, np.asarray(dense(new), float))


def test_partial_shift_middle_only_moves_middle(env):
    out, pts, nrm, offs = _long_line_env(env, lambda m: _smoothed(m, m // 3, 2 * m // 3, 12.0))
    r = apply("position", out, 1)
    assert r.changed == {"exportA": ["f3.png"]} and r.skipped == []
    new = _new_long(out)
    res = _residual(new, pts, nrm, offs)
    assert res.max() < 1.5  # the middle lands on its target all along
    assert np.allclose(new["vertices"][0], [0, 300], atol=1.5)  # the ends stay put
    assert np.allclose(new["vertices"][-1], [600, 300], atol=1.5)
    assert set(new["types"]) == {"L"} and len(new["vertices"]) > 2


def test_partial_shift_end_only_keeps_far_end(env):
    out, pts, nrm, offs = _long_line_env(env, lambda m: _smoothed(m, 0, m // 4, 12.0))
    r = apply("position", out, 1)
    assert r.skipped == []
    new = _new_long(out)
    assert abs(new["vertices"][0][1] - (300 + 12 * nrm[0, 1])) < 1.5  # near end moved
    assert np.allclose(new["vertices"][-1], [600, 300], atol=1.0)  # far end did not
    assert _residual(new, pts, nrm, offs).max() < 1.5  # and the line is not tilted


def test_unrepresentable_shift_becomes_hand_frame(env):
    orig, out, frames = env
    poly = frames[0]["labels"][2]["poly2d"][0]  # the LCCL curve
    pts, _ = resample(dense(poly))
    offs = 8.0 * (-1.0) ** np.arange(len(pts))  # flips every sample: a Bezier cannot follow
    i = _issue("exportA", "f1.png", "c1", "position",
               {"op": "shift", "poly_index": 0, "offsets": offs.tolist()})
    _setup(out, [i], ["real"])
    r = apply("position", out, 1)
    assert r.changed == {} and r.hand == {"exportA": ["f1.png"]}
    assert len(r.skipped) == 1 and "partial shift not representable" in r.skipped[0]
    items = json.load(open(out / "rounds" / "exportA_fix_position_v1_items.json"))
    assert [f["name"] for f in items["frames"]] == ["f1.png"]
    c1 = next(l for l in items["frames"][0]["labels"] if l["id"] == "c1")
    assert c1["poly2d"] == frames[0]["labels"][2]["poly2d"]  # unchanged


# ---- C1: round guards ----
from annotation_qa.fix import FixRefused  # noqa: E402
from annotation_qa.issues import file_sha1, write_meta  # noqa: E402
from annotation_qa.merge import merge_reviewed  # noqa: E402


def _two_types(out):
    a = _issue("exportA", "f1.png", "a", "curb_soft", {"op": "set_category", "to": "curb_road_edge"})
    b = _issue("exportA", "f3.png", "z", "solid_dashed",
               {"op": "set_category", "to": "dashed_white_line"})
    write_issues(out / "issues_curb_soft.jsonl", [a])
    write_issues(out / "issues_solid_dashed.jsonl", [b])
    write_marks(out / "marks.json", {a.key: "real", b.key: "real"})


def _merge_round(out, tmp_path, tag):
    rev = tmp_path / f"{tag}_reviewed.json"
    rev.write_text((out / "rounds" / f"{tag}_items.json").read_text())  # accepted as proposed
    return merge_reviewed("exportA", rev, out, round_tag=tag)


def _rescanned(out, itype):
    """What a (since-merge) rescan records: the sha1 of the export it read."""
    write_meta(out, itype, {"exportA": {"sha1": file_sha1(out / "work" / "exportA.json"),
                                        "scanned_at": "2026-10-17T00:00:00+00:00"}})


def test_second_round_refused_until_first_is_merged(env):
    orig, out, _ = env
    _two_types(out)
    apply("curb_soft", out, 1)
    with pytest.raises(FixRefused, match="not merged yet: exportA_fix_curb_soft_v1"):
        apply("solid_dashed", out, 1)
    assert not (out / "rounds" / "exportA_fix_solid_dashed_v1_items.json").exists()


def test_second_round_allowed_after_merge_and_rescan(env, tmp_path):
    orig, out, _ = env
    _two_types(out)
    apply("curb_soft", out, 1)
    _merge_round(out, tmp_path, "exportA_fix_curb_soft_v1")
    with pytest.raises(FixRefused, match="re-scan first"):
        apply("solid_dashed", out, 1)  # its issues were detected before the merge
    _rescanned(out, "solid_dashed")
    r = apply("solid_dashed", out, 1)
    assert r.changed == {"exportA": ["f3.png"]} and r.forced == []
    # round 2 starts from round 1's merged output: the accepted curb change is kept
    fixed = {f["name"]: f for f in json.load(
        open(out / "work" / "exportA_fix_solid_dashed_v1.json"))["frames"]}
    assert fixed["f1.png"]["labels"][0]["category"] == "curb_road_edge"
    assert fixed["f3.png"]["labels"][0]["category"] == "dashed_white_line"


def test_existing_tag_refused(env):
    orig, out, _ = env
    _two_types(out)
    apply("curb_soft", out, 1)
    with pytest.raises(FixRefused, match="exportA_fix_curb_soft_v1 already exists"):
        apply("curb_soft", out, 1)


def test_stale_issues_refused(env):
    orig, out, _ = env
    _two_types(out)
    write_meta(out, "curb_soft", {"exportA": {"sha1": file_sha1(orig / "exportA.json")}})
    work = working_copy("exportA", out)
    work.write_text(json.dumps({"frames": [], "config": {}}))  # e.g. a merge since the scan
    with pytest.raises(FixRefused, match="re-scan first"):
        apply("curb_soft", out, 1)


def test_no_metadata_and_changed_work_copy_refused(env):
    orig, out, _ = env
    _two_types(out)
    apply("curb_soft", out, 1)  # no meta, untouched work copy: fine (scan read the original)
    working_copy("exportA", out).write_text(json.dumps({"frames": [], "config": {}}))
    with pytest.raises(FixRefused, match="no scan metadata"):
        apply("solid_dashed", out, 1, force=False)


def test_force_overrides_each_guard(env, capsys):
    orig, out, _ = env
    _two_types(out)
    apply("curb_soft", out, 1)
    r = apply("curb_soft", out, 1, force=True)  # existing tag
    assert any("already exists" in f for f in r.forced) and r.changed
    r = apply("solid_dashed", out, 1, force=True)  # unmerged earlier round
    assert any("not merged yet" in f for f in r.forced)
    assert (out / "rounds" / "exportA_fix_solid_dashed_v1_items.json").exists()


def test_forced_rebuild_drops_old_merge_marker(env, tmp_path):
    from annotation_qa.rounds import merged_tags
    orig, out, _ = env
    _two_types(out)
    apply("curb_soft", out, 1)
    _merge_round(out, tmp_path, "exportA_fix_curb_soft_v1")
    assert merged_tags(out)
    apply("curb_soft", out, 1, force=True)
    assert merged_tags(out) == set()


def test_no_round_files_for_stem_without_changes(env):
    orig, out, _ = env
    i = _issue("exportA", "f1.png", "nope", "curb_soft", {"op": "delete"})  # label not found
    _setup(out, [i], ["real"])
    r = apply("curb_soft", out, 1)
    assert r.changed == {} and r.hand == {} and r.tags == [] and len(r.skipped) == 1
    assert not list((out / "rounds").glob("*")) if (out / "rounds").exists() else True
    assert not (out / "work" / "exportA_fix_curb_soft_v1.json").exists()


# ---- I10: calibration gate ----
def _real_refusal(issues, marks, issue_type, out):
    from annotation_qa.issues import calibration_status, format_calibration
    st = calibration_status(out, issue_type, issues, marks)
    return None if st["status"] == "PASS" else format_calibration(issue_type, st)


def _many(out, n_issues, answers):
    """n_issues delete-issues on frame f3/z..; ``answers`` = marks for the first k of the sample."""
    from annotation_qa.issues import calibration_sample
    iss = [_issue("exportA", "f3.png", f"q{k}", "shape", {"op": "delete"}) for k in range(n_issues)]
    write_issues(out / "issues_shape.jsonl", iss)
    sample = calibration_sample(out, "shape", iss)
    write_marks(out / "marks.json", {i.key: a for i, a in zip(sample, answers)})
    return iss


def _old_sample(iss):
    """The pre-R25 algorithm, kept here to pin the first draw."""
    import random
    mine = sorted(iss, key=lambda i: i.key)
    return random.Random(0).sample(mine, 30) if len(mine) > 30 else mine


def test_calibration_status_numbers(tmp_path):
    from annotation_qa.issues import calibration_sample, calibration_status
    iss = [_issue("e", "f", f"q{k}", "shape") for k in range(50)]
    sample = calibration_sample(tmp_path, "shape", iss)
    assert len(sample) == 30 and sample == _old_sample(iss)  # first draw = old algorithm
    assert sample == calibration_sample(tmp_path, "shape", list(reversed(iss)))
    st = calibration_status(tmp_path, "shape", iss, {i.key: "real" for i in sample[:5]})
    assert st["status"] == "NOT CALIBRATED" and st["answered"] == 5 and st["n"] == 30
    marks = {i.key: ("real" if k < 14 else "false") for k, i in enumerate(sample[:20])}
    st = calibration_status(tmp_path, "shape", iss, marks)
    assert st["status"] == "PASS" and st["real"] == 14 and st["precision"] == pytest.approx(0.7)
    marks = {i.key: ("real" if k < 13 else "false") for k, i in enumerate(sample[:20])}
    assert calibration_status(tmp_path, "shape", iss, marks)["status"] == "FAIL"  # 13/20 < 2/3
    # marks outside the fixed sample do not count
    others = [i for i in iss if i not in sample]
    assert calibration_status(tmp_path, "shape", iss,
                              {i.key: "real" for i in others})["answered"] == 0
    # a type with fewer than 20 issues is judged once all of them are answered
    few = iss[:3]
    d = tmp_path / "few"
    assert calibration_status(d, "shape", few, {few[0].key: "real", few[1].key: "real"})[
        "status"] == "NOT CALIBRATED"
    assert calibration_status(d, "shape", few, {i.key: "real" for i in few})["status"] == "PASS"


def test_sample_persisted_file_and_stable_after_issue_change(tmp_path):
    from annotation_qa.issues import calibration_sample, calibration_status
    iss = [_issue("e", "f", f"q{k}", "shape") for k in range(50)]
    sample = calibration_sample(tmp_path, "shape", iss)
    meta = json.loads((tmp_path / "calibration" / "sample_shape.json").read_text())
    assert meta["type"] == "shape" and meta["keys"] == [i.key for i in sample]
    assert meta["n_issues_at_draw"] == 50 and meta["created"]
    marks = {i.key: "real" for i in sample[:25]}
    before = calibration_status(tmp_path, "shape", iss, marks)
    changed = iss[10:] + [_issue("e", "f", f"new{k}", "shape") for k in range(40)]  # rescan
    assert [i.key for i in calibration_sample(tmp_path, "shape", changed)] == \
        [i.key for i in sample if i in changed]
    live_keys = {i.key for i in changed}
    after = calibration_status(tmp_path, "shape", changed, marks)
    assert after["answered"] == 25 and after["status"] == before["status"] == "PASS"
    assert after["n"] == len([i for i in sample if i.key in live_keys or i.key in marks])


def test_vanished_answered_counts_unanswered_dropped(tmp_path):
    from annotation_qa.issues import calibration_sample, calibration_status
    iss = [_issue("e", "f", f"q{k}", "shape") for k in range(30)]
    sample = calibration_sample(tmp_path, "shape", iss)
    gone_answered, gone_open = sample[0], sample[1]
    live = [i for i in iss if i not in (gone_answered, gone_open)]
    marks = {gone_answered.key: "real"}
    st = calibration_status(tmp_path, "shape", live, marks)
    assert st["n"] == 29 and st["answered"] == 1 and st["real"] == 1  # open one dropped
    marks.update({i.key: "real" for i in live[:19]})
    st = calibration_status(tmp_path, "shape", live, marks)
    assert st["answered"] == 20 and st["need"] == 20 and st["status"] == "PASS"


def test_fix_gate_uses_persisted_sample(tmp_path):
    from annotation_qa.issues import calibration_sample
    iss = [_issue("e", "f", f"q{k}", "shape") for k in range(50)]
    sample = calibration_sample(tmp_path, "shape", iss)
    marks = {i.key: "real" for i in sample[:20]}
    assert _real_refusal(iss, marks, "shape", tmp_path) is None
    rescanned = iss[5:] + [_issue("e", "f", f"n{k}", "shape") for k in range(30)]
    assert _real_refusal(rescanned, marks, "shape", tmp_path) is None  # not reset

def test_fix_refused_unless_calibrated(env, monkeypatch):
    orig, out, _ = env
    monkeypatch.setattr(fixmod, "calibration_refusal", _real_refusal)
    _many(out, 25, ["real"] * 5)
    with pytest.raises(FixRefused, match=r"NOT CALIBRATED: answered 5/25"):
        apply("shape", out, 1)
    _many(out, 25, ["real"] * 14 + ["false"] * 6)
    r = apply("shape", out, 1)  # PASS: 14/20 real, so not refused
    assert r.forced == [] and r.skipped  # (the synthetic labels do not exist)


def test_cli_fix_prints_calibration_and_exits_nonzero(env, monkeypatch, capsys):
    from annotation_qa import __main__ as cli
    orig, out, _ = env
    monkeypatch.setattr(fixmod, "calibration_refusal", _real_refusal)
    _many(out, 25, ["real"] * 13 + ["false"] * 7)
    assert cli.main(["fix", "--type", "shape", "--version", "1", "--out", str(out)]) != 0
    text = capsys.readouterr().out
    assert "REFUSED" in text and "FAIL" in text and "answered 20/25" in text and "real 13" in text
    assert cli.main(["fix", "--type", "shape", "--version", "1", "--out", str(out),
                     "--force"]) == 0
    assert "forced" in capsys.readouterr().out.lower()


def test_exclude_and_apply_skips(env):
    from annotation_qa.exclude import exclude_round, read_excluded
    orig, out, _ = env
    i1 = _issue("exportA", "f1.png", "a", "curb_soft", {"op": "set_category", "to": "curb_road_edge"})
    i2 = _issue("exportA", "f3.png", "z", "curb_soft", {"op": "delete"})
    _setup(out, [i1, i2], ["real", "real"])
    apply("curb_soft", out, 1)
    tag = "exportA_fix_curb_soft_v1"
    (out / "rounds" / f"{tag}_verify.json").write_text(json.dumps({
        "f1.png": {"a": {"verdict": "bad", "reasons": ["x"]}},
        "f3.png": {"z": {"verdict": "ok", "reasons": []}}}))
    assert exclude_round(out, tag, "bad") == 1
    assert exclude_round(out, tag, "bad") == 0  # idempotent
    ex = read_excluded(out)
    assert list(ex) == [i1.key]
    assert ex[i1.key] == {"reason": "auto-fix rejected by verifier", "round": tag,
                          "verdict": "bad", "reasons": ["x"]}
    # next version: f1 excluded and left out; f3 was merged-free? simulate by merging marker-free force
    r = apply("curb_soft", out, 2, force=True)
    assert any(s.startswith(i1.key) and "excluded: auto-fix rejected by verifier" in s
               for s in r.skipped)
    assert "f1.png" not in r.changed.get("exportA", [])
    items = json.load(open(out / "rounds" / "exportA_fix_curb_soft_v2_items.json"))
    assert [f["name"] for f in items["frames"]] == ["f3.png"]


def test_excluded_card_badge(env):
    from annotation_qa.review_server import ReviewState
    orig, out, _ = env
    i1 = _issue("exportA", "f1.png", "a", "curb_soft")
    i2 = _issue("exportA", "f3.png", "z", "curb_soft")
    _setup(out, [i1, i2], ["real", "real"])
    (out / "excluded.json").write_text(json.dumps({i1.key: {"reason": "r"}}))
    cards = ReviewState(out, out, orig).cards("curb_soft")
    assert {c["key"]: c["auto_fix_rejected"] for c in cards} == {i1.key: True, i2.key: False}
