import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from annotation_qa.scalabel_project import create, parse_multipart


class _Srv:
    def __init__(self):
        outer = self
        self.fields, self.files, self.paths = {}, {}, []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.paths.append(self.path)
                outer.fields, outer.files = parse_multipart(self.headers["Content-Type"], body)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

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
    url = create(items, "x_fix_curb_soft_v1", server=srv.url)
    f = srv.fields
    assert srv.paths == ["/postProject"]
    assert f["auto_correct"] == "false" and f["label_type"] == "polyline2d"
    assert f["item_type"] == "image" and f["page_title"] == "2D Lane"
    assert f["task_size"] == "2" and f["demo_mode"] == "false"
    assert f["project_name"] == "x_fix_curb_soft_v1"
    assert f["instructions"] == "https://doc.scalabel.ai/instructions/segmentation.html"
    assert "item_file" in srv.files and "categories" in srv.files
    assert srv.files["item_file"] == items.read_bytes()
    cats = srv.files["categories"].decode()
    assert cats.splitlines()[0] == "- name: center_turn_lane" and len(cats.splitlines()) == 6
    assert url == f"{srv.url}/dashboard?project_name=x_fix_curb_soft_v1"
