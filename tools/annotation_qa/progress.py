"""View B review progress: OUT/viewb_progress.json = {round_tag: {frame_name: {reviewed, time}}}."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def progress_path(out: Path) -> Path:
    return Path(out) / "viewb_progress.json"


def read_progress(out: Path) -> dict:
    try:
        d = json.loads(progress_path(out).read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def write_progress(out: Path, data: dict) -> None:
    p = progress_path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    os.replace(tmp, p)


def set_reviewed(out: Path, tag: str, frame: str, reviewed: bool) -> bool:
    """Mark or unmark one frame (atomic write). Caller holds the lock."""
    data = read_progress(out)
    rnd = data.setdefault(tag, {})
    if reviewed:
        rnd[frame] = {"reviewed": True,
                      "time": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    else:
        rnd.pop(frame, None)
        if not rnd:
            data.pop(tag)
    write_progress(out, data)
    return reviewed


def is_reviewed(progress: dict, tag: str, frame: str) -> bool:
    rec = (progress.get(tag) or {}).get(frame)
    return isinstance(rec, dict) and bool(rec.get("reviewed"))
