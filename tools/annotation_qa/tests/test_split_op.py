"""The "split" fix op end to end: geometry, fix.apply, changes.json, merge, revert,
verify and View B."""

import json
import re

import cv2
import numpy as np
import pytest

from annotation_qa import fix as fixmod
from annotation_qa.fix import apply
from annotation_qa.geometry import SIMPLIFY_TOL, _arc, _dist_to_polyline, dense, split_poly
from annotation_qa.issues import Issue, write_issues, write_marks
from annotation_qa.merge import merge_reviewed

CURB, SOFT = "curb_road_edge", "without_curb_road_edge"
STEM = "exportA"
TAG = f"{STEM}_fix_curb_soft_split_v1"
ID_RE = re.compile(r"^[A-Za-z0-9]{16}$")


def _label(lid, cat, verts, types=None, attrs=None):
    return {"id": lid, "category": cat, "attributes": attrs or {}, "manualShape": True,
            "poly2d": [{"vertices": verts, "types": types or "L" * len(verts), "closed": False}],
            "box2d": None, "box3d": None}


def _frame(name, labels):
    return {"name": name, "url": name, "videoName": "", "timestamp": 0, "attributes": {},
            "sensor": -1, "labels": labels}


# ---- geometry ----
def test_split_poly_pieces_continuous_and_exact_ends():
    poly = {"vertices": [[0, 0], [100, 0], [200, 50], [300, 50]], "types": "LLLL",
            "closed": False}
    total = float(_arc(dense(poly))[-1])
    pieces = split_poly(poly, [80.0, 200.0])
    assert len(pieces) == 3
    assert pieces[0]["vertices"][0] == [0.0, 0.0] and pieces[-1]["vertices"][-1] == [300.0, 50.0]
    for a, b in zip(pieces, pieces[1:]):
        assert a["vertices"][-1] == b["vertices"][0]  # shared cut point, exactly
    assert pieces[0]["vertices"][-1] == pytest.approx([80.0, 0.0])
    lens = [float(_arc(np.asarray(p["vertices"], float))[-1]) for p in pieces]
    assert sum(lens) == pytest.approx(total, abs=0.5)
    assert all(set(p["types"]) == {"L"} and not p["closed"] for p in pieces)


def test_split_poly_bezier_becomes_polyline_within_tolerance():
    poly = {"vertices": [[0, 0], [100, 0], [200, 200], [300, 200], [400, 200]],
            "types": "LCCLL", "closed": False}
    d = np.asarray(dense(poly), float)
    total = float(_arc(d)[-1])
    pieces = split_poly(poly, [total / 3])
    assert pieces[0]["vertices"][-1] == pieces[1]["vertices"][0]
    assert "C" not in pieces[0]["types"] + pieces[1]["types"]
    joined = np.vstack([np.asarray(p["vertices"], float) for p in pieces])
    assert _dist_to_polyline(d, joined).max() <= SIMPLIFY_TOL + 1e-6
    assert len(pieces[0]["vertices"]) < 40  # simplified, not every dense sample


def test_split_poly_rejects_bad_cuts_and_rings():
    poly = {"vertices": [[0, 0], [100, 0]], "types": "LL", "closed": False}
    for cuts in ([], [0.0], [100.0], [60.0, 50.0]):
        with pytest.raises(ValueError):
            split_poly(poly, cuts)
    ring = {"vertices": [[0, 0], [100, 0], [100, 100]], "types": "LLL", "closed": True}
    with pytest.raises(ValueError):
        split_poly(ring, [50.0])


# ---- fix.apply ----
@pytest.fixture
def env(tmp_path, monkeypatch):
    from annotation_qa import data
    orig = tmp_path / "orig"
    orig.mkdir()
    curve = _label("bez", CURB, [[0, 300], [100, 300], [200, 400], [300, 400], [400, 400]],
                   "LCCLL", attrs={"note": "x"})
    frames = [_frame("f1.png", [_label("e1", CURB, [[0, 100], [600, 100]], attrs={"k": 1}),
                                _label("p1", "solid_single_line", [[0, 200], [600, 200]]), curve])]
    (orig / f"{STEM}.json").write_text(json.dumps({"frames": frames, "config": {}}))
    monkeypatch.setattr(fixmod, "ORIGINALS_DIR", orig)
    monkeypatch.setattr(data, "ORIGINALS_DIR", orig)
    monkeypatch.setattr(fixmod, "calibration_refusal", lambda *a: None)
    out = tmp_path / "out"
    out.mkdir()
    return out, frames


