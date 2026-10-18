"""Automatic self-crossing removal: geometry, the shape detector's set_poly fix, and the
set_poly op through fix.apply, changes.json, verify and View B."""

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from annotation_qa import fix as fixmod
from annotation_qa.fix import apply
from annotation_qa.geometry import (SIMPLIFY_TOL, UNCROSS_LOOP_MAX, _dist_to_polyline, dense,
                                    remove_self_crossings, resample, self_crossings)
from annotation_qa.issues import Issue, write_issues, write_marks

FIXTURE = Path(__file__).parent / "fixtures" / "selfcross_join_fixes.json"
STEM = "exportA"
TAG = f"{STEM}_fix_shape_v1"


def poly(verts, types=None, closed=False):
    return {"vertices": verts, "types": types or "L" * len(verts), "closed": closed}


def _dist(p, q):
    """Distance from every 1 px sample of ``p``'s drawn curve to ``q``'s drawn curve."""
    P, _ = resample(dense(p), 1.0)
    return _dist_to_polyline(P, np.asarray(dense(q), float))


# overlapping ends: a square ring drawn as an open line whose two ends overshoot by 5 px
OVERLAP = poly([[-5, 0], [100, 0], [100, 100], [0, 100], [0, -5]])
# a straight line with a small loop where two joined pieces overlapped (~12 px of line)
HOOK = poly([[0, 0], [100, 0], [104, 4], [100, 4], [102, -4], [200, -4]])
# the X / bow tie: the "loop" is most of the line
BOW = poly([[50, 50], [250, 150], [250, 50], [50, 150]])


# ---- geometry ----
def test_self_crossings_finds_the_crossing_point():
    [(a, b, x)] = self_crossings(HOOK)
    assert a < b and x == pytest.approx((100.8, 0.8), abs=1e-6)
    assert self_crossings(poly([[0, 0], [100, 0], [100, 100], [0, 100]], closed=True)) == []


def test_overlapping_ends_join_into_a_ring():
    new, info = remove_self_crossings(OVERLAP)
    assert info["modes"] == ["ring"] and info["removed_len"] == pytest.approx(10.0)
    assert new["closed"] and new["types"] == "LLLL"
    assert np.allclose(new["vertices"], [[0, 0], [100, 0], [100, 100], [0, 100]])
    assert self_crossings(new) == []


def test_small_hook_cut_out_arms_joined_at_crossing():
    new, info = remove_self_crossings(HOOK)
    assert info["modes"] == ["cut"] and 10 < info["removed_len"] < 13
    assert not new["closed"]
    assert np.allclose(new["vertices"], [[0, 0], [100, 0], [100.8, 0.8], [102, -4], [200, -4]])
    assert self_crossings(new) == []
    assert _dist(new, HOOK).max() < 0.01  # what is left lies on the old line


def test_curve_vertices_outside_the_loop_kept_exactly():
    # a Bezier away from the hook, then the hook, then a straight tail
    p = poly([[0, 0], [60, 40], [140, 40], [200, 0], [204, 4], [200, 4], [202, -4], [300, -4]],
             "LCCLLLLL")
    new, _ = remove_self_crossings(p)
    assert new["vertices"][:4] == [[0.0, 0.0], [60.0, 40.0], [140.0, 40.0], [200.0, 0.0]]
    assert new["types"].startswith("LCCL")
    assert self_crossings(new) == []


def test_loop_through_a_bezier():
    # the crossing lies inside a curve: the curve is split (de Casteljau); a trimmed, nearly
    # straight bit becomes one L edge within SIMPLIFY_TOL
    p = poly([[0, 0], [100, 0], [180, 60], [60, 60], [140, -20], [300, -20]], "LLCCLL")
    xs = self_crossings(p)
    assert xs
    new, info = remove_self_crossings(p)
    assert self_crossings(new) == []
    assert _dist(new, p).max() <= SIMPLIFY_TOL
    assert sum(info["removed"]) < UNCROSS_LOOP_MAX


def test_out_and_back_hook_at_the_start_trimmed_to_its_tip():
    # the line starts, runs out to a tip and comes back over itself: the first leg goes, the
    # line still reaches the tip (cutting the loop would shorten it by ~34 px)
    p = poly([[40, -3], [0, 0], [0, 2], [60, -6], [300, -6]])
    new, info = remove_self_crossings(p)
    assert info["modes"] == ["trim_head"] and not new["closed"]
    assert np.allclose(new["vertices"][0], [0, 2], atol=0.5)
    assert new["vertices"][-2:] == [[60.0, -6.0], [300.0, -6.0]]
    assert self_crossings(new) == [] and _dist(p, new).max() < 2.5


