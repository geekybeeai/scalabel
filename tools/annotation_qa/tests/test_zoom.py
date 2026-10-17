import json
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from urllib.parse import quote

import cv2
import numpy as np
import pytest

from annotation_qa import locator, review_server, zoom
from annotation_qa.issues import Issue
from annotation_qa.review_server import calibrate_html, index_html, make_server

STEM = "proj_export_a"


def _label(lid, y):
    return {"id": lid, "category": "solid_single_line",
            "poly2d": [{"vertices": [[20, y], [380, y]], "types": "LL", "closed": False}]}


def _issue(frame, lid, conf, t="shape", anchor=(200.0, 100.0)):
    return Issue(t, STEM, frame, lid, "solid_single_line", conf, "why", anchor, None, False, [])


@pytest.fixture
def env(tmp_path):
    root = tmp_path
    (root / "local-data/items").mkdir(parents=True)
    img = np.full((400, 400, 3), 110, np.uint8)
    names = ["a.png", "b.png", "c.png"]
    frames = []
    for n in names:
        cv2.imwrite(str(root / "local-data/items" / n), img)
        frames.append({"name": n, "url": f"items/{n}", "labels": [_label("1", 100), _label("2", 200)]})
    (root / "local-data/scalabel/projects/proj").mkdir(parents=True)
    (root / "local-data/scalabel/projects/proj/project.json").write_text(
        json.dumps({"config": {"taskSize": 2}}))
    orig = tmp_path / "orig"
    orig.mkdir()
    (orig / f"{STEM}.json").write_text(json.dumps({"frames": frames, "config": {}}))
    out = tmp_path / "out"
    out.mkdir()
    issues = {"shape": [_issue("a.png", "1", .5), _issue("a.png", "2", .6, anchor=(50., 300.)),
                        _issue("b.png", "1", .95), _issue("c.png", "1", .7),
                        _issue("c.png", "2", .4), _issue("c.png", "3", .3)],
              "position": [_issue("a.png", "9", .5, "position")]}
    for t, lst in issues.items():
        (out / f"issues_{t}.jsonl").write_text("".join(json.dumps(i.__dict__) + "\n" for i in lst))
    return out, root, orig, issues


@pytest.fixture
def server(env):
    out, root, orig, _ = env
    srv = make_server(out, port=0, repo_root=root, originals_dir=orig)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def fetch(srv, p):
    with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}{p}") as r:
        return r.read()


def status(srv, p):
    try:
        fetch(srv, p)
    except urllib.error.HTTPError as e:
        return e.code
    return 200


def test_zoom_renders_caches_and_serves(server, env):
    out, _, _, issues = env
    key = issues["shape"][0].key
    body = fetch(server, "/zoom?key=" + quote(key, safe=""))
    img = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[:2] == (1320, 2 * 1320 + 8)
    f = zoom.zoom_path(out, key)
    assert f.exists() and f.parent == out / "zoom"
    mt = f.stat().st_mtime_ns
    assert fetch(server, "/zoom?key=" + quote(key, safe="")) == body
    assert f.stat().st_mtime_ns == mt  # served from the file, not re-rendered
    raw = fetch(server, "/zoom?raw=1&key=" + quote(key, safe=""))
    rimg = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    assert rimg.shape[:2] == (1320, 1320)
    assert zoom.zoom_path(out, key, raw=True).exists() and zoom.zoom_path(out, key, True) != f


def test_zoom_validation(server, env):
    out, *_ = env
    assert status(server, "/zoom?key=nope") == 404
    assert status(server, "/zoom") == 404
    assert status(server, "/zoom?key=" + quote("../../etc/passwd|x|y|shape", safe="")) == 404
    assert status(server, "/zoom?key=" + quote("a|b|c|../../x", safe="")) == 404
    assert not (out / "zoom").exists() or not list((out / "zoom").glob("*"))
    assert status(server, "/locator?key=nope") == 404
    assert status(server, "/locator/frame?type=shape&stem=..&frame=x") == 404


def test_semaphore_limits_to_two():
    assert isinstance(zoom.ZOOM_SLOTS, type(threading.Semaphore(2)))
    assert zoom.ZOOM_SLOTS.acquire(False) and zoom.ZOOM_SLOTS.acquire(False)
    assert not zoom.ZOOM_SLOTS.acquire(False)
    zoom.ZOOM_SLOTS.release()
    zoom.ZOOM_SLOTS.release()