def _split_issue(lid, cuts, cats, cat=CURB, margins=(1.0, 1.0)):
    pieces = [{"s0": 0.0, "s1": 1.0, "category": c, "n_votes": 4, "n_for": 4, "n_against": 0,
               "margin": m} for c, m in zip(cats, margins)]
    return Issue("curb_soft_split", STEM, "f1.png", lid, cat, 0.9, "r", (0.0, 0.0),
                 {"op": "split", "poly_index": 0, "cuts": cuts, "categories": cats}, False,
                 features={"pieces": pieces})


def _apply(out, issues):
    write_issues(out / "issues_curb_soft_split.jsonl", issues)
    write_marks(out / "marks.json", {i.key: "real" for i in issues})
    return apply("curb_soft_split", out, 1)


def test_apply_split_first_keeps_id_new_ids_unique(env):
    out, _ = env
    r = _apply(out, [_split_issue("e1", [200.0, 450.0], [CURB, SOFT, CURB])])
    assert r.changed == {STEM: ["f1.png"]}
    work = json.loads((out / "work" / f"{TAG}.json").read_text())
    labels = work["frames"][0]["labels"]
    ids = [l["id"] for l in labels]
    assert ids[0] == "e1" and ids[3] == "p1" and len(set(ids)) == len(ids)
    new = labels[1:3]
    assert all(ID_RE.match(l["id"]) for l in new)
    assert [labels[0]["category"]] + [l["category"] for l in new] == [CURB, SOFT, CURB]
    assert all(l["attributes"] == {"k": 1} for l in labels[:3])
    v = [l["poly2d"][0]["vertices"] for l in labels[:3]]
    assert v[0][0] == [0.0, 100.0] and v[0][-1] == [200.0, 100.0]
    assert v[0][-1] == v[1][0] and v[1][-1] == v[2][0] and v[2][-1] == [600.0, 100.0]
    ch = json.loads((out / "rounds" / f"{TAG}_changes.json").read_text())
    rec = ch["f1.png"]["e1"]
    assert rec["before"]["category"] == CURB and len(rec["before"]["poly2d"][0]["vertices"]) == 2
    assert [p["id"] for p in rec["after"]] == ids[:3]
    assert [p["category"] for p in rec["after"]] == [CURB, SOFT, CURB]
    assert rec["ops"] == ["split"] and rec["anchor"] == [200, 100]
    assert rec["cut_points"] == [[200.0, 100.0], [450.0, 100.0]]
    items = json.loads((out / "rounds" / f"{TAG}_items.json").read_text())
    assert [l["id"] for l in items["frames"][0]["labels"]] == ids


def test_apply_split_bezier_label(env):
    out, frames = env
    total = float(_arc(dense(frames[0]["labels"][2]["poly2d"][0]))[-1])
    _apply(out, [_split_issue("bez", [total / 2], [SOFT, CURB])])
    labels = json.loads((out / "work" / f"{TAG}.json").read_text())["frames"][0]["labels"]
    a, b = labels[2], labels[3]
    assert a["id"] == "bez" and a["category"] == SOFT and b["category"] == CURB
    assert a["attributes"] == b["attributes"] == {"note": "x"}
    assert a["poly2d"][0]["vertices"][-1] == b["poly2d"][0]["vertices"][0]
    assert "C" not in a["poly2d"][0]["types"] + b["poly2d"][0]["types"]


def test_apply_split_with_bad_cuts_is_skipped(env):
    out, _ = env
    r = _apply(out, [_split_issue("e1", [9999.0], [CURB, SOFT])])
    assert r.changed == {} and any("bad split cuts" in s for s in r.skipped)


def test_new_label_id_unique_and_stable():
    taken = {"a"}
    x = fixmod.new_label_id("seed", taken)
    assert ID_RE.match(x) and x in taken
    y = fixmod.new_label_id("seed", taken)
    assert y != x and fixmod.new_label_id("seed", {"a"}) == x


# ---- merge / revert / verify / View B ----
def _items(out):
    return json.loads((out / "rounds" / f"{TAG}_items.json").read_text())["frames"]


def test_merge_keeps_new_ids_and_revert_restores(env, tmp_path):
    out, frames = env
    _apply(out, [_split_issue("e1", [300.0], [CURB, SOFT])])
    reviewed = tmp_path / "reviewed.json"
    reviewed.write_text(json.dumps({"frames": _items(out), "config": {}}))
    new_id = _items(out)[0]["labels"][1]["id"]
    merge_reviewed(STEM, reviewed, out, round_tag=TAG)
    work = json.loads((out / "work" / f"{STEM}.json").read_text())["frames"][0]["labels"]
    assert [l["id"] for l in work][:2] == ["e1", new_id]
    assert work[1]["category"] == SOFT


