import json
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np
import pytest

from annotation_qa import discard_page, occlusion as oc


def _road():
    img = np.zeros((300, 600, 3), np.uint8)
    img[100:200, :] = (120, 120, 120)  # grey road strip; black outside it
    return img


def _line(y):
    return [{"id": "L", "category": "solid_single_line",
             "poly2d": [{"vertices": [[20, y], [580, y]], "types": "LL", "closed": False}]}]


def test_canopy_over_the_line_counts_grass_beside_it_does_not():
    img = _road()
    assert oc.measure_frame(img, _line(150))["line_cover"] == 0.0
    over = img.copy()
    over[120:180, 100:400] = (40, 140, 60)  # tree canopy across the line (both sides)
    r = oc.measure_frame(over, _line(150))
    assert 0.45 < r["line_cover"] < 0.6 and r["marks"]
    beside = img.copy()
    beside[152:200, 100:400] = (40, 140, 60)  # grass on one side only
    assert oc.measure_frame(beside, _line(150))["line_cover"] < 0.05
    dark = img.copy()
    dark[120:180, 100:250] = (20, 20, 20)  # deep shadow across the line
    assert oc.measure_frame(dark, _line(150))["line_cover"] > 0.2


def test_answers_are_saved_and_cleared(tmp_path):
    oc.set_answer(tmp_path, "s|a.png", "discard")
    oc.set_answer(tmp_path, "s|a.png", None, "half the road under trees")
    assert oc.read_answers(tmp_path) == {"s|a.png": {"value": "discard", "comment": "half the road under trees"}}
    oc.set_answer(tmp_path, "s|a.png", "", "")
    assert oc.read_answers(tmp_path) == {}
    with pytest.raises(ValueError):
        oc.set_answer(tmp_path, "s|a.png", "maybe")


def test_candidates_sorted_and_cut(tmp_path):
    rows = [{"stem": "s", "index": i, "name": f"{i}.png", "line_cover": c, "strip_cover": 0.1,
             "line_samples": 10, "marks": []} for i, c in enumerate([0.05, 0.3, None, 0.12])]
    keep = oc.write_candidates(tmp_path, rows, 0.10)
    assert [k["name"] for k in keep] == ["1.png", "3.png"]
    assert oc.read_candidates(tmp_path)["measured"] == 4


def _server(out, monkeypatch, tmp_path):
    from annotation_qa.review_server import make_server
    import threading
    monkeypatch.setattr(discard_page, "REPO_ROOT", tmp_path)
    srv = make_server(out, 0, repo_root=tmp_path, originals_dir=tmp_path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_discard_page_routes(tmp_path, monkeypatch):
    out = tmp_path / "out"
    (out / "work").mkdir(parents=True)
    (tmp_path / "local-data" / "items").mkdir(parents=True)
    cv2.imwrite(str(tmp_path / "local-data" / "items" / "a.png"), _road())
    (out / "work" / "s.json").write_text(json.dumps({"frames": [
        {"name": "items/a.png", "url": "items/a.png", "labels": _line(150)}]}))
    oc.write_candidates(out, [{"stem": "s", "index": 0, "name": "items/a.png", "line_cover": 0.4,
                               "strip_cover": 0.2, "line_samples": 10, "marks": [[100, 150]]}], 0.1)
    (out / "discard" / "project.json").write_text(json.dumps({"project": "qa discard"}))
    srv = _server(out, monkeypatch, tmp_path)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/discard").read().decode()
        assert "Discard review" in page
        d = json.loads(urllib.request.urlopen(base + "/discard/items").read())
        c = d["cards"][0]
        assert c["key"] == "s|items/a.png" and c["answer"] == ""
        assert "project_name=qa%20discard&task_index=0&item=1" in c["link"]
        img = urllib.request.urlopen(base + c["img"]).read()
        assert img[:2] == b"\xff\xd8"  # a JPEG
        def post(body):
            req = urllib.request.Request(base + "/discard/answer", json.dumps(body).encode(),
                                         {"Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(req).read())
        assert post({"key": c["key"], "value": "discard"})["answered"] == 1
        assert oc.read_answers(out)[c["key"]]["value"] == "discard"
        with pytest.raises(urllib.error.HTTPError) as e:
            post({"key": "s|other.png", "value": "keep"})
        assert e.value.code == 400
        with pytest.raises(urllib.error.HTTPError):
            post({"key": c["key"], "value": "maybe"})
    finally:
        srv.shutdown()


def test_page_script_has_no_syntax_error():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    for js in re.findall(r"<script>(.*?)</script>", discard_page.HTML, re.S):
        p = Path(tempfile.mkdtemp()) / "p.js"
        p.write_text(js)
        assert subprocess.run(["node", "--check", str(p)]).returncode == 0


def test_discarded_lists_only_discard_answers(tmp_path):
    oc.set_answer(tmp_path, "s|a.png", "discard")
    oc.set_answer(tmp_path, "s|b.png", "keep")
    oc.set_answer(tmp_path, "t|c.png", None, "just a comment")
    assert oc.discarded(tmp_path) == {"s|a.png"}
    oc.set_answer(tmp_path, "s|d.png", "ignore")  # skipped in decisions, not cut at the end
    assert oc.discarded(tmp_path) == {"s|a.png", "s|d.png"} and oc.to_remove(tmp_path) == {"s|a.png"}
    assert oc.discarded(tmp_path / "nowhere") == set()
