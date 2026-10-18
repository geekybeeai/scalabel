"""Second review (C2, I1-I3, merge minors): merge order / re-merge / changed-frame checks,
backups, archive-round, stale verify files, revert accounting and the review outcome."""

import json

import pytest

from annotation_qa import __main__ as cli
from annotation_qa import progress, reverts, rounds
from annotation_qa.archive import archive_round
from annotation_qa.exclude import exclude_round, read_excluded
from annotation_qa.fix import apply
from annotation_qa.issues import file_sha1, read_marks, write_issues, write_marks, write_meta
from annotation_qa.merge import CHANGED_MSG, MergeRefused, merge_reviewed
from annotation_qa.rounds import merge_markers, read_verdicts, record_merge
from annotation_qa.verify import write_verify
from test_fix import _issue, env  # noqa: F401  (env is a fixture)

STEM = "exportA"
CURB = {"op": "set_category", "to": "curb_road_edge"}


def _setup(out, issues, marks=None):
    by = {}
    for i in issues:
        by.setdefault(i.issue_type, []).append(i)
    for t, lst in by.items():
        write_issues(out / f"issues_{t}.jsonl", lst)
    write_marks(out / "marks.json", marks if marks is not None else {i.key: "real" for i in issues})


def _work(out):
    return json.loads((out / "work" / f"{STEM}.json").read_text())


def _lab(exp, fname, lid):
    f = next(f for f in exp["frames"] if f["name"] == fname)
    return next((l for l in f["labels"] if l["id"] == lid), None)


def _review(out, tag, tmp_path, edit=None):
    """The round's export as proposed, optionally edited by ``edit(frames_by_name)``."""
    exp = json.loads((out / "rounds" / f"{tag}_items.json").read_text())
    if edit:
        edit({f["name"]: f for f in exp["frames"]})
    p = tmp_path / f"{tag}.reviewed.json"
    p.write_text(json.dumps(exp))
    return p


def _rescanned(out, *types):
    for t in types:
        write_meta(out, t, {STEM: {"sha1": file_sha1(out / "work" / f"{STEM}.json"),
                                   "scanned_at": "2026-10-18T00:00:00+00:00"}})


def _round1(out):
    a = _issue(STEM, "f1.png", "a", "curb_soft", CURB)
    _setup(out, [a])
    return a, apply("curb_soft", out, 1).tags[0]


# ---- C2: base snapshot ----
def test_fix_writes_base_snapshot(env):
    orig, out, frames = env
    _, tag = _round1(out)
    base = rounds.read_base(out, tag)
    assert base["stem"] == STEM and base["tag"] == tag and base["built_at"]
    assert base["frames"] == {"f1.png": rounds.labels_sha1(frames[0]["labels"])}


# ---- C2: already merged / re-merge ----
def test_already_merged_refused_unless_remerge(env, tmp_path):
    orig, out, _ = env
    _, tag = _round1(out)
    rev = _review(out, tag, tmp_path)
    assert merge_reviewed(STEM, rev, out, tag).frames == 1
    with pytest.raises(MergeRefused, match="already merged.*--remerge"):
        merge_reviewed(STEM, rev, out, tag)
    assert len(merge_markers(out)) == 1
    # a re-merge accepts the labels its own last merge wrote (no changed-frame refusal)
    r = merge_reviewed(STEM, rev, out, tag, remerge=True)
    assert r.frames == 1 and not r.legacy
    assert [m["tag"] for m in merge_markers(out)] == [tag, tag] and merge_markers(out)[-1]["remerge"]


