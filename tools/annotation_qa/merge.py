"""Merge a reviewed Scalabel export back into a working copy."""

from __future__ import annotations

from pathlib import Path

from annotation_qa.data import load_export, save_export
from annotation_qa.fix import working_copy


def merge_reviewed(export_stem: str, reviewed_export: Path, out: Path) -> int:
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
    return n
