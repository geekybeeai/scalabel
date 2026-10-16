"""Merge a reviewed Scalabel export back into a working copy."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from annotation_qa.data import load_export, save_export
from annotation_qa.fix import working_copy


def merge_reviewed(export_stem: str, reviewed_export: Path, out: Path,
                   round_tag: str | None = None) -> int:
    """Replace labels of matching frames in OUT/work/<stem>.json; return frames merged."""
    work_path = working_copy(export_stem, out)
    work = load_export(work_path)
    reviewed = {f["name"]: f for f in load_export(reviewed_export).frames}
    n = 0
    for frame in work.frames:
        rf = reviewed.get(frame["name"])
        if rf is not None:
            frame["labels"] = rf.get("labels") or []
            n += 1
    save_export(work, work_path)
    if round_tag:
        _record_merge(Path(out), export_stem, round_tag, Path(reviewed_export), n)
    return n


def _record_merge(out: Path, stem: str, tag: str, reviewed: Path, n: int) -> None:
    """Append a marker to OUT/rounds/merged.json (atomic), so `final` knows what was merged."""
    path = out / "rounds" / "merged.json"
    entries = json.loads(path.read_text()) if path.exists() else []
    entries.append({"stem": stem, "tag": tag, "reviewed_path": str(reviewed),
                    "merged_frames": n,
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(entries, indent=1))
    os.replace(tmp, path)