def test_remerge_of_old_round_after_later_round_refused(env, tmp_path):
    """Probe: merging round 1 again after round 2 was merged would drop round 2's delete."""
    orig, out, _ = env
    a, t1 = _round1(out)
    rev1 = _review(out, t1, tmp_path)
    merge_reviewed(STEM, rev1, out, t1)
    b = _issue(STEM, "f1.png", "b", "duplicates", {"op": "delete"})
    _setup(out, [b])
    _rescanned(out, "duplicates")
    t2 = apply("duplicates", out, 1).tags[0]
    merge_reviewed(STEM, _review(out, t2, tmp_path), out, t2)
    assert _lab(_work(out), "f1.png", "b") is None
    with pytest.raises(MergeRefused, match=f"{t2} of {STEM} was merged after"):
        merge_reviewed(STEM, rev1, out, t1, remerge=True)
    assert _lab(_work(out), "f1.png", "b") is None  # nothing written
    assert [m["tag"] for m in merge_markers(out)] == [t1, t2]


def test_other_round_merged_after_build_refused(env, tmp_path):
    orig, out, _ = env
    _, tag = _round1(out)
    record_merge(out, STEM, f"{STEM}_fix_shape_v9", tmp_path / "x.json", ["f3.png"])
    with pytest.raises(MergeRefused, match="was merged after that"):
        merge_reviewed(STEM, _review(out, tag, tmp_path), out, tag)


# ---- C2: changed frames ----
def _live_edit(out, fname, lid, **kv):
    w = _work(out)
    _lab(w, fname, lid).update(kv)
    (out / "work" / f"{STEM}.json").write_text(json.dumps(w))


def test_changed_round_frame_refused_then_allowed(env, tmp_path, capsys):
    orig, out, _ = env
    _, tag = _round1(out)
    _live_edit(out, "f1.png", "b", category="dashed_white_line")  # live-edit import
    before = (out / "work" / f"{STEM}.json").read_text()
    rev = _review(out, tag, tmp_path)
    with pytest.raises(MergeRefused) as e:
        merge_reviewed(STEM, rev, out, tag)
    assert CHANGED_MSG in str(e.value) and "f1.png" in str(e.value)
    assert (out / "work" / f"{STEM}.json").read_text() == before and merge_markers(out) == []
    r = merge_reviewed(STEM, rev, out, tag, allow_changed_frames=["f1.png"])
    assert r.frames == 1 and r.changed_allowed == ["f1.png"]
    assert any(CHANGED_MSG in w for w in r.warnings)
    assert merge_markers(out)[-1]["changed_frames_allowed"] == ["f1.png"]


def test_change_outside_round_frames_is_fine(env, tmp_path):
    orig, out, _ = env
    _, tag = _round1(out)
    _live_edit(out, "f2.png", "h", category="solid_single_line")
    assert merge_reviewed(STEM, _review(out, tag, tmp_path), out, tag).frames == 1


def test_cli_merge_flags(env, tmp_path, capsys):
    orig, out, _ = env
    _, tag = _round1(out)
    _live_edit(out, "f1.png", "b", category="dashed_white_line")
    rev = _review(out, tag, tmp_path)
    args = ["merge", "--stem", STEM, "--reviewed", str(rev), "--round", tag, "--out", str(out)]
    assert cli.main(args) == 2
    assert "work copy changed since the round was built" in capsys.readouterr().out
    assert cli.main(args + ["--allow-changed-frames", "f1.png,nope.png"]) == 0
    text = capsys.readouterr().out
    assert "merged 1 frames; reverted_applied=0 reverted_conflicts=0 unmatched=0" in text
    assert "not a changed frame" in text and "nope.png" in text
    assert cli.main(args) == 2 and "--remerge" in capsys.readouterr().out
    assert cli.main(args + ["--remerge"]) == 0


# ---- C2: backup ----
def test_merge_writes_backup_first(env, tmp_path):
    orig, out, _ = env
    _, tag = _round1(out)
    before = (out / "work" / f"{STEM}.json").read_bytes()
    r = merge_reviewed(STEM, _review(out, tag, tmp_path), out, tag)
    (b,) = (out / "backup" / "work").glob(f"{STEM}.*.json")
    assert str(b) == r.backup and b.read_bytes() == before
    assert (out / "work" / f"{STEM}.json").read_bytes() != before


