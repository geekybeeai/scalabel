import json
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

import pytest

from annotation_qa.__main__ import main
from annotation_qa.review_server import calibrate_html, index_html

from test_review_server import (_raw_post, _url, get, out_dir, post,  # noqa: F401
                                 repo, server)

KEY = "proj_export_a|f1.png|0|shape"


def test_post_get_roundtrip_and_metadata(server, out_dir):
    r = post(server, "/comments", {"key": KEY, "text": "looks wrong\nsee left"})
    assert r == {"saved": True, "count": 1}
    c = json.loads(get(server, "/comments"))
    e = c[KEY]
    assert e["text"] == "looks wrong\nsee left" and e["updated"]
    assert (e["issue_type"], e["export_stem"], e["frame_name"], e["label_id"]) == (
        "shape", "proj_export_a", "f1.png", "0")
    assert json.loads((out_dir / "comments.json").read_text()) == c
    assert not list(out_dir.glob("comments.json.tmp"))


def test_empty_text_deletes(server, out_dir):
    post(server, "/comments", {"key": KEY, "text": "x"})
    assert post(server, "/comments", {"key": KEY, "text": "  "}) == {"saved": True, "count": 0}
    assert json.loads(get(server, "/comments")) == {}


def test_unknown_key_and_too_long_are_400(server):
    h = {"Content-Type": "application/json"}
    for body in ({"key": "nope|x|1|shape", "text": "a"}, {"key": KEY, "text": "a" * 4001},
                 {"key": KEY}, {"text": "a"}, {"key": KEY, "text": 5}):
        assert _raw_post(server, "/comments", json.dumps(body).encode(), h) == 400
    assert _raw_post(server, "/comments", json.dumps({"key": KEY, "text": "a" * 4000}).encode(),
                     h) == 200


def test_content_type_and_host_checked(server, out_dir):
    b = json.dumps({"key": KEY, "text": "a"}).encode()
    assert _raw_post(server, "/comments", b, {"Content-Type": "text/plain"}) == 415
    assert _raw_post(server, "/comments", b,
                     {"Content-Type": "application/json", "Host": "evil.example"}) == 403
    assert json.loads(get(server, "/comments")) == {}


def test_prefilled_in_page_data(server):
    post(server, "/comments", {"key": KEY, "text": "hello"})
    items = json.loads(get(server, "/issues?type=shape"))
    assert {i["key"]: i["comment"] for i in items}[KEY] == "hello"
    assert all(i["comment"] == "" for i in items if i["key"] != KEY)
    assert "Comments (<span id=\"ncm\">1</span>)" in get(server, "/")
    assert "Comment for Claude (optional)" in get(server, "/")
    assert "Comment for Claude (optional)" in get(server, "/calibrate?type=shape")
    cal = json.loads(get(server, "/calibrate/items?type=shape"))
    assert any(i["comment"] == "hello" for i in cal)


def test_comments_view_lists_grouped(server):
    post(server, "/comments", {"key": KEY, "text": "a <b>bold</b> note"})
    post(server, "/comments", {"key": "proj_export_a|f1.png|10|position", "text": "pos note"})
    html = get(server, "/comments/view")
    assert "shape (1)" in html and "position (1)" in html
    assert "a &lt;b&gt;bold&lt;/b&gt; note" in html and "<b>bold</b>" not in html
    assert "/crops/shape/" in html and "Open in Scalabel" in html
    assert "label=0" in html and "f1.png" in html


def test_cli_prints_comments(server, out_dir, capsys):
    assert main(["comments", "--out", str(out_dir)]) == 0
    assert "no comments" in capsys.readouterr().out
    post(server, "/comments", {"key": KEY, "text": "fix this"})
    assert main(["comments", "--out", str(out_dir)]) == 0
    assert capsys.readouterr().out.strip() == "shape · f1.png · 0 · fix this"


def test_page_scripts_parse_and_guard_keys():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    for html in (index_html({"shape": 1}, 2), calibrate_html("shape", 2)):
        for js in re.findall(r"<script>(.*?)</script>", html, re.S):
            p = Path(tempfile.mkdtemp()) / "p.js"
            p.write_text(js)
            assert subprocess.run(["node", "--check", str(p)]).returncode == 0
        assert "TEXTAREA" in html and "setTimeout(()=>cmtSave(i),600)" in html
        assert 'id="lbcm"' in html and 'rows="2"' in html
