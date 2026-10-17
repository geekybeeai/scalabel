import json
import threading
import urllib.request

import cv2
import numpy as np
import pytest

from annotation_qa import __main__ as cli
from annotation_qa import verify
from annotation_qa.issues import Issue
from annotation_qa.review_server import make_server
from annotation_qa.viewb import build_rows, select_rows

STEM = "proj_export_a"
SOLID = "solid_single_line"


def _poly(pts):
    return {"vertices": [list(map(float, p)) for p in pts], "types": "L" * len(pts), "closed": False}


def _label(lid, pts, cat=SOLID):
    return {"id": lid, "category": cat, "attributes": {}, "manualShape": True,
            "poly2d": [_poly(pts)], "box2d": None, "box3d": None}


def _snap(label):
    return {"category": label["category"], "poly2d": label["poly2d"]}


def _hline(y):
    return [(50, y), (350, y)]


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def _image(core=(200, 230, 255, 230, 200)):
    img = np.zeros((400, 400, 3), np.uint8)
    img[10:390, 10:390] = (110, 110, 110)
    for dy, v in zip(range(-2, 3), core):
        img[200 + dy, 10:390] = (v, v, v)
    return img


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "local-data").mkdir()
    return tmp_path


@pytest.fixture
def out(tmp_path):
    return tmp_path / "out"


def _round(repo, out, before_pts, after_pts, core=None, neighbours=(), v=1, issue_type="position",
           changes_extra=None, hand=False):
    cv2.imwrite(str(repo / "local-data" / "f1.png"), _image(core) if core else _image())
    b, a = _label("1", before_pts), _label("1", after_pts)
    frames = [{"name": "f1.png", "url": "f1.png",
               "labels": [a] + [_label(str(i + 2), p) for i, p in enumerate(neighbours)]}]
    changes = {"f1.png": {"1": {"before": _snap(b), "after": _snap(a)}}}
    changes["f1.png"].update(changes_extra or {})
    if hand:
        frames.append({"name": "h.png", "url": "f1.png", "labels": []})
    tag = f"{STEM}_fix_{issue_type}_v{v}"
    _write(out / "rounds" / f"{tag}_changes.json", changes)
    _write(out / "rounds" / f"{tag}_items.json", {"frames": frames, "config": {}})
    _write(out / "work" / f"{tag}.json", {"frames": frames, "config": {}})
    return tag


def _run(repo, out, tag):
    verify.run(out, tag, False, repo)
    return json.loads((out / "rounds" / f"{tag}_verify.json").read_text())


def test_shift_onto_paint_is_ok(repo, out):
    tag = _round(repo, out, _hline(212), _hline(200))
    v = _run(repo, out, tag)["f1.png"]["1"]
    assert v["verdict"] == "ok", v
    assert v["after_score"] >= 25 and v["after_score"] >= v["before_score"] + 15
    assert v["max_move"] <= 25


def test_shift_off_paint_is_bad(repo, out):
    tag = _round(repo, out, _hline(212), _hline(230))
    v = _run(repo, out, tag)["f1.png"]["1"]
    assert v["verdict"] == "bad" and any("not on paint" in r for r in v["reasons"])


def test_shift_onto_neighbour_is_bad(repo, out):
    tag = _round(repo, out, _hline(212), _hline(200), neighbours=[_hline(200)])
    v = _run(repo, out, tag)["f1.png"]["1"]
    assert v["verdict"] == "bad" and v["neighbour_frac"] >= 0.3
    assert any("another label" in r for r in v["reasons"])


def test_new_kink_is_bad(repo, out):
    after = [(50, 200), (200, 200), (220, 225)]
    tag = _round(repo, out, [(50, 212), (220, 212)], after)
    v = _run(repo, out, tag)["f1.png"]["1"]
    assert v["verdict"] == "bad" and v["turn_after"] > 35 >= v["turn_before"]
    assert any("turn" in r for r in v["reasons"])