# ---- C2: legacy rounds (built before base snapshots) ----
def test_legacy_round_skips_frame_check_but_keeps_order_checks(env, tmp_path):
    orig, out, _ = env
    _, tag = _round1(out)
    rounds.base_path(out, tag).unlink()  # e.g. the open position_v3 rounds
    _live_edit(out, "f1.png", "b", category="dashed_white_line")
    rev = _review(out, tag, tmp_path)
    r = merge_reviewed(STEM, rev, out, tag)
    assert r.legacy and r.frames == 1
    assert any("legacy round (no base snapshot): changed-frame check skipped" in w
               for w in r.warnings)
    assert list((out / "backup" / "work").glob(f"{STEM}.*.json"))  # backup still written
    with pytest.raises(MergeRefused, match="already merged"):
        merge_reviewed(STEM, rev, out, tag)
    # later-merged check: build time falls back to the round files' mtime
    record_merge(out, STEM, f"{STEM}_fix_shape_v9", tmp_path / "x.json", ["f3.png"])
    with pytest.raises(MergeRefused, match="was merged after that"):
        merge_reviewed(STEM, rev, out, tag, remerge=True)


# ---- I1: archive-round, stale verify files ----
def test_archive_round_moves_files_and_review_state(env, tmp_path):
    orig, out, _ = env
    _, tag = _round1(out)
    write_verify(out, tag, {"f1.png": {"a": {"verdict": "ok", "reasons": []}}})
    reverts.set_revert(out, tag, "f1.png", "a", True)
    reverts.set_revert(out, "other_fix_x_v1", "f", "l", True)
    progress.set_reviewed(out, tag, "f1.png", True)
    assert cli.main(["archive-round", "--round", tag, "--out", str(out)]) == 0
    dest = out / "backup" / "archived_rounds" / tag
    for k in ("items", "changes", "verify", "base"):
        assert not (out / "rounds" / f"{tag}_{k}.json").exists()
        assert (dest / "rounds" / f"{tag}_{k}.json").exists()
    assert not (out / "work" / f"{tag}.json").exists() and (dest / "work" / f"{tag}.json").exists()
    assert tag not in reverts.read_reverts(out) and "other_fix_x_v1" in reverts.read_reverts(out)
    assert tag not in progress.read_progress(out)
    assert tag in json.loads((dest / "reverts.json").read_text())
    assert tag in json.loads((dest / "viewb_progress.json").read_text())
    # the round can now be rebuilt from scratch: no stale verify / revert / progress
    r = apply("curb_soft", out, 1)
    assert r.tags == [tag] and read_verdicts(out, tag) is None
    assert reverts.round_reverts(out, tag) == {} and tag not in progress.read_progress(out)
    assert cli.main(["archive-round", "--round", "nope_fix_x_v1", "--out", str(out)]) == 2


def test_archive_keeps_merge_markers(env, tmp_path):
    orig, out, _ = env
    _, tag = _round1(out)
    merge_reviewed(STEM, _review(out, tag, tmp_path), out, tag)
    r = archive_round(out, tag)
    assert r["merged"] and rounds.merged_tags(out) == {(STEM, tag)}


def test_existing_review_state_blocks_rebuild(env):
    from annotation_qa.fix import FixRefused
    orig, out, _ = env
    _setup(out, [_issue(STEM, "f1.png", "a", "curb_soft", CURB)])
    tag = f"{STEM}_fix_curb_soft_v1"
    reverts.set_revert(out, tag, "f1.png", "a", True)  # left over from an earlier build
    with pytest.raises(FixRefused, match=r"reverts.json\[" + tag):
        apply("curb_soft", out, 1)


