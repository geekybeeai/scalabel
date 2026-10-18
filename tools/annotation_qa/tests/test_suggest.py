"""Suggestion-only rounds: fix --suggest, verify on the proposal, View B, merge."""
import json

import cv2
import numpy as np
import pytest

from annotation_qa import fix as fixmod
from annotation_qa import verify
from annotation_qa.exclude import read_excluded
from annotation_qa.fix import FixRefused, apply
from annotation_qa.geometry import dense, resample
from annotation_qa.issues import Issue, write_issues, write_marks
from annotation_qa.merge import merge_reviewed
from annotation_qa.viewb import build_rows, render_crop

STEM = "exportA"
TAG = f"{STEM}_fix_position_suggest_v1"


def _label(lid, cat, verts):
    return {"id": lid, "category": cat, "attributes": {}, "manualShape": True,
            "poly2d": [{"vertices": verts, "types": "L" * len(verts), "closed": False}],
            "box2d": None, "box3d": None}


def _frame(name, labels):
    return {"name": name, "url": "f1.png", "videoName": "", "timestamp": 0,
            "attributes": {}, "sensor": -1, "labels": labels}


def _issue(frame, lid, fix=None, hand=False, reason="r"):
    return Issue("position", STEM, frame, lid, "solid_single_line", 0.9, reason, (50.0, 60.0),
                 fix, hand)


@pytest.fixture
def env(tmp_path, monkeypatch):
    orig = tmp_path / "orig"
    orig.mkdir()
    (tmp_path / "local-data").mkdir()
    img = np.zeros((400, 400, 3), np.uint8)
    img[10:390, 10:390] = (110, 110, 110)
    img[198:203, 10:390] = (230, 230, 230)
    cv2.imwrite(str(tmp_path / "local-data" / "f1.png"), img)
    frames = [_frame("f1.png", [_label("a", "solid_single_line", [[20, 190], [180, 190]]),
                                _label("b", "solid_single_line", [[20, 300], [180, 300]])]),
              _frame("f2.png", [_label("h", "dashed_white_line", [[20, 90], [180, 90]])]),
              _frame("f3.png", [_label("z", "solid_single_line", [[20, 80], [180, 80]])])]
    (orig / f"{STEM}.json").write_text(json.dumps({"frames": frames, "config": {}}))
    monkeypatch.setattr(fixmod, "ORIGINALS_DIR", orig)
    monkeypatch.setattr(fixmod, "calibration_refusal", lambda *a: None)
    monkeypatch.setattr(fixmod, "DISABLED_FOR_ROUNDS", frozenset())
    out = tmp_path / "out"
    out.mkdir()
    n = len(resample(dense(frames[0]["labels"][0]["poly2d"][0]))[0])
    i1 = _issue("f1.png", "a", {"op": "shift", "poly_index": 0, "offsets": [10.0] * n})
    i2 = _issue("f2.png", "h", None, hand=True, reason="draw it by hand")
    i3 = _issue("f3.png", "z", {"op": "delete"})
    write_issues(out / "issues_position.jsonl", [i1, i2, i3])
    write_marks(out / "marks.json", {i1.key: "real", i2.key: "real", i3.key: "false"})
    return tmp_path, out, frames, (i1, i2, i3)


def _build(out):
    return apply("position", out, 1, name="position_suggest", suggest=True)


def _read(out, suffix):
    return json.loads((out / "rounds" / f"{TAG}_{suffix}.json").read_text())


def test_suggest_round_leaves_labels_unchanged(env):
    tmp, out, frames, _ = env
    r = _build(out)
    assert r.suggest and r.tags == [TAG]
    items = _read(out, "items")
    assert [f["name"] for f in items["frames"]] == ["f1.png", "f2.png"]  # f3 not real-marked
    by = {f["name"]: f for f in frames}
    for f in items["frames"]:
        assert f["labels"] == by[f["name"]]["labels"]
    work = json.loads((out / "work" / f"{TAG}.json").read_text())
    assert {f["name"]: f["labels"] for f in work["frames"]} == {n: f["labels"] for n, f in by.items()}
    ch = _read(out, "changes")
    assert ch["suggest"] is True
    rec = ch["f1.png"]["a"]
    assert "after" not in rec and rec["before"]["poly2d"] == frames[0]["labels"][0]["poly2d"]
    assert rec["proposed"]["poly2d"] != rec["before"]["poly2d"] and rec["anchor"]
    hand = ch["f2.png"]["h"]
    assert hand["proposed"] is None and hand["hand"] and hand["reason"] == "draw it by hand"
    assert _read(out, "base")["mode"] == "suggest"


