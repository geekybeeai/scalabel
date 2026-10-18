"""Automatic join of overlapping pieces (duplicates overlapping_pieces): geometry, the
join_into op through fix.apply, changes.json, verify, merge / revert and View B."""

import json

import cv2
import numpy as np
import pytest

from annotation_qa import fix as fixmod
from annotation_qa.fix import apply
from annotation_qa.geometry import (_dist_to_polyline, _sub_cubic, dense, doubled_length,
                                    join_overlapping, resample, reverse_poly, self_crossings)
from annotation_qa.issues import Issue, write_issues, write_marks
from annotation_qa.merge import merge_reviewed

STEM = "exportA"
TAG = f"{STEM}_fix_duplicates_v1"
CAT = "solid_single_line"


def poly(verts, types=None, closed=False):
    return {"vertices": [list(map(float, v)) for v in verts],
            "types": types or "L" * len(verts), "closed": closed}


def _dist(p, q):
    """Distance from every 1 px sample of ``p``'s drawn curve to ``q``'s drawn curve."""
    P, _ = resample(dense(p), 1.0)
    return _dist_to_polyline(P, np.asarray(dense(q), float))


def _cubic(ctrl, t0, t1):
    c = _sub_cubic(ctrl, t0, t1)
    return poly([q.tolist() for q in c], "LCCL")


# a straight line in two pieces overlapping by 50 px, 2 px apart
B_STRAIGHT = poly([[0, 0], [200, 0]])
A_STRAIGHT = poly([[150, 2], [300, 2]])
# one Bezier arc cut into two pieces that overlap (t 0.45..0.6)
ARC = [np.array(p, float) for p in ([0, 0], [100, -120], [300, -120], [400, 0])]
B_CURVE, A_CURVE = _cubic(ARC, 0.0, 0.6), _cubic(ARC, 0.45, 1.0)


# ---- geometry ----
def test_straight_pieces_join_into_one_simple_line():
    new, info = join_overlapping(A_STRAIGHT, B_STRAIGHT)
    assert info["mode"] == "extend" and info["gaps"] == [pytest.approx(2.0)]
    assert new["vertices"][0] == [0.0, 0.0] and new["vertices"][-1] == [300.0, 2.0]
    assert not new["closed"] and self_crossings(new) == [] and doubled_length(new) == 0
    # B is kept up to the splice, A beyond it; only the short bridge differs
    assert _dist(B_STRAIGHT, new)[:196].max() < 1e-6
    assert _dist(new, A_STRAIGHT)[-90:].max() < 1e-6
    assert info["splices"] == [[pytest.approx(200.0), pytest.approx(1.0)]]


def test_join_keeps_direction_of_b_whatever_the_pieces_direction():
    for a in (A_STRAIGHT, reverse_poly(A_STRAIGHT)):
        new, _ = join_overlapping(a, B_STRAIGHT)
        assert new["vertices"][0] == [0.0, 0.0] and new["vertices"][-1] == [300.0, 2.0]
        new, _ = join_overlapping(a, reverse_poly(B_STRAIGHT))
        assert new["vertices"][0] == [300.0, 2.0] and new["vertices"][-1] == [0.0, 0.0]


def test_curved_pieces_join_back_into_the_curve():
    new, info = join_overlapping(A_CURVE, B_CURVE)
    assert info["mode"] == "extend" and info["gaps"][0] < 0.5  # same curve: no bridge
    assert "C" in new["types"] and self_crossings(new) == []
    whole = poly([p.tolist() for p in ARC], "LCCL")
    assert _dist(new, whole).max() < 0.5 and _dist(whole, new).max() < 0.5
    # B's control points survive exactly (its curve is untouched up to the splice)
    assert new["vertices"][:2] == B_CURVE["vertices"][:2]


def test_curved_pieces_offset_get_a_bridge():
    off = poly([[x, y + 2.5] for x, y in A_CURVE["vertices"]], A_CURVE["types"])
    new, info = join_overlapping(off, B_CURVE)
    assert info["mode"] == "extend" and 2.0 < info["gaps"][0] < 3.0
    assert self_crossings(new) == [] and doubled_length(new) == 0
    assert _dist(new, off)[-100:].max() < 0.5