def test_verify_records_changes_hash_and_stale_file_is_ignored(env):
    orig, out, _ = env
    _, tag = _round1(out)
    write_verify(out, tag, {"f1.png": {"a": {"verdict": "bad", "reasons": ["x"]}}})
    raw = json.loads((out / "rounds" / f"{tag}_verify.json").read_text())
    assert raw["__meta__"]["changes_sha1"]
    assert read_verdicts(out, tag) == {"f1.png": {"a": {"verdict": "bad", "reasons": ["x"]}}}
    chg = out / "rounds" / f"{tag}_changes.json"
    chg.write_text(chg.read_text() + " ")  # a rebuilt round
    assert read_verdicts(out, tag) is None
    with pytest.raises(ValueError, match="stale"):
        exclude_round(out, tag)
    assert read_excluded(out) == {}


def test_legacy_verify_file_older_than_changes_is_ignored(env):
    import os
    orig, out, _ = env
    _, tag = _round1(out)
    v = out / "rounds" / f"{tag}_verify.json"
    v.write_text(json.dumps({"f1.png": {"a": {"verdict": "ok", "reasons": []}}}))
    assert read_verdicts(out, tag) is not None  # written after changes.json: current
    chg = out / "rounds" / f"{tag}_changes.json"
    st = v.stat()
    os.utime(chg, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert read_verdicts(out, tag) is None


def test_viewb_ignores_stale_verdicts(env, tmp_path):
    from annotation_qa.viewb import build_rows
    orig, out, _ = env
    _, tag = _round1(out)
    write_verify(out, tag, {"f1.png": {"a": {"verdict": "bad", "reasons": ["x"]}}})
    (c,) = build_rows(out, tmp_path)[0]["changes"]
    assert c["verdict"] == "bad"
    chg = out / "rounds" / f"{tag}_changes.json"
    chg.write_text(chg.read_text() + " ")
    (c,) = build_rows(out, tmp_path)[0]["changes"]
    assert c["verdict"] == "unverified"


# ---- I2: reverts ----
def test_reverts_with_unreadable_changes_refuse_merge(env, tmp_path):
    """Probe: the revert used to be dropped silently."""
    orig, out, _ = env
    _, tag = _round1(out)
    reverts.set_revert(out, tag, "f1.png", "a", True)
    (out / "rounds" / f"{tag}_changes.json").write_text("{corrupt")
    before = (out / "work" / f"{STEM}.json").read_text()
    with pytest.raises(MergeRefused, match="revert.*cannot be read"):
        merge_reviewed(STEM, _review(out, tag, tmp_path), out, tag)
    assert (out / "work" / f"{STEM}.json").read_text() == before and merge_markers(out) == []


def test_unmatched_reverts_counted(env, tmp_path, capsys):
    orig, out, _ = env
    _, tag = _round1(out)
    reverts.set_revert(out, tag, "f1.png", "a", True)
    reverts.set_revert(out, tag, "f1.png", "zz", True)  # no such change
    reverts.set_revert(out, tag, "f9.png", "q", True)  # no such frame
    r = merge_reviewed(STEM, _review(out, tag, tmp_path), out, tag)
    assert (r.reverted_applied, r.reverted_conflicts, r.unmatched_reverts) == (1, 0, 2)
    assert _lab(_work(out), "f1.png", "a")["category"] == "solid_single_line"
    m = merge_markers(out)[-1]
    assert m["unmatched_reverts"] == 2 and m["reverted_applied"] == 1
    assert "2 revert(s) matched no change" in capsys.readouterr().out


def _delete_round(out):
    b = _issue(STEM, "f1.png", "b", "duplicates", {"op": "delete"})
    _setup(out, [b])
    return apply("duplicates", out, 1).tags[0]


def test_revert_delete_skipped_when_user_redrew_label(env, tmp_path):
    orig, out, frames = env
    tag = _delete_round(out)
    reverts.set_revert(out, tag, "f1.png", "b", True)
    redraw = json.loads(json.dumps(frames[0]["labels"][1]))
    redraw["id"] = "new1"
    redraw["poly2d"][0]["vertices"] = [[0, 51], [100, 51]]  # same class, 1 px off

    def edit(fr):
        fr["f1.png"]["labels"].append(redraw)
    r = merge_reviewed(STEM, _review(out, tag, tmp_path, edit), out, tag)
    assert r.reverted_skipped_overlap == 1 and r.reverted_applied == 0
    ids = [l["id"] for l in next(f for f in _work(out)["frames"] if f["name"] == "f1.png")["labels"]]
    assert "b" not in ids and "new1" in ids


def test_revert_delete_readds_when_new_label_elsewhere(env, tmp_path):
    orig, out, frames = env
    tag = _delete_round(out)
    reverts.set_revert(out, tag, "f1.png", "b", True)
    other = json.loads(json.dumps(frames[0]["labels"][1]))
    other["id"] = "new1"
    other["poly2d"][0]["vertices"] = [[0, 300], [100, 300]]

    def edit(fr):
        fr["f1.png"]["labels"].append(other)
    r = merge_reviewed(STEM, _review(out, tag, tmp_path, edit), out, tag)
    assert r.reverted_applied == 1 and r.reverted_skipped_overlap == 0
    assert _lab(_work(out), "f1.png", "b") is not None


# ---- I3: review outcome ----
def test_review_outcome_excludes_rejected_and_drops_kept_marks(env, tmp_path, monkeypatch):
    orig, out, _ = env
    a = _issue(STEM, "f1.png", "a", "curb_soft", CURB)  # kept as proposed
    z = _issue(STEM, "f3.png", "z", "curb_soft", CURB)  # user changes it
    h = _issue(STEM, "f2.png", "h", "curb_soft", CURB)  # reverted in View B
    cal = _issue(STEM, "f1.png", "c1", "curb_soft", CURB)  # kept, but in the calibration sample
    _setup(out, [a, z, h, cal])
    (out / "calibration").mkdir()
    (out / "calibration" / "sample_curb_soft.json").write_text(json.dumps({"keys": [cal.key]}))
    tag = apply("curb_soft", out, 1).tags[0]
    reverts.set_revert(out, tag, "f2.png", "h", True)

    def edit(fr):
        fr["f3.png"]["labels"][0]["category"] = "without_curb_road_edge"
    r = merge_reviewed(STEM, _review(out, tag, tmp_path, edit), out, tag)
    ex = read_excluded(out)
    assert set(ex) == {z.key, h.key} and r.excluded_added == 2
    assert ex[z.key]["reason"] == f"rejected in review ({tag})"
    marks = read_marks(out / "marks.json")
    assert a.key not in marks and r.marks_dropped == 1
    assert marks[cal.key] == "real"  # calibration answers are never dropped
    assert marks[z.key] == "real" and marks[h.key] == "real"  # excluded instead
    # a rescan finds z again: the next round leaves the rejected fix out
    write_marks(out / "marks.json", {**marks, a.key: "real"})
    _rescanned(out, "curb_soft")
    r2 = apply("curb_soft", out, 2)
    assert any(s.startswith(z.key) and "rejected in review" in s for s in r2.skipped)
    assert any(s.startswith(h.key) for s in r2.skipped)


def test_review_outcome_combined_round_uses_all_issue_types(env, tmp_path):
    orig, out, _ = env
    a = _issue(STEM, "f1.png", "a", "curb_soft", CURB)
    a2 = _issue(STEM, "f1.png", "a", "shape", {"op": "set_poly", "vertices": [[0, 0], [100, 2]],
                                                "types": "LL", "closed": False})
    _setup(out, [a, a2])
    tag = apply("curb_soft,shape", out, 1, name="geo").tags[0]

    def edit(fr):
        fr["f1.png"]["labels"][0]["category"] = "solid_single_line"
    merge_reviewed(STEM, _review(out, tag, tmp_path, edit), out, tag)
    assert set(read_excluded(out)) == {a.key, a2.key}
