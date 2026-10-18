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

from annotation_qa.review_server import make_server
from annotation_qa.viewb import build_rows, select_rows, viewb_html

from test_viewb import STEM, _frame, _label, _round, _snap, _write  # noqa: F401
from test_viewb import originals, out, repo  # noqa: F401 - fixtures


def _setup(out, repo, n=60, size=50, names=(0, 49, 50, 51)):
    """n frames in the project; frames not in ``names`` are draw-by-hand rows (n=0: only ``names``)."""
    frames = [_frame(f"f{i}.png", []) for i in (range(n) if n else names)]
    ch = {f"f{i}.png": {"a": {"before": _snap(_label("a")), "after": None}} for i in names}
    tag = _round(out, "shape", 1, ch, frames)
    _write(repo / "local-data/scalabel/projects" / tag / "project.json",
           {"config": {"taskSize": size}})
    return tag


def _srv(out, repo):
    srv = make_server(out, port=0, repo_root=repo, originals_dir=repo / "orig")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _post(base, body):
    req = urllib.request.Request(base + "/viewb/progress", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    try:
        return 200, json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _get(base, path):
    return json.loads(urllib.request.urlopen(base + path).read())


def test_progress_endpoint_validation_persistence_toggle(out, repo):
    tag = _setup(out, repo)
    srv, base = _srv(out, repo)
    try:
        good = {"round": tag, "frame": "f0.png", "reviewed": True}
        assert _post(base, good) == (200, {"reviewed": True})
        data = json.loads((out / "viewb_progress.json").read_text())
        assert data[tag]["f0.png"]["reviewed"] is True and "time" in data[tag]["f0.png"]
        assert list(_get(base, f"/viewb/progress?round={tag}")) == ["f0.png"]
        for bad in ({**good, "round": "nope"}, {**good, "frame": "zz.png"}, {**good, "reviewed": 1},
                    {"round": tag}):
            assert _post(base, bad)[0] == 400
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(base, "/viewb/progress?round=nope")
        assert e.value.code == 400
        assert _post(base, {**good, "reviewed": False}) == (200, {"reviewed": False})
        assert _get(base, f"/viewb/progress?round={tag}") == {}
        req = urllib.request.Request(base + "/viewb/progress", b"{}", {"Content-Type": "text/plain"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 415
    finally:
        srv.shutdown()


def test_image_numbers_and_resume_in_filtered_round(out, repo):
    tag = _setup(out, repo, 0, names=(1, 2, 3))
    rows = select_rows(build_rows(out, repo), tag, "all")
    assert [(r["frame_name"], r["n"]) for r in rows["rows"]] == [
        ("f1.png", 1), ("f2.png", 2), ("f3.png", 3)]
    assert rows["total"] == 3 and rows["reviewed"] == 0 and rows["resume"] == 0
    prog = {tag: {"f1.png": {"reviewed": True, "time": "t"}}}
    v = select_rows(build_rows(out, repo), tag, "all", prog)
    assert v["reviewed"] == 1 and v["resume"] == 1 and v["rows"][0]["reviewed"]
    v = select_rows(build_rows(out, repo), tag, "unreviewed", prog)
    assert [(r["frame_name"], r["n"]) for r in v["rows"]] == [("f2.png", 2), ("f3.png", 3)]
    assert v["total"] == 3 and v["resume"] == 0
    prog[tag].update({"f2.png": {"reviewed": True}, "f3.png": {"reviewed": True}})
    assert select_rows(build_rows(out, repo), tag, "all", prog)["resume"] == -1


def test_scalabel_frame_numbers_single_and_multi_task(out, repo):
    _setup(out, repo, 60, 50)
    rows = {r["frame_name"]: r for r in select_rows(build_rows(out, repo), "", "all")["rows"]}
    s = rows["f51.png"]["scalabel"]
    assert (s["task_index"], s["item"], s["n_tasks"]) == (1, 2, 2)
    assert rows["f0.png"]["scalabel"]["item"] == 1


def test_single_task_project_has_one_task(out, repo):
    _setup(out, repo, 5, 50, names=(4,))
    r = next(r for r in select_rows(build_rows(out, repo), "", "all")["rows"]
             if r["frame_name"] == "f4.png")
    assert r["scalabel"]["n_tasks"] == 1 and r["scalabel"]["item"] == 5


def test_merged_round_reported_reviewed_and_read_only(out, repo):
    tag = _setup(out, repo, 0, names=(1, 2))
    _write(out / "rounds" / "merged.json", [{"stem": STEM, "tag": tag}])
    v = select_rows(build_rows(out, repo), tag, "all")
    assert v["reviewed"] == v["total"] == 2 and v["resume"] == -1
    assert all(r["reviewed"] and r["locked"] for r in v["rows"])
    srv, base = _srv(out, repo)
    try:
        got = _get(base, f"/viewb/progress?round={tag}")
        assert got["f1.png"]["reviewed"] and got["f2.png"]["reviewed"]
        assert _post(base, {"round": tag, "frame": "f1.png", "reviewed": False})[0] == 400
        assert not (out / "viewb_progress.json").exists()
    finally:
        srv.shutdown()


def test_view_endpoint_includes_progress_and_page_js(out, repo):
    tag = _setup(out, repo, 0)
    srv, base = _srv(out, repo)
    try:
        _post(base, {"round": tag, "frame": "f0.png", "reviewed": True})
        d = _get(base, f"/viewb/view?round={tag}&show=all")
        assert d["reviewed"] == 1 and d["total"] == 4 and d["resume"] == 1
    finally:
        srv.shutdown()
    html = viewb_html("")
    assert 'id="resume"' in html and "not yet reviewed" in html and "/viewb/progress" in html
    if not shutil.which("node"):
        pytest.skip("node not installed")
    for js in re.findall(r"<script>(.*?)</script>", html, re.S):
        p = Path(tempfile.mkdtemp()) / "p.js"
        p.write_text(js)
        assert subprocess.run(["node", "--check", str(p)]).returncode == 0