def test_link_has_item_and_label(server):
    cards = json.loads(fetch(server, "/issues?type=shape"))
    c = next(c for c in cards if c["frame_name"] == "c.png" and c["label_id"] == "2")
    assert "&item=1&label=2" in c["link"]  # c.png is index 2, taskSize 2 -> frame 1 of task 1
    assert "task_index=1" in c["link"] and "label=" not in c["frame_link"]
    assert c["frame_link"].endswith("&item=1")
    assert c["link"].endswith("&x=200&y=100") and "&x=" not in c["frame_link"]


def test_grouping_order_and_numbering(server, env):
    cards = json.loads(fetch(server, "/issues?type=shape"))
    order = [(c["frame_name"], c["n"]) for c in cards]
    # c: 3 issues first; a: 2 issues (max .6); b: 1 issue
    assert order == [("c.png", 1), ("c.png", 2), ("c.png", 3), ("a.png", 1), ("a.png", 2),
                     ("b.png", 1)]
    assert [c["label_id"] for c in cards[:3]] == ["1", "2", "3"]  # confidence desc
    assert cards[3]["group_size"] == 2 and cards[3]["other_types"] == 1
    # the overview numbers circles with the very list the cards are numbered from
    grp = review_server.ReviewState(env[0], env[1], env[2]).frame_issues("shape", STEM, "c.png")
    assert [i.label_id for i in grp] == [c["label_id"] for c in cards[:3]]
    assert fetch(server, cards[0]["overview"])[:2] == b"\xff\xd8"
    assert fetch(server, cards[0]["locator"])[:2] == b"\xff\xd8"


def test_locator_cache_and_circles(server, env):
    out, *_ = env
    cards = json.loads(fetch(server, "/issues?type=shape"))
    fetch(server, cards[0]["overview"])
    bases = list((out / "locator").glob("*.jpg"))
    assert len(bases) == 1
    mt = bases[0].stat().st_mtime_ns
    fetch(server, cards[0]["locator"])
    fetch(server, cards[1]["locator"])
    assert [p.stat().st_mtime_ns for p in (out / "locator").glob("*.jpg")] == [mt]
    big = locator.draw(np.full((50, 50, 3), 200, np.uint8), [((200.0, 200.0), "1")], 260)
    im = cv2.imdecode(np.frombuffer(big, np.uint8), cv2.IMREAD_COLOR)
    assert im.shape[1] == 260 and (im[..., 2] > 200).any() and im[..., 1].min() < 100  # red drawn


def test_prerender_groups_by_frame(env, monkeypatch):
    out, root, orig, issues = env
    loads = []

    class Spy(zoom.FrameImage):
        @property
        def bgr(self):
            if self._bgr is None:
                loads.append(self.path.name)
            return super().bgr

    monkeypatch.setattr(zoom, "FrameImage", Spy)
    n = zoom.prerender(out, root, orig)
    assert n == 7 * 2
    assert sorted(loads) == ["a.png", "b.png", "c.png"]  # one decode per frame, 2 issues in a
    assert len(list((out / "locator").glob("*.jpg"))) == 3  # base thumbnails made from that decode
    loads.clear()
    assert zoom.prerender(out, root, orig) == 0 and loads == []


def test_prerender_calibration_only(env):
    out, root, orig, issues = env
    (out / "calibration").mkdir()
    (out / "calibration/sample_shape.json").write_text(json.dumps({"keys": [issues["shape"][2].key]}))
    assert zoom.prerender(out, root, orig, calibration_only=True) == 2
    assert len(list((out / "zoom").glob("*.jpg"))) == 2


def test_page_scripts_parse_and_have_keys():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    for html in (index_html({"shape": 1}), calibrate_html("shape")):
        for k, js in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S)):
            p = zoom.Path(__import__("tempfile").mkdtemp()) / f"p{k}.js"
            p.write_text(js)
            assert subprocess.run(["node", "--check", str(p)]).returncode == 0
        assert "lbOpen" in html and "'/zoom?key='" in html
    assert "LB_ANSWER=answer" in calibrate_html("shape")
