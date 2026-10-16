import json
import threading
import urllib.request
from pathlib import Path

import pytest

from annotation_qa.issues import Issue, read_marks, write_marks
from annotation_qa.review_server import load_issues_tolerant, make_server


def _issue(t, i, conf, frame="f1.png", stem="proj_export_a", hand=False):
    return Issue(t, stem, frame, str(i), "solid_single_line", conf, f"why {i}",
                 (1.0, 2.0), None, hand, [])


@pytest.fixture
def out_dir(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    shape = [_issue("shape", i, c) for i, c in enumerate([0.9, 0.8, 0.5])]
    pos = [_issue("position", 10, 0.7), _issue("position", 11, 0.2, frame="f2.png")]
    for t, lst in (("shape", shape), ("position", pos)):
        (out / f"issues_{t}.jsonl").write_text(
            "".join(json.dumps(i.__dict__) + "\n" for i in lst))
    return out


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "local-data/scalabel/projects/proj").mkdir(parents=True)
    (tmp_path / "local-data/scalabel/projects/proj/project.json").write_text(
        json.dumps({"config": {"taskSize": 2}}))
    orig = tmp_path / "orig"
    orig.mkdir()
    frames = [{"name": n, "labels": []} for n in ("a.png", "b.png", "f1.png", "f2.png")]
    (orig / "proj_export_a.json").write_text(json.dumps({"frames": frames, "config": {}}))
    return tmp_path, orig


@pytest.fixture
def server(out_dir, repo):
    root, orig = repo
    srv = make_server(out_dir, port=0, repo_root=root, originals_dir=orig)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _url(srv, p):
    return f"http://127.0.0.1:{srv.server_address[1]}{p}"


def get(srv, p):
    with urllib.request.urlopen(_url(srv, p)) as r:
        return r.read().decode()


def post(srv, p, body):
    req = urllib.request.Request(_url(srv, p), json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def test_post_marks_merges_and_persists(server, out_dir):
    assert post(server, "/marks", {"k1": "real"}) == {"saved": 1}
    post(server, "/marks", {"k2": "false"})
    assert read_marks(out_dir / "marks.json") == {"k1": "real", "k2": "false"}
    assert json.loads(get(server, "/marks")) == {"k1": "real", "k2": "false"}


def test_index_lists_tabs_with_counts(server):
    html = get(server, "/")
    assert "shape (3)" in html and "position (2)" in html
    assert "/calibrate?type=shape" in html


def test_bulk_threshold_marks_only_above_and_keeps_false(server, out_dir):
    write_marks(out_dir / "marks.json", {"proj_export_a|f1.png|1|shape": "false"})
    r = post(server, "/marks/bulk", {"issue_type": "shape", "min_confidence": 0.8})
    assert r == {"saved": 1}
    assert read_marks(out_dir / "marks.json") == {
        "proj_export_a|f1.png|1|shape": "false",
        "proj_export_a|f1.png|0|shape": "real"}


def test_issues_api_sorted_with_link_and_other_count(server):
    items = json.loads(get(server, "/issues?type=shape"))
    assert [i["confidence"] for i in items] == [0.9, 0.8, 0.5]
    first = items[0]
    assert first["others"] == 3 and first["frame_no"] == 1  # f1.png is index 2 -> task 1, frame 1
    assert first["link"] == "http://localhost:8686/label?project_name=proj&task_index=1"
    assert first["project_found"] is True
    assert first["crop"].startswith("/crops/shape/")


def test_missing_project_marked(out_dir, tmp_path):
    srv = make_server(out_dir, port=0, repo_root=tmp_path, originals_dir=tmp_path)
    try:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        first = json.loads(get(srv, "/issues?type=shape"))[0]
        assert first["project_found"] is False and "task_index=0" in first["link"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_crops_served_and_traversal_blocked(server, out_dir):
    (out_dir / "crops/shape").mkdir(parents=True)
    (out_dir / "crops/shape/x.jpg").write_bytes(b"JPEG")
    assert get(server, "/crops/shape/x.jpg") == "JPEG"
    with pytest.raises(urllib.error.HTTPError) as e:
        get(server, "/crops/../issues_shape.jsonl")
    assert e.value.code == 404


def test_calibrate_sample_fixed_30(tmp_path, repo):
    out = tmp_path / "o"
    out.mkdir()
    (out / "issues_shape.jsonl").write_text(
        "".join(json.dumps(_issue("shape", i, 0.5).__dict__) + "\n" for i in range(50)))
    root, orig = repo
    srv = make_server(out, port=0, repo_root=root, originals_dir=orig)
    try:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        a = json.loads(get(srv, "/calibrate/items?type=shape"))
        b = json.loads(get(srv, "/calibrate/items?type=shape"))
        assert len(a) == 30 and a == b
        assert "YES" in get(srv, "/calibrate?type=shape")
    finally:
        srv.shutdown()
        srv.server_close()


def test_tolerant_reader_skips_truncated_line(tmp_path):
    p = tmp_path / "issues_shape.jsonl"
    good = json.dumps(_issue("shape", 1, 0.5).__dict__)
    p.write_text(good + "\n" + good[:20])
    assert len(load_issues_tolerant(p)) == 1


def test_binds_loopback_only(server):
    assert server.server_address[0] == "127.0.0.1"