def test_small_improvement_is_check(repo, out):
    tag = _round(repo, out, _hline(212), _hline(200), core=(125, 130, 135, 130, 125))
    v = _run(repo, out, tag)["f1.png"]["1"]
    assert v["verdict"] == "check", v
    assert 15 <= v["after_score"] < 25


def test_delete_and_hand_are_check(repo, out):
    extra = {"9": {"before": _snap(_label("9", _hline(100))), "after": None}}
    tag = _round(repo, out, _hline(212), _hline(200), changes_extra=extra, hand=True)
    r = _run(repo, out, tag)
    assert r["f1.png"]["9"]["verdict"] == "check"
    assert r["h.png"][verify.HAND] == {"verdict": "check", "reasons": ["draw by hand"]}


def _issue(t, feats, cat, to):
    return Issue(t, STEM, "f1.png", "1", cat, 0.9, "r", (1.0, 2.0), {"op": "set_category", "to": to},
                 False, [], feats)


@pytest.mark.parametrize("cat,to,feats,want", [
    ("without_curb_road_edge", "curb_road_edge", {"near_band_votes": 3, "share": 0.9, "cad_soft": True}, "ok"),
    ("without_curb_road_edge", "curb_road_edge", {"near_band_votes": 2, "share": 0.95, "cad_soft": True}, "check"),
    ("without_curb_road_edge", "curb_road_edge", {"near_band_votes": 1, "share": 0.7, "cad_soft": False}, "ok"),
    ("curb_road_edge", "without_curb_road_edge", {"n_votes": 8, "cad_soft": None}, "ok"),
    ("curb_road_edge", "without_curb_road_edge", {"n_votes": 7, "cad_soft": True}, "check"),
    ("curb_road_edge", "without_curb_road_edge", {"n_votes": 20, "cad_soft": False}, "check"),
])
def test_set_category_curb_soft(repo, out, cat, to, feats, want):
    b, a = _label("1", _hline(100), cat), _label("1", _hline(100), to)
    rec = {"before": _snap(b), "after": _snap(a)}
    assert verify._set_category(_issue("curb_soft", feats, cat, to), rec)["verdict"] == want


def test_curb_soft_long_uniform_bright_strip_is_check(repo, out):
    b, a = _label("1", _hline(100), "without_curb_road_edge"), _label("1", _hline(100), "curb_road_edge")
    rec = {"before": _snap(b), "after": _snap(a)}
    feats = {"near_band_votes": 15, "share": 1.0, "cad_soft": True, "outside_texture": 17.0}
    v = verify._set_category(_issue("curb_soft", feats, "without_curb_road_edge", "curb_road_edge"), rec)
    assert v["verdict"] == "check"
    assert "long uniform bright strip" in v["reasons"][0]
    # texture just above the cut-off, or a shorter strip, keeps the normal ok rule
    for f in ({**feats, "outside_texture": 17.5}, {**feats, "near_band_votes": 14}):
        assert verify._set_category(_issue("curb_soft", f, "without_curb_road_edge", "curb_road_edge"), rec)["verdict"] == "ok"


def test_set_category_other_types_and_via_run(repo, out):
    b, a = _label("1", _hline(100), SOLID), _label("1", _hline(100), "dashed_white_line")
    rec = {"before": _snap(b), "after": _snap(a)}
    assert verify._set_category(_issue("solid_dashed", {}, SOLID, "dashed_white_line"), rec)["verdict"] == "check"
    cat, to = "without_curb_road_edge", "curb_road_edge"
    cv2.imwrite(str(repo / "local-data" / "f1.png"), _image())
    bb, aa = _label("1", _hline(100), cat), _label("1", _hline(100), to)
    tag = f"{STEM}_fix_curb_soft_v1"
    _write(out / "rounds" / f"{tag}_changes.json", {"f1.png": {"1": {"before": _snap(bb), "after": _snap(aa)}}})
    _write(out / "rounds" / f"{tag}_items.json", {"frames": [{"name": "f1.png", "url": "f1.png", "labels": [aa]}], "config": {}})
    from dataclasses import asdict
    (out / "issues_curb_soft.jsonl").write_text(json.dumps(asdict(_issue(
        "curb_soft", {"near_band_votes": 5, "share": 1.0, "cad_soft": True}, cat, to))) + "\n")
    assert _run(repo, out, tag)["f1.png"]["1"]["verdict"] == "ok"


