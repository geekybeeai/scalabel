import json
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from annotation_qa import fix as fixmod
from annotation_qa.final import finalize
from annotation_qa.merge import merge_reviewed
from annotation_qa.review_server import make_server
from annotation_qa.rounds import merge_markers
from annotation_qa.viewb import build_rows, select_rows, viewb_html

STEM = "exp"
TAG = f"{STEM}_fix_shape_v1"


def _label(lid, cat="solid_single_line", y=100.0):
    return {"id": lid, "category": cat, "attributes": {}, "manualShape": True,
            "poly2d": [{"vertices": [[20, y], [180, y]], "types": "LL", "closed": False}],
            "box2d": None, "box3d": None}


def _snap(l):
    return {"category": l["category"], "poly2d": l["poly2d"]}


def _w(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def _exp(frames):
    return {"frames": frames, "config": {}}


def _frame(labels):
    return {"name": "f1.png", "url": "f1.png", "labels": labels}


@pytest.fixture
def out(tmp_path, monkeypatch):
    from annotation_qa import data
    orig = tmp_path / "orig"
    orig.mkdir()
    monkeypatch.setattr(data, "ORIGINALS_DIR", orig)
    monkeypatch.setattr(fixmod, "ORIGINALS_DIR", orig)
    o = tmp_path / "out"
    a1, a2 = _label("1", y=110), _label("2", "dashed_white_line", y=130)
    ch = {"f1.png": {"1": {"before": _snap(_label("1", y=100)), "after": _snap(a1)},
                     "2": {"before": _snap(_label("2", y=130)), "after": _snap(a2)},
                     "3": {"before": _snap(_label("3", y=150)), "after": None}}}
    _w(o / "rounds" / f"{TAG}_changes.json", ch)
    _w(o / "rounds" / f"{TAG}_items.json", _exp([_frame([a1, a2, _label("4", y=170)])]))
    _w(o / "work" / f"{STEM}.json", _exp([_frame([_label("1", y=100), _label("2", y=130),
                                                  _label("3", y=150), _label("4", y=170)])]))
    return o


def _revert(out, *labels, on=True):
    from annotation_qa import reverts
    for l in labels:
        reverts.set_revert(out, TAG, "f1.png", l, on)


def _merge(out, tmp_path, labels):
    rev = tmp_path / "rev.json"
    _w(rev, _exp([_frame(labels)]))
    merge_reviewed(STEM, rev, out, round_tag=TAG)
    work = json.loads((out / "work" / f"{STEM}.json").read_text())
    return {l["id"]: l for l in work["frames"][0]["labels"]}


def _proposed():
    return [_label("1", y=110), _label("2", "dashed_white_line", y=130), _label("4", y=170)]


def test_endpoint_validation_persistence_undo(out, tmp_path):
    srv = make_server(out, port=0, repo_root=tmp_path, originals_dir=tmp_path / "orig")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def post(body):
        req = urllib.request.Request(base + "/viewb/revert", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        try:
            return 200, json.loads(urllib.request.urlopen(req).read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    try:
        good = {"round": TAG, "frame": "f1.png", "label": "1", "revert": True}
        assert post(good) == (200, {"reverted": True})
        data = json.loads((out / "reverts.json").read_text())
        assert "time" in data[TAG]["f1.png"]["1"]
        got = json.loads(urllib.request.urlopen(base + f"/viewb/reverts?round={TAG}").read())
        assert list(got["f1.png"]) == ["1"]
        for bad in ({**good, "round": "nope"}, {**good, "frame": "zz.png"}, {**good, "label": "99"},
                    {**good, "revert": "yes"}):
            assert post(bad)[0] == 400
        assert post({**good, "revert": False}) == (200, {"reverted": False})
        assert not (json.loads((out / "reverts.json").read_text()))
        assert json.loads(urllib.request.urlopen(base + f"/viewb/reverts?round={TAG}").read()) == {}
    finally:
        srv.shutdown()
        srv.server_close()


def test_merge_applies_revert_when_untouched(out, tmp_path):
    _revert(out, "1", "2")
    got = _merge(out, tmp_path, _proposed())
    assert got["1"]["poly2d"][0]["vertices"][0][1] == 100.0
    assert got["2"]["category"] == "solid_single_line"
    assert got["4"]["poly2d"][0]["vertices"][0][1] == 170.0
    (m,) = merge_markers(out)
    assert m["reverted_applied"] == 2 and m["reverted_conflicts"] == 0


def test_scalabel_edit_wins_with_warning(out, tmp_path, capsys):
    _revert(out, "1")
    labels = _proposed()
    labels[0] = _label("1", y=125)
    got = _merge(out, tmp_path, labels)
    assert got["1"]["poly2d"][0]["vertices"][0][1] == 125.0
    assert "label 1 was edited in Scalabel; Scalabel edit wins over the revert" in capsys.readouterr().out
    (m,) = merge_markers(out)
    assert (m["reverted_applied"], m["reverted_conflicts"]) == (0, 1)


def test_deleted_in_review_stays_deleted(out, tmp_path, capsys):
    _revert(out, "1")
    got = _merge(out, tmp_path, _proposed()[1:])
    assert "1" not in got
    assert "deleted in Scalabel" in capsys.readouterr().out
    assert merge_markers(out)[0]["reverted_conflicts"] == 1


def test_tool_delete_revert_readds_label(out, tmp_path):
    _revert(out, "3")
    got = _merge(out, tmp_path, _proposed())
    assert got["3"]["category"] == "solid_single_line"
    assert got["3"]["poly2d"][0]["vertices"][0][1] == 150.0
    assert got["3"]["attributes"] == {} and merge_markers(out)[0]["reverted_applied"] == 1


def test_viewb_rows_show_reverted_and_counts(out, tmp_path):
    _revert(out, "1")
    (r,) = build_rows(out, tmp_path)
    got = {c["label_id"]: c for c in r["changes"]}
    assert got["1"]["reverted"] and got["1"]["revert_state"] == "pending"
    assert not got["2"]["reverted"]
    assert select_rows([r])["counts"]["unverified"] == 2
    _merge(out, tmp_path, _proposed())
    (r,) = build_rows(out, tmp_path)
    c = next(c for c in r["changes"] if c["label_id"] == "1")
    assert c["revert_state"] == "applied" and c["review"] == "reverted"


def test_final_counts_applied_reverts_as_no_change(out, tmp_path):
    _revert(out, "1")
    _merge(out, tmp_path, _proposed())
    exported, _ = finalize(out, tmp_path / "orig")
    assert exported[STEM] == 2


def test_revert_refused_after_merge(out, tmp_path):
    _merge(out, tmp_path, _proposed())
    srv = make_server(out, port=0, repo_root=tmp_path, originals_dir=tmp_path / "orig")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.server_address[1]}/viewb/revert",
            json.dumps({"round": TAG, "frame": "f1.png", "label": "1", "revert": True}).encode(),
            {"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 400
    finally:
        srv.shutdown()
        srv.server_close()


def test_page_script_has_revert_and_parses():
    html = viewb_html("")
    assert "/viewb/revert" in html and "Undo revert" in html and "REVERTED" in html
    if not shutil.which("node"):
        pytest.skip("node not installed")
    for js in re.findall(r"<script>(.*?)</script>", html, re.S):
        p = Path(tempfile.mkdtemp()) / "p.js"
        p.write_text(js)
        assert subprocess.run(["node", "--check", str(p)]).returncode == 0