def test_piece_bridging_both_ends_closes_a_ring():
    u = poly([[100, 0], [0, 0], [0, 100], [100, 100]])  # B: a U open on the right
    cap = poly([[90, 101], [120, 101], [120, -1], [90, -1]])  # A closes it, overlapping both ends
    new, info = join_overlapping(cap, u)
    assert info["mode"] == "ring" and new["closed"] and len(info["splices"]) == 2
    assert self_crossings(new) == []
    assert _dist(u, new).max() <= 4.0 and _dist(new, u).min() < 1e-6


def test_piece_lying_on_the_other_is_contained():
    new, info = join_overlapping(poly([[50, 1], [80, 3], [120, 1]]), B_STRAIGHT)
    assert info["mode"] == "contained" and new["vertices"] == B_STRAIGHT["vertices"]


def test_refusals_fork_wrong_way_and_crossing():
    # a Y: the piece leaves B in its middle
    new, why = join_overlapping(poly([[50, 1], [120, 1], [200, 80]]), B_STRAIGHT)
    assert new is None and "fork" in why
    # overlaps B's end but runs back along B, then away: joining would double back
    new, why = join_overlapping(poly([[195, 2], [120, 2], [120, 60]]), B_STRAIGHT)
    assert new is None and "wrong way" in why
    # continues past B's end, then loops back across B
    loop = poly([[150, 1], [250, 1], [250, -50], [100, -50], [100, 50]])
    new, why = join_overlapping(loop, B_STRAIGHT)
    assert new is None and "cross itself" in why
    assert join_overlapping(A_STRAIGHT, poly([[0, 0], [200, 0], [100, 50]], closed=True))[0] is None


def test_reverse_poly_curve():
    r = reverse_poly(B_CURVE)
    assert r["types"] == "LCCL" and r["vertices"][0] == B_CURVE["vertices"][-1]
    assert np.allclose(dense(r)[::-1], dense(B_CURVE), atol=1e-6)


# ---- join_into through fix.apply / verify / merge / View B ----
def _label(lid, p, cat=CAT):
    return {"id": lid, "category": cat, "attributes": {"k": lid}, "manualShape": True,
            "poly2d": [p], "box2d": None, "box3d": None}


@pytest.fixture
def env(tmp_path, monkeypatch):
    from annotation_qa import data
    orig = tmp_path / "orig"
    orig.mkdir()
    labels = [_label("B", poly([[0, 100], [200, 100]])),
              _label("A", poly([[150, 101.5], [300, 101.5]])),
              _label("C", poly([[0, 300], [200, 300]])),
              _label("D", poly([[150, 300], [300, 300]])),
              _label("E", poly([[-100, 100], [50, 100]]))]
    frames = [{"name": "f1.png", "url": "f1.png", "videoName": "", "timestamp": 0,
               "attributes": {}, "sensor": -1, "labels": labels}]
    (orig / f"{STEM}.json").write_text(json.dumps({"frames": frames, "config": {}}))
    monkeypatch.setattr(fixmod, "ORIGINALS_DIR", orig)
    monkeypatch.setattr(data, "ORIGINALS_DIR", orig)
    monkeypatch.setattr(fixmod, "calibration_refusal", lambda *a: None)
    out = tmp_path / "out"
    out.mkdir()
    return out, frames


def _join_issue(frames, lid, target, conf=0.6):
    labs = {l["id"]: l for l in frames[0]["labels"]}
    new, info = join_overlapping(labs[lid]["poly2d"][0], labs[target]["poly2d"][0])
    fix = {"op": "join_into", "target": target, "poly_index": 0, "vertices": new["vertices"],
           "types": new["types"], "closed": new["closed"]}
    return Issue("duplicates", STEM, "f1.png", lid, CAT, conf, "r", (0.0, 0.0), fix, False,
                 related_label_ids=[target],
                 features={"kind": "overlapping_pieces", "join": info["mode"],
                           "join_splices": info["splices"], "join_gaps": info["gaps"]})


def _apply(out, issues):
    write_issues(out / "issues_duplicates.jsonl", issues)
    write_marks(out / "marks.json", {i.key: "real" for i in issues})
    return apply("duplicates", out, 1)


def _work(out, name=TAG):
    return {l["id"]: l for l in
            json.loads((out / "work" / f"{name}.json").read_text())["frames"][0]["labels"]}


def _changes(out):
    return json.loads((out / "rounds" / f"{TAG}_changes.json").read_text())["f1.png"]