def test_wide_end_loop_is_cut_not_trimmed():
    # an end loop that does NOT retrace the line: trimming would drop real line
    p = poly([[30, -20], [30, 20], [0, 20], [0, 0], [600, 0]])
    new, info = remove_self_crossings(p)
    assert info["modes"] == ["cut"] and self_crossings(new) == []


def test_two_hooks_removed_one_by_one():
    p = poly([[0, 0], [100, 0], [104, 4], [100, 4], [102, -4], [200, -4],
              [204, 0], [200, 0], [202, -8], [300, -8]])
    new, info = remove_self_crossings(p)
    assert info["modes"] == ["cut", "cut"] and self_crossings(new) == []


def test_refuses_long_loops():
    assert remove_self_crossings(BOW) is None  # the detour is most of the line
    # a 2000 px line with a ~200 px loop: under 25 % of the line but over 150 px
    big = poly([[0, 0], [1000, 0], [1060, 60], [1000, 60], [1020, -40], [2000, -40]])
    [(a, b, _)] = self_crossings(big)
    assert b - a > UNCROSS_LOOP_MAX
    assert remove_self_crossings(big) is None
    assert remove_self_crossings(poly([[0, 0], [100, 0]])) is None  # nothing to remove


def test_closed_figure_eight_refused_small_seam_hook_fixed():
    eight = poly([[50, 50], [250, 150], [250, 50], [50, 150]], closed=True)
    assert remove_self_crossings(eight) is None
    # a ring whose closing edge overshoots the start by a few px
    ring = poly([[3, -3], [100, 0], [100, 100], [0, 100], [-2, 4]], closed=True)
    if self_crossings(ring):
        new, info = remove_self_crossings(ring)
        assert new["closed"] and self_crossings(new) == [] and info["removed_len"] < 15


def test_reference_join_fixes_within_2px():
    """Roshan fixed these with the editor's join (self-close): ours must land within ~2 px."""
    cases = json.loads(FIXTURE.read_text())["cases"]
    assert {c["label_id"] for c in cases} >= {"MgLLWDKf5dOIzRXB"}
    for c in cases:
        new, info = remove_self_crossings(c["before"])
        assert info["modes"] == ["ring"] and new["closed"] == c["after"]["closed"] is True
        assert self_crossings(new) == []
        mine, his = _dist(new, c["after"]), _dist(c["after"], new)
        assert np.median(mine) <= 2.0 and np.median(his) <= 2.0
        assert mine.max() <= 2.0 and his.max() <= 2.0


# ---- the shape detector ----
def test_shape_issue_carries_set_poly(tmp_path):
    from conftest import _road, edge_ctx_from_image
    from annotation_qa.detectors.shape import detect, keep_issue
    ctx = edge_ctx_from_image(tmp_path, _road(), [OVERLAP], "without_curb_road_edge")
    [i] = detect(ctx)
    assert i.features["kind"] == "self_cross" and keep_issue(i)
    assert not i.hand_fix and i.fix["op"] == "set_poly" and i.fix["poly_index"] == 0
    assert i.fix["closed"] is True and len(i.fix["vertices"]) == len(i.fix["types"]) == 4
    assert i.features["uncross"] == "ring" and i.features["opened_to_ring"]
    assert "ring" in i.reason


def test_shape_bow_tie_stays_hand_fix(tmp_path):
    from conftest import _road, edge_ctx_from_image
    from annotation_qa.detectors.shape import detect
    [i] = detect(edge_ctx_from_image(tmp_path, _road(), [BOW], "without_curb_road_edge"))
    assert i.hand_fix and i.fix is None and i.features["uncross"] == "refused"


# ---- set_poly through fix.apply / verify / View B ----
def _label(lid, cat, p):
    return {"id": lid, "category": cat, "attributes": {}, "manualShape": True,
            "poly2d": [p], "box2d": None, "box3d": None}


def _frame(name, labels):
    return {"name": name, "url": name, "videoName": "", "timestamp": 0, "attributes": {},
            "sensor": -1, "labels": labels}