def test_revert_of_split_restores_original(env, tmp_path):
    from annotation_qa import reverts
    out, frames = env
    _apply(out, [_split_issue("e1", [300.0], [CURB, SOFT])])
    reverts.set_revert(out, TAG, "f1.png", "e1", True)
    reviewed = tmp_path / "reviewed.json"
    reviewed.write_text(json.dumps({"frames": _items(out), "config": {}}))
    merge_reviewed(STEM, reviewed, out, round_tag=TAG)
    work = json.loads((out / "work" / f"{STEM}.json").read_text())["frames"][0]["labels"]
    assert [l["id"] for l in work] == ["e1", "p1", "bez"]
    assert work[0]["poly2d"] == frames[0]["labels"][0]["poly2d"]


def test_verify_split_ok_only_with_strong_pieces(env):
    from annotation_qa.verify import verify_frame_change
    out, _ = env
    _apply(out, [_split_issue("e1", [300.0], [CURB, SOFT])])
    rec = json.loads((out / "rounds" / f"{TAG}_changes.json").read_text())["f1.png"]["e1"]
    strong = _split_issue("e1", [300.0], [CURB, SOFT], margins=(1.0, 0.8))
    weak = _split_issue("e1", [300.0], [CURB, SOFT], margins=(1.0, 0.2))
    assert verify_frame_change(None, strong, rec, {}, "e1")["verdict"] == "ok"
    assert verify_frame_change(None, weak, rec, {}, "e1")["verdict"] == "check"
    assert verify_frame_change(None, None, rec, {}, "e1")["verdict"] == "check"


def test_viewb_rows_and_crops_for_split(env, tmp_path):
    from annotation_qa.viewb import build_rows, frame_specs, render_frame
    out, _ = env
    repo = tmp_path / "repo"
    img = np.full((500, 700, 3), 120, np.uint8)
    (repo / "local-data").mkdir(parents=True)
    cv2.imwrite(str(repo / "local-data" / "f1.png"), img)
    _apply(out, [_split_issue("e1", [300.0], [CURB, SOFT])])
    (row,) = [r for r in build_rows(out, repo) if r["changes"]]
    (c,) = row["changes"]
    assert c["class_change"] == "curb → split in 2: curb | SOFT EDGE"
    assert c["what"].startswith("split into 2 pieces") and c["anchor"] == [300, 100]
    specs = frame_specs(out, STEM, "f1.png")
    after = specs[(TAG, "e1", "after")]["issue"]
    before = specs[(TAG, "e1", "before")]
    assert before["issue"].highlight_category == CURB and not before["issue"].markers
    assert {l["id"] for l in before["frame"]["labels"]} == {"e1", "p1", "bez"}
    assert after.highlight_category == CURB and list(after.highlights.values()) == [SOFT]
    assert after.markers == [[300.0, 100.0]]
    assert render_frame(out, repo, STEM, "f1.png") == 2
    crop = cv2.imread(str(specs[(TAG, "e1", "after")]["dest"]))
    right = crop[:, crop.shape[1] // 2 + 4:]
    # red curb piece and green soft piece both drawn (BGR)
    assert ((right[..., 2] > 200) & (right[..., 1] < 50)).any()
    assert ((right[..., 1] > 200) & (right[..., 2] < 50)).any()


def test_viewb_merged_split_review_state(env, tmp_path):
    from annotation_qa import reverts
    from annotation_qa.viewb import build_rows
    out, _ = env
    _apply(out, [_split_issue("e1", [300.0], [CURB, SOFT])])
    reviewed = tmp_path / "reviewed.json"
    reviewed.write_text(json.dumps({"frames": _items(out), "config": {}}))
    merge_reviewed(STEM, reviewed, out, round_tag=TAG)
    (c,) = [c for r in build_rows(out, tmp_path) for c in r["changes"]]
    assert c["merged"] and c["review"] == ""  # shipped as proposed
    reverts.set_revert(out, TAG, "f1.png", "e1", True)
    merge_reviewed(STEM, reviewed, out, round_tag=TAG)  # re-merge applies the revert
    (c,) = [c for r in build_rows(out, tmp_path) for c in r["changes"]]
    assert c["review"] == "reverted" and c["revert_state"] == "applied"