def test_apply_join_deletes_piece_and_sets_target(env):
    out, frames = env
    iss = _join_issue(frames, "A", "B")
    r = _apply(out, [iss])
    assert r.changed == {STEM: ["f1.png"]} and r.skipped == []
    work = _work(out)
    assert "A" not in work and work["B"]["attributes"] == {"k": "B"}
    assert work["B"]["poly2d"][0]["vertices"] == iss.fix["vertices"]
    assert self_crossings(work["B"]["poly2d"][0]) == []
    rec = _changes(out)["A"]
    assert rec["after"] is None and rec["ops"] == ["join_into"]
    assert rec["before"]["poly2d"] == frames[0]["labels"][1]["poly2d"]
    j = rec["join"]
    assert j["target"] == "B" and j["before"]["poly2d"] == frames[0]["labels"][0]["poly2d"]
    assert j["after"]["poly2d"][0]["vertices"] == iss.fix["vertices"] and j["mode"] == "extend"
    assert rec["anchor"] == [200, 101] and list(_changes(out)) == ["A"]


def test_apply_join_skipped_when_target_changed_or_taken(env):
    out, frames = env
    j1 = _join_issue(frames, "A", "B")
    other = Issue("duplicates", STEM, "f1.png", "B", CAT, 0.9, "r", (0.0, 0.0),
                  {"op": "delete"}, False)  # B itself is changed by another issue
    r = _apply(out, [j1, other])
    assert any("A|duplicates (join_into): join target B is also changed" in s for s in r.skipped)
    work = _work(out)
    assert "A" in work and "B" not in work  # the join is not half-applied


def test_apply_two_joins_into_one_target_keeps_the_first(env, tmp_path):
    out, frames = env
    j1, j2 = _join_issue(frames, "A", "B", 0.7), _join_issue(frames, "E", "B", 0.6)
    r = _apply(out, [j1, j2])
    assert len(r.skipped) == 1 and "E|duplicates" in r.skipped[0]
    assert "already joined" in r.skipped[0]
    work = _work(out)
    assert "A" not in work and "E" in work
    assert work["B"]["poly2d"][0]["vertices"] == j1.fix["vertices"]


def test_verify_join_ok_check_bad(env):
    from annotation_qa.verify import verify_frame_change
    out, frames = env
    j_ab, j_dc = _join_issue(frames, "A", "B"), _join_issue(frames, "D", "C")
    _apply(out, [j_ab, j_dc])
    ch = _changes(out)
    v = verify_frame_change(None, j_dc, ch["D"], {}, "D")  # collinear pieces: exact join
    assert v["verdict"] == "ok" and v["max_follow_a"] < 1e-6 and v["splice_gap"] == 0
    v = verify_frame_change(None, j_ab, ch["A"], {}, "A")  # 1.5 px apart: within half
    assert v["verdict"] == "ok" and v["max_follow_a"] == pytest.approx(1.5, abs=0.05)
    off = json.loads(json.dumps(ch["A"]))
    off["before"]["poly2d"][0]["vertices"] = [[150, 103.5], [300, 103.5]]  # 3.5 px apart
    off["join"]["after"]["poly2d"][0]["vertices"] = [[0, 100], [196, 100], [204, 103.5],
                                                     [300, 103.5]]
    off["join"]["after"]["poly2d"][0]["types"] = "LLLL"
    off["join"]["gaps"] = [3.5]
    v = verify_frame_change(None, j_ab, off, {}, "A")
    assert v["verdict"] == "check" and any("confirm which line" in r for r in v["reasons"])
    wide = json.loads(json.dumps(ch["A"]))
    wide["join"]["gaps"] = [10.0]
    assert verify_frame_change(None, j_ab, wide, {}, "A")["verdict"] == "check"
    bad = json.loads(json.dumps(ch["A"]))
    bad["join"]["after"]["poly2d"][0]["vertices"] = [[0, 100], [200, 100], [200, 50],
                                                     [100, 50], [100, 150]]
    bad["join"]["after"]["poly2d"][0]["types"] = "LLLLL"
    assert verify_frame_change(None, j_ab, bad, {}, "A")["verdict"] == "bad"


def _review(out, tmp_path, edit=None):
    items = json.loads((out / "rounds" / f"{TAG}_items.json").read_text())["frames"]
    if edit:
        edit(items[0]["labels"])
    reviewed = tmp_path / "reviewed.json"
    reviewed.write_text(json.dumps({"frames": items, "config": {}}))
    return reviewed


