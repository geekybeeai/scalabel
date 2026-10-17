"""Free-text comments on issues: OUT/comments.json = {issue_key: {text, updated, ...}}."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

MAX_LEN = 4000


def comments_path(out: Path) -> Path:
    return Path(out) / "comments.json"


def read_comments(out: Path) -> dict[str, dict]:
    p = comments_path(out)
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def write_comments(out: Path, comments: dict[str, dict]) -> None:
    p = comments_path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(comments, indent=1, sort_keys=True))
    os.replace(tmp, p)


def entry(issue, text: str) -> dict:
    return {"text": text, "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "issue_type": issue.issue_type, "export_stem": issue.export_stem,
            "frame_name": issue.frame_name, "label_id": issue.label_id}


def format_text(comments: dict[str, dict]) -> str:
    """Plain text for the developer: type · frame · label · text, one comment per line."""
    rows = sorted(comments.values(), key=lambda c: (
        c.get("issue_type", ""), c.get("export_stem", ""), c.get("frame_name", ""),
        str(c.get("label_id", ""))))
    return "\n".join(
        f"{c.get('issue_type', '')} · {c.get('frame_name', '')} · {c.get('label_id', '')} · "
        + str(c.get("text", "")).replace("\n", "\n    ") for c in rows)