@pytest.fixture
def env(tmp_path, monkeypatch):
    from annotation_qa import data
    orig = tmp_path / "orig"
    orig.mkdir()
    big_hook = poly([[0, 300], [300, 300], [330, 330], [300, 330], [315, 270], [600, 270]])
    frames = [_frame("f1.png", [_label("ring", "curb_road_edge", OVERLAP),
                                _label("hook", "solid_single_line",
                                       poly([[x, y + 200] for x, y in HOOK["vertices"]])),
                                _label("big", "solid_single_line", big_hook)])]
    (orig / f"{STEM}.json").write_text(json.dumps({"frames": frames, "config": {}}))
    monkeypatch.setattr(fixmod, "ORIGINALS_DIR", orig)
    monkeypatch.setattr(data, "ORIGINALS_DIR", orig)
    monkeypatch.setattr(fixmod, "calibration_refusal", lambda *a: None)
    out = tmp_path / "out"
    out.mkdir()
    return out, frames


def _shape_issue(lid, p, conf=0.9):
    new, info = remove_self_crossings(p)
    fix = {"op": "set_poly", "poly_index": 0, "vertices": new["vertices"],
           "types": new["types"], "closed": new["closed"]}
    return Issue("shape", STEM, "f1.png", lid, "curb_road_edge", conf, "r", (0.0, 0.0), fix,
                 False, features={"kind": "self_cross", "removed_len": info["removed_len"]})


def _labels_of(env_frames):
    return {l["id"]: l for l in env_frames[0]["labels"]}


def _apply(out, issues):
    write_issues(out / "issues_shape.jsonl", issues)
    write_marks(out / "marks.json", {i.key: "real" for i in issues})
    return apply("shape", out, 1)


def test_apply_set_poly_writes_geometry_and_changes(env):
    out, frames = env
    labs = _labels_of(frames)
    iss = [_shape_issue(lid, labs[lid]["poly2d"][0]) for lid in ("ring", "hook", "big")]
    r = _apply(out, iss)
    assert r.changed == {STEM: ["f1.png"]} and r.skipped == []
    work = {l["id"]: l for l in
            json.loads((out / "work" / f"{TAG}.json").read_text())["frames"][0]["labels"]}
    for i in iss:
        p = work[i.label_id]["poly2d"][0]
        assert p["vertices"] == i.fix["vertices"] and p["types"] == i.fix["types"]
        assert p["closed"] == i.fix["closed"] and self_crossings(p) == []
    ch = json.loads((out / "rounds" / f"{TAG}_changes.json").read_text())["f1.png"]
    assert ch["ring"]["ops"] == ["set_poly"] and ch["ring"]["after"]["poly2d"][0]["closed"]
    assert not ch["ring"]["before"]["poly2d"][0]["closed"]
    ax, ay = ch["hook"]["anchor"]
    assert abs(ax - 103) <= 2 and abs(ay - 203) <= 2  # at the removed loop, not mid-line


def test_set_poly_rejects_malformed_geometry():
    with pytest.raises(ValueError):
        fixmod._set_poly(HOOK, {"vertices": [[0, 0], [1, 1]], "types": "L"})
    with pytest.raises(ValueError):
        fixmod._set_poly(HOOK, {"vertices": [[0, 0], [1, 1]], "types": "LL", "closed": True})
    with pytest.raises(ValueError):
        fixmod._set_poly(HOOK, {"vertices": [[0, 0], [1, 1], [2, 2]], "types": "CCL"})


def test_compose_set_category_and_set_poly(env):
    out, frames = env
    labs = _labels_of(frames)
    sp = _shape_issue("ring", labs["ring"]["poly2d"][0])
    cat = Issue("curb_soft", STEM, "f1.png", "ring", "curb_road_edge", 0.5, "r", (0.0, 0.0),
                {"op": "set_category", "to": "without_curb_road_edge"}, False)
    write_issues(out / "issues_shape.jsonl", [sp])
    write_issues(out / "issues_curb_soft.jsonl", [cat])
    write_marks(out / "marks.json", {sp.key: "real", cat.key: "real"})
    r = apply("shape,curb_soft", out, 1, name="combo")
    assert r.skipped == []
    tag = r.tags[0]
    lab = next(l for l in json.loads((out / "work" / f"{tag}.json").read_text())
               ["frames"][0]["labels"] if l["id"] == "ring")
    assert lab["category"] == "without_curb_road_edge" and lab["poly2d"][0]["closed"]
    rec = json.loads((out / "rounds" / f"{tag}_changes.json").read_text())["f1.png"]["ring"]
    assert rec["ops"] == ["set_category", "set_poly"]