def test_merge_keeps_join_and_revert_restores_both(env, tmp_path):
    from annotation_qa import reverts
    out, frames = env
    _apply(out, [_join_issue(frames, "A", "B")])
    reverts.set_revert(out, TAG, "f1.png", "A", True)
    merge_reviewed(STEM, _review(out, tmp_path), out, round_tag=TAG)
    work = _work(out, STEM)
    orig = {l["id"]: l for l in frames[0]["labels"]}
    assert work["A"]["poly2d"] == orig["A"]["poly2d"] and work["A"]["attributes"] == {"k": "A"}
    assert work["B"]["poly2d"] == orig["B"]["poly2d"]


def test_merge_without_revert_ships_join_and_edited_target_wins(env, tmp_path):
    from annotation_qa import reverts
    out, frames = env
    iss = _join_issue(frames, "A", "B")
    _apply(out, [iss])
    reverts.set_revert(out, TAG, "f1.png", "A", True)

    def edit(labels):  # Roshan moved B's end in Scalabel
        next(l for l in labels if l["id"] == "B")["poly2d"][0]["vertices"][-1] = [310, 110]
    merge_reviewed(STEM, _review(out, tmp_path, edit), out, round_tag=TAG)
    work = _work(out, STEM)
    assert "A" not in work and work["B"]["poly2d"][0]["vertices"][-1] == [310, 110]


def test_viewb_join_rows_and_crops(env, tmp_path):
    from annotation_qa.viewb import build_rows, frame_specs, render_frame
    out, frames = env
    _apply(out, [_join_issue(frames, "A", "B")])
    repo = tmp_path / "repo"
    (repo / "local-data").mkdir(parents=True)
    cv2.imwrite(str(repo / "local-data" / "f1.png"), np.full((500, 700, 3), 120, np.uint8))
    (row,) = [r for r in build_rows(out, repo) if r["changes"]]
    (c,) = row["changes"]
    assert c["label_id"] == "A" and c["ops"] == ["join_into"]
    assert c["what"].startswith("joined into B (extended, +100 px)") and c["anchor"] == [200, 101]
    specs = frame_specs(out, STEM, "f1.png")
    before, after = specs[(TAG, "A", "before")], specs[(TAG, "A", "after")]
    bl = {l["id"]: l for l in before["frame"]["labels"]}
    al = {l["id"]: l for l in after["frame"]["labels"]}
    assert bl["A"]["poly2d"] == frames[0]["labels"][1]["poly2d"]
    assert bl["B"]["poly2d"] == frames[0]["labels"][0]["poly2d"]
    assert "A" not in al and al["B"]["poly2d"][0]["vertices"][-1] == [300.0, 101.5]
    assert before["issue"].highlight_category == CAT and before["issue"].highlights == {"B": CAT}
    assert after["issue"].highlights == {"B": CAT} and after["issue"].markers
    assert render_frame(out, repo, STEM, "f1.png") == 2
    merge_reviewed(STEM, _review(out, tmp_path), out, round_tag=TAG)
    (c,) = [r for r in build_rows(out, repo) if r["changes"]][0]["changes"]
    assert c["merged"] and c["review"] == ""


def test_refilter_duplicates_keeps_all_and_the_answered_sample(tmp_path):
    from annotation_qa.__main__ import main
    from annotation_qa.issues import read_issues
    iss = [Issue("duplicates", "exp", "f.png", f"L{k}", CAT, 0.6, "r", (0.0, 0.0), None, True,
                 features={"kind": "overlapping_pieces"}) for k in range(3)]
    write_issues(tmp_path / "issues_duplicates.jsonl", iss)
    (tmp_path / "calibration").mkdir()
    (tmp_path / "calibration" / "sample_duplicates.json").write_text(
        json.dumps({"keys": [iss[0].key]}))
    (tmp_path / "marks.json").write_text(json.dumps({iss[0].key: "real"}))
    assert main(["refilter", "--type", "duplicates", "--out", str(tmp_path)]) == 0
    assert len(read_issues(tmp_path / "issues_duplicates.jsonl")) == 3
    keys = json.loads((tmp_path / "calibration" / "sample_duplicates.json").read_text())["keys"]
    assert keys[0] == iss[0].key and len(keys) == 3