def test_all_skips_rounds_with_verdict_file(repo, out):
    tag = _round(repo, out, _hline(212), _hline(200))
    assert list(verify.run(out, None, True, repo)) == [tag]
    assert verify.run(out, None, True, repo) == {}


def test_cli_writes_file(repo, out, monkeypatch):
    monkeypatch.setattr(verify, "REPO_ROOT", repo)
    tag = _round(repo, out, _hline(212), _hline(200))
    assert cli.main(["verify", "--round", tag, "--out", str(out)]) == 0
    assert (out / "rounds" / f"{tag}_verify.json").exists()
    assert cli.main(["verify", "--out", str(out)]) == 2


# ---- View B ----
def _vb_setup(repo, out):
    """Round 1: frames a (ok), b (bad), c (check), d (ok); round order a,b,c,d."""
    names = "abcd"
    frames, changes, ver = [], {}, {}
    for n, vd in zip(names, ("ok", "bad", "check", "ok")):
        f = f"{n}.png"
        lab = _label("1", _hline(200))
        frames.append({"name": f, "url": f, "labels": [lab]})
        changes[f] = {"1": {"before": _snap(_label("1", _hline(212))), "after": _snap(lab)}}
        ver[f] = {"1": {"verdict": vd, "reasons": [f"why {vd}"]}}
    tag = f"{STEM}_fix_position_v1"
    _write(out / "rounds" / f"{tag}_changes.json", changes)
    _write(out / "rounds" / f"{tag}_items.json", {"frames": frames, "config": {}})
    _write(out / "rounds" / f"{tag}_verify.json", ver)
    return tag


def test_viewb_badges_filter_order_counts(repo, out):
    tag = _vb_setup(repo, out)
    rows = build_rows(out, repo)
    by = {r["frame_name"]: r for r in rows}
    assert by["b.png"]["changes"][0]["verdict"] == "bad"
    assert by["b.png"]["changes"][0]["reasons"] == ["why bad"]
    assert by["a.png"]["all_ok"] and not by["b.png"]["all_ok"]
    needs = select_rows(rows, tag, "needs")
    assert [r["frame_name"] for r in needs["rows"]] == ["b.png", "c.png"]  # bad before check
    assert needs["counts"] == {"ok": 2, "check": 1, "bad": 1, "unverified": 0}
    assert [r["frame_name"] for r in select_rows(rows, tag, "bad")["rows"]] == ["b.png"]
    assert [r["frame_name"] for r in select_rows(rows, tag, "all")["rows"]] == [
        "b.png", "c.png", "a.png", "d.png"]  # bad, check, ok (ok in item order)


def test_viewb_unverified_counts_as_check(repo, out):
    tag = _vb_setup(repo, out)
    (out / "rounds" / f"{tag}_verify.json").unlink()
    res = select_rows(build_rows(out, repo), tag, "needs")
    assert len(res["rows"]) == 4 and res["counts"]["unverified"] == 4


def test_viewb_server_view_and_page(repo, out):
    tag = _vb_setup(repo, out)
    srv = make_server(out, port=0, repo_root=repo, originals_dir=repo / "orig")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        html = urllib.request.urlopen(base + "/viewb").read().decode()
        assert 'id="show"' in html and "needs a look" in html and "all changes verified" in html
        assert "open anyway" in html
        d = json.loads(urllib.request.urlopen(f"{base}/viewb/view?round={tag}&show=needs").read())
        assert [r["frame_name"] for r in d["rows"]] == ["b.png", "c.png"]
        d = json.loads(urllib.request.urlopen(f"{base}/viewb/view?show=all").read())
        assert len(d["rows"]) == 4
    finally:
        srv.shutdown()
        srv.server_close()
