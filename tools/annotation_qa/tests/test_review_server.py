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


def _freeze_sample(out, t, keys):
    (out / "calibration").mkdir(exist_ok=True)
    (out / "calibration" / f"sample_{t}.json").write_text(json.dumps({"type": t, "keys": keys}))


def test_bulk_threshold_marks_only_above_and_keeps_false(server, out_dir):
    _freeze_sample(out_dir, "shape", ["proj_export_a|f1.png|2|shape"])  # bulk skips the sample
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
    assert first["link"] == "http://localhost:8686/label?project_name=proj&task_index=1&item=1&label=0&x=1&y=2"
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


# ---- I10: calibration status ----
def test_calibration_status_endpoint(tmp_path, repo):
    from annotation_qa.issues import calibration_sample
    out = tmp_path / "o"
    out.mkdir()
    iss = [_issue("shape", i, 0.5) for i in range(40)]
    (out / "issues_shape.jsonl").write_text("".join(json.dumps(i.__dict__) + "\n" for i in iss))
    sample = calibration_sample(out, "shape", iss)
    write_marks(out / "marks.json", {i.key: ("real" if k < 15 else "false")
                                     for k, i in enumerate(sample[:21])})
    root, orig = repo
    srv = make_server(out, port=0, repo_root=root, originals_dir=orig)
    try:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        st = json.loads(get(srv, "/calibration"))["shape"]
        assert st == {"n": 30, "answered": 21, "real": 15, "need": 20,
                      "precision": 15 / 21, "status": "PASS"}
        items = json.loads(get(srv, "/calibrate/items?type=shape"))
        assert [i["key"] for i in items] == [i.key for i in sample]  # the gated sample
        page = get(srv, "/calibrate?type=shape")
        assert "/calibration" in page and "PASS needs" in page
        assert "/calibration" in get(srv, "/") and 'class="cal"' in get(srv, "/")
    finally:
        srv.shutdown()
        srv.server_close()


# ---- M5: POST hardening ----
def _raw_post(srv, path, body: bytes, headers):
    req = urllib.request.Request(_url(srv, path), body, headers)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_post_needs_json_content_type(server, out_dir):
    assert _raw_post(server, "/marks", b'{"k":"real"}', {"Content-Type": "text/plain"}) == 415
    assert _raw_post(server, "/marks", b'{"k":"real"}',
                     {"Content-Type": "application/x-www-form-urlencoded"}) == 415
    assert read_marks(out_dir / "marks.json") == {}


def test_post_needs_local_host_header(server, out_dir):
    h = {"Content-Type": "application/json", "Host": "evil.example:8790"}
    assert _raw_post(server, "/marks", b'{"k":"real"}', h) == 403
    assert read_marks(out_dir / "marks.json") == {}
    h["Host"] = f"localhost:{server.server_address[1]}"
    assert _raw_post(server, "/marks", b'{"k":"real"}', h) == 200


def test_bulk_dry_run_counts_without_writing(server, out_dir):
    _freeze_sample(out_dir, "shape", ["proj_export_a|f1.png|2|shape"])
    r = post(server, "/marks/bulk", {"issue_type": "shape", "min_confidence": 0.8,
                                     "dry_run": True})
    assert r == {"count": 2} and read_marks(out_dir / "marks.json") == {}


def test_page_js_checks_post_response_and_confirms_bulk(server):
    html = get(server, "/")
    assert "if(!r.ok)" in html and "confirm(" in html and "dry_run:true" in html
    assert "if(!r.ok)" in get(server, "/calibrate?type=shape")


# ---- strict JSON for the browser: non-finite features must never reach the page ----
def _reject_constant(name):
    raise ValueError(f"non-finite JSON constant {name} in browser payload")


def _strict_loads(text):
    return json.loads(text, parse_constant=_reject_constant)


def test_finite_helper_replaces_non_finite_recursively():
    import math
    from annotation_qa.review_server import _dumps, _finite
    obj = {"a": float("inf"), "b": [float("-inf"), float("nan"), 1.5, 2, True], "c": {"d": "x", "e": None}}
    assert _finite(obj) == {"a": None, "b": [None, None, 1.5, 2, True], "c": {"d": "x", "e": None}}
    assert _strict_loads(_dumps(obj))["b"][2] == 1.5
    assert math.isfinite(_finite(3.0))