def test_verify_set_poly_ok_check_bad(env):
    from annotation_qa.verify import verify_frame_change
    out, frames = env
    labs = _labels_of(frames)
    iss = {lid: _shape_issue(lid, labs[lid]["poly2d"][0]) for lid in ("ring", "hook", "big")}
    _apply(out, list(iss.values()))
    ch = json.loads((out / "rounds" / f"{TAG}_changes.json").read_text())["f1.png"]
    ok = verify_frame_change(None, iss["ring"], ch["ring"], {}, "ring")
    assert ok["verdict"] == "ok" and ok["removed_extent"] <= 8 and ok["max_follow"] <= 2
    assert "ring" in ok["reasons"][0]
    assert verify_frame_change(None, iss["hook"], ch["hook"], {}, "hook")["verdict"] == "ok"
    big = verify_frame_change(None, iss["big"], ch["big"], {}, "big")
    assert big["verdict"] == "check" and big["removed_extent"] > 8
    assert any("confirm the shape" in r for r in big["reasons"])
    still = {**ch["hook"], "after": ch["hook"]["before"]}  # a "fix" that left the loop in
    assert verify_frame_change(None, iss["hook"], still, {}, "hook")["verdict"] == "bad"


def test_viewb_set_poly_rows_and_crop(env, tmp_path):
    from annotation_qa.viewb import build_rows, frame_specs, render_frame
    out, frames = env
    labs = _labels_of(frames)
    _apply(out, [_shape_issue("ring", labs["ring"]["poly2d"][0]),
                 _shape_issue("hook", labs["hook"]["poly2d"][0])])
    repo = tmp_path / "repo"
    (repo / "local-data").mkdir(parents=True)
    cv2.imwrite(str(repo / "local-data" / "f1.png"), np.full((500, 700, 3), 120, np.uint8))
    (row,) = [r for r in build_rows(out, repo) if r["changes"]]
    by = {c["label_id"]: c for c in row["changes"]}
    assert by["ring"]["ops"] == ["set_poly"] and "closed into a ring" in by["ring"]["what"]
    assert "vertices 5 → 4" in by["ring"]["what"]
    assert "moved" in by["hook"]["what"] and by["hook"]["class_change"] == ""
    specs = frame_specs(out, STEM, "f1.png")
    ch = json.loads((out / "rounds" / f"{TAG}_changes.json").read_text())["f1.png"]
    assert specs[(TAG, "hook", "after")]["issue"].anchor == tuple(map(float, ch["hook"]["anchor"]))
    assert render_frame(out, repo, STEM, "f1.png") == 4


# ---- refilter after a rescan that no longer reports fixed labels ----
def test_refilter_keeps_answers_of_labels_fixed_since(tmp_path):
    from annotation_qa.__main__ import main
    from annotation_qa.issues import read_issues

    def stored(lid, kind="self_cross"):
        return Issue("shape", "exp", "f.png", lid, "solid_single_line", 0.9, "r", (0.0, 0.0),
                     None, True, features={"kind": kind})

    live = [stored(f"C{k}") for k in range(3)]
    gone = stored("FIXED")  # answered, then fixed by hand: the rescan does not report it
    ruled = stored("T0", "turn_back")  # answered, but the rule drops it
    write_issues(tmp_path / "issues_shape.jsonl", live + [ruled])
    (tmp_path / "calibration").mkdir()
    (tmp_path / "calibration" / "sample_shape.json").write_text(
        json.dumps({"keys": [gone.key, live[0].key, ruled.key]}))
    (tmp_path / "marks.json").write_text(json.dumps(
        {gone.key: "real", live[0].key: "real", ruled.key: "false"}))
    assert main(["refilter", "--type", "shape", "--out", str(tmp_path)]) == 0
    assert len(read_issues(tmp_path / "issues_shape.jsonl")) == 3
    sample = json.loads((tmp_path / "calibration" / "sample_shape.json").read_text())["keys"]
    assert sample[:2] == [gone.key, live[0].key] and ruled.key not in sample
