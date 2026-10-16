"""Create a Scalabel review project by POSTing the same multipart form the /create page sends."""

from __future__ import annotations

import json
import uuid
from email import message_from_bytes
from pathlib import Path
from urllib import request

CATEGORIES = [
    "center_turn_lane", "solid_single_line", "curb_road_edge",
    "dashed_white_line", "double_yellow_line", "without_curb_road_edge",
]
INSTRUCTIONS = "https://doc.scalabel.ai/instructions/segmentation.html"


def _multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes]]) -> tuple[bytes, str]:
    boundary = "----annotationqa" + uuid.uuid4().hex
    parts: list[bytes] = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (fname, data) in files.items():
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; '
                      f'filename="{fname}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
                     + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], dict[str, bytes]]:
    """Split a multipart body into (text fields, file contents); used by tests."""
    msg = message_from_bytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body)
    fields: dict[str, str] = {}
    files: dict[str, bytes] = {}
    for part in msg.get_payload():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True) or b""
        if part.get_filename():
            files[name] = payload
        else:
            fields[name] = payload.decode()
    return fields, files


def create(items_file: Path, project_name: str, server: str = "http://localhost:8686") -> str:
    """POST /postProject with an image + polyline2d project; return the dashboard URL."""
    items_file = Path(items_file)
    data = items_file.read_bytes()
    n_frames = len(json.loads(data).get("frames", []))
    fields = {
        "project_name": project_name,
        "item_type": "image",
        "label_type": "polyline2d",
        "page_title": "2D Lane",
        "task_size": str(n_frames),
        "instructions": INSTRUCTIONS,
        "demo_mode": "false",
        "auto_correct": "false",
    }
    cats = "".join(f"- name: {c}\n" for c in CATEGORIES).encode()
    body, ctype = _multipart(fields, {
        "item_file": (items_file.name, data),
        "categories": ("categories.yml", cats),
    })
    req = request.Request(server.rstrip("/") + "/postProject", data=body,
                          headers={"Content-Type": ctype}, method="POST")
    with request.urlopen(req, timeout=300) as resp:
        resp.read()
    return f"{server.rstrip('/')}/dashboard?project_name={project_name}"