def test_calibrate_items_strict_json_with_inf_features(tmp_path, repo):
    from dataclasses import replace
    out = tmp_path / "o"
    out.mkdir()
    iss = [replace(_issue("shape", i, 0.5), features={"on_cv": float("inf"), "near": float("nan"), "n": 3})
           for i in range(3)]
    (out / "issues_shape.jsonl").write_text("".join(json.dumps(i.__dict__) + "\n" for i in iss))
    root, orig = repo
    srv = make_server(out, port=0, repo_root=root, originals_dir=orig)
    try:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        items = _strict_loads(get(srv, "/calibrate/items?type=shape"))
        assert len(items) == 3
        assert items[0]["features"] == {"on_cv": None, "near": None, "n": 3}
        assert _strict_loads(get(srv, "/issues?type=shape"))
        page = get(srv, "/calibrate?type=shape")
        import re
        embedded = re.search(r"const T=(.*?);", page).group(1)
        assert _strict_loads(embedded) == "shape"
        assert "Infinity" not in page and "NaN" not in page
    finally:
        srv.shutdown()
        srv.server_close()


# ---- review 2: I4 bulk vs calibration, bulk body, Host check on GET, escaped links ----
def test_bulk_cannot_fake_a_calibration_pass(tmp_path):
    from annotation_qa.review_server import ReviewState
    out = tmp_path / "out"
    out.mkdir()
    many = [_issue("shape", i, 0.9, frame=f"f{i}.png") for i in range(45)]
    (out / "issues_shape.jsonl").write_text("".join(json.dumps(i.__dict__) + "\n" for i in many))
    st = ReviewState(out, tmp_path, tmp_path)
    assert st.calibration_status()["shape"]["status"] == "NOT CALIBRATED"
    n = st.bulk("shape", 0.0)
    status = st.calibration_status()["shape"]
    assert status["status"] == "NOT CALIBRATED" and status["answered"] == 0
    assert n == 45 - status["n"] == 15  # everything outside the frozen sample of 30
    sample = set(json.loads((out / "calibration" / "sample_shape.json").read_text())["keys"])
    assert not sample & set(read_marks(out / "marks.json"))


def test_bulk_skips_keys_of_other_types_samples_too(tmp_path):
    from annotation_qa.review_server import ReviewState
    out = tmp_path / "out"
    out.mkdir()
    iss = [_issue("shape", i, 0.9, frame=f"f{i}.png") for i in range(40)]
    (out / "issues_shape.jsonl").write_text("".join(json.dumps(i.__dict__) + "\n" for i in iss))
    _freeze_sample(out, "shape", [iss[0].key])
    _freeze_sample(out, "other", [iss[1].key])
    assert ReviewState(out, tmp_path, tmp_path).bulk("shape", 0.0) == 38


@pytest.mark.parametrize("body", [b"[]", b'"x"', b"1", b'{"issue_type": "shape"}',
                                  b'{"issue_type": 3, "min_confidence": 0.5}',
                                  b'{"issue_type": "shape", "min_confidence": true}',
                                  b'{"issue_type": "shape", "min_confidence": "0.5"}'])
def test_bulk_body_validated(server, out_dir, body):
    h = {"Content-Type": "application/json", "Host": f"127.0.0.1:{server.server_address[1]}"}
    assert _raw_post(server, "/marks/bulk", body, h) == 400
    assert read_marks(out_dir / "marks.json") == {}


@pytest.mark.parametrize("path", ["/", "/marks", "/issues?type=shape", "/viewb/rows", "/comments"])
def test_get_needs_local_host_header(server, path):
    import urllib.error
    req = urllib.request.Request(_url(server, path), headers={"Host": "evil.example:8790"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 403
    ok = urllib.request.Request(_url(server, path),
                                headers={"Host": f"localhost:{server.server_address[1]}"})
    assert urllib.request.urlopen(ok).status == 200


def test_links_quote_project_and_page_escapes_urls(tmp_path, server):
    from annotation_qa.review_server import ReviewState
    i = _issue("shape", 1, 0.9, stem='a b&c"<x>_export_z')
    card = ReviewState(tmp_path, tmp_path, tmp_path).card(i, {})
    assert "project_name=a%20b%26c%22%3Cx%3E&" in card["link"]
    assert "project_name=a%20b%26c%22%3Cx%3E&" in card["frame_link"]
    html = get(server, "/")
    for k in ("link", "frame_link", "crop", "locator", "overview"):
        assert "${it.%s}" % k not in html
    assert 'href="${esc(it.link)}"' in html and 'src="${esc(it.crop)}"' in html
    cal = get(server, "/calibrate?type=shape")
    assert "${it.link}" not in cal and "${it.crop}" not in cal
