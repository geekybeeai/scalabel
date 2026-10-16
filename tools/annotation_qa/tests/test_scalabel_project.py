import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from annotation_qa.scalabel_project import create, parse_multipart


class _Srv:
    def __init__(self):
        outer = self
        self.fields, self.files, self.paths, self.names = {}, {}, [], []
        self.responses: list[tuple[int, bytes]] = []  # queued (status, body); default 200 ok

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.paths.append(self.path)
                outer.fields, outer.files = parse_multipart(self.headers["Content-Type"], body)
                outer.names.append(outer.fields.get("project_name"))
                code, text = outer.responses.pop(0) if outer.responses else (200, b"ok")
                self.send_response(code)
                self.send_header("Content-Length", str(len(text)))
                self.end_headers()
                self.wfile.write(text)

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


@pytest.fixture
def srv():
    s = _Srv()
    yield s
    s.httpd.shutdown()


def test_create_posts_expected_fields(tmp_path, srv):
    items = tmp_path / "items.json"
    items.write_text(json.dumps({"frames": [{"name": "a"}, {"name": "b"}], "config": {}}))
    url = create(items, "x_fix_curb_soft_v1", server=srv.url, projects_dir=tmp_path / "projects")
    f = srv.fields
    assert srv.paths == ["/postProject"]
    assert f["auto_correct"] == "false" and f["label_type"] == "polyline2d"
    assert f["item_type"] == "image" and f["page_title"] == "2D Lane"
    assert f["task_size"] == "50" and f["demo_mode"] == "false"
    assert f["project_name"] == "x_fix_curb_soft_v1"
    assert f["instructions"] == "https://doc.scalabel.ai/instructions/segmentation.html"
    assert "item_file" in srv.files and "categories" in srv.files
    assert srv.files["item_file"] == items.read_bytes()
    cats = srv.files["categories"].decode()
    assert cats.splitlines()[0] == "- name: center_turn_lane" and len(cats.splitlines()) == 6
    assert url == f"{srv.url}/dashboard?project_name=x_fix_curb_soft_v1"


# ---- I4: failures are reported and do not strand the other rounds ----
from annotation_qa import __main__ as cli  # noqa: E402
from annotation_qa import fix as fixmod  # noqa: E402
from annotation_qa import scalabel_project  # noqa: E402
from annotation_qa.issues import Issue, write_issues, write_marks  # noqa: E402
from annotation_qa.scalabel_project import ProjectCreateError, ProjectExists  # noqa: E402


def test_create_raises_with_server_body(tmp_path, srv):
    items = tmp_path / "items.json"
    items.write_text(json.dumps({"frames": [{"name": "a"}], "config": {}}))
    srv.responses.append((400, b"Project name already exists."))
    with pytest.raises(ProjectCreateError, match="HTTP 400.*Project name already exists"):
        create(items, "p1", server=srv.url, projects_dir=tmp_path)


def test_create_skips_existing_project_dir(tmp_path, srv):
    items = tmp_path / "items.json"
    items.write_text(json.dumps({"frames": [], "config": {}}))
    (tmp_path / "p1").mkdir()
    with pytest.raises(ProjectExists):
        create(items, "p1", server=srv.url, projects_dir=tmp_path)
    assert srv.paths == []


@pytest.fixture
def two_stems(tmp_path, monkeypatch):
    orig = tmp_path / "orig"
    orig.mkdir()
    lab = {"id": "a", "category": "solid_single_line", "attributes": {}, "manualShape": True,
           "poly2d": [{"vertices": [[0, 0], [9, 0]], "types": "LL", "closed": False}],
           "box2d": None, "box3d": None}
    issues = []
    for stem in ("exportA", "exportB"):
        (orig / f"{stem}.json").write_text(json.dumps(
            {"frames": [{"name": "f.png", "url": "f.png", "labels": [lab]}], "config": {}}))
        issues.append(Issue("shape", stem, "f.png", "a", "solid_single_line", .9, "r", (0, 0),
                            {"op": "delete"}, False))
    out = tmp_path / "out"
    write_issues(out / "issues_shape.jsonl", issues)
    write_marks(out / "marks.json", {i.key: "real" for i in issues})
    monkeypatch.setattr(fixmod, "ORIGINALS_DIR", orig)
    monkeypatch.setattr(fixmod, "calibration_refusal", lambda *a: None)
    monkeypatch.setattr(scalabel_project, "PROJECTS_DIR", tmp_path / "projects")
    return out


def test_cli_fix_reports_failed_project_and_continues(two_stems, srv, capsys):
    srv.responses.append((400, b"bad item file: no frames"))
    rc = cli.main(["fix", "--type", "shape", "--version", "1", "--out", str(two_stems),
                   "--create-projects", "--server", srv.url])
    text = capsys.readouterr().out
    assert rc == 1
    assert srv.names == ["exportA_fix_shape_v1", "exportB_fix_shape_v1"]  # loop continued
    assert "exportA_fix_shape_v1: FAILED" in text and "bad item file: no frames" in text
    assert "project --round exportA_fix_shape_v1" in text  # how to retry
    assert "exportB_fix_shape_v1: created" in text


def test_cli_project_recreates_one_round(two_stems, srv, tmp_path, capsys):
    cli.main(["fix", "--type", "shape", "--version", "1", "--out", str(two_stems)])
    assert srv.names == []
    rc = cli.main(["project", "--round", "exportA_fix_shape_v1", "--out", str(two_stems),
                   "--server", srv.url])
    assert rc == 0 and srv.names == ["exportA_fix_shape_v1"]
    assert srv.files["item_file"] == (two_stems / "rounds" /
                                      "exportA_fix_shape_v1_items.json").read_bytes()
    (tmp_path / "projects" / "exportB_fix_shape_v1").mkdir(parents=True)
    rc = cli.main(["project", "--round", "exportB_fix_shape_v1", "--out", str(two_stems),
                   "--server", srv.url])
    assert rc == 0 and "already exists" in capsys.readouterr().out
    assert srv.names == ["exportA_fix_shape_v1"]  # not sent
    assert cli.main(["project", "--round", "nope_fix_x_v1", "--out", str(two_stems)]) == 2