def test_suggest_guards_still_apply(env):
    tmp, out, _, _ = env
    _build(out)
    with pytest.raises(FixRefused):  # unmerged earlier round of the stem
        apply("position", out, 2, name="position_suggest", suggest=True)
    with pytest.raises(FixRefused):
        apply("position", out, 1, name="position_suggest", suggest=True)


def test_verify_scores_proposed_geometry(env):
    tmp, out, _, _ = env
    _build(out)
    v = verify.verify_round(out, next(r for r in verify.round_files(out) if r["tag"] == TAG), tmp)
    assert v["f1.png"]["a"]["verdict"] in ("ok", "check", "bad")
    assert "after_score" in v["f1.png"]["a"]  # scored like a shift
    assert v["f2.png"]["h"]["verdict"] == "check"
    assert "draw it by hand" in v["f2.png"]["h"]["reasons"][0]
    verify.run(out, TAG, False, tmp)
    assert (out / "rounds" / f"{TAG}_verify.json").exists()


def test_viewb_rows_flag_suggest_and_no_revert(env):
    tmp, out, _, _ = env
    _build(out)
    verify.run(out, TAG, False, tmp)
    rows = {r["frame_name"]: r for r in build_rows(out, tmp)}
    c = rows["f1.png"]["changes"][0]
    assert c["suggest"] and not c["reverted"] and "nothing changed" in c["badge"]
    assert "side=suggest" in c["suggest_crop"] and "x=" in c["link"] and "label=a" in c["link"]
    h = rows["f2.png"]["changes"][0]
    assert h["hand_fix"] and "draw it by hand" in h["what"]
    crop = render_crop(out, tmp, STEM, "f1.png", "a", TAG, "suggest")
    assert crop is not None and crop.exists()
    from annotation_qa.review_server import ReviewState
    with pytest.raises(ValueError, match="nothing to revert"):
        ReviewState(out, tmp, tmp / "orig").set_revert(TAG, "f1.png", "a", True)


def _review(tmp, out, edit):
    items = _read(out, "items")
    for f in items["frames"]:
        if f["name"] == "f1.png" and edit:
            f["labels"][0]["poly2d"][0]["vertices"][0][1] += 5
    p = tmp / "reviewed.json"
    p.write_text(json.dumps(items))
    return p


def test_merge_copies_user_edits_and_excludes_keys(env):
    tmp, out, frames, (i1, i2, _) = env
    _build(out)
    r = merge_reviewed(STEM, _review(tmp, out, True), out, TAG)
    assert r.suggest and r.reverted_applied == 0 and r.excluded_added == 2
    work = {f["name"]: f for f in json.loads((out / "work" / f"{STEM}.json").read_text())["frames"]}
    assert work["f1.png"]["labels"][0]["poly2d"][0]["vertices"][0] == [20, 195]
    assert work["f1.png"]["labels"][1] == frames[0]["labels"][1]
    assert work["f2.png"]["labels"] == frames[1]["labels"]
    ex = read_excluded(out)
    assert ex[i1.key]["reason"] == "handled by user in suggestion round"
    assert ex[i2.key]["reason"] == "user left as is (suggestion declined)"
    rows = {r["frame_name"]: r for r in build_rows(out, tmp)}
    assert rows["f1.png"]["changes"][0]["review"] == "changed by user"
    assert rows["f2.png"]["changes"][0]["review"] == "left as is"


def test_merge_keeps_base_check(env):
    tmp, out, _, _ = env
    _build(out)
    wc = out / "work" / f"{STEM}.json"
    d = json.loads(wc.read_text())
    d["frames"][0]["labels"].pop()
    wc.write_text(json.dumps(d))
    from annotation_qa.merge import MergeRefused
    with pytest.raises(MergeRefused, match="work copy changed"):
        merge_reviewed(STEM, _review(tmp, out, False), out, TAG)
