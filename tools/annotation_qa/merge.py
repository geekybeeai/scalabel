"""Merge a reviewed Scalabel export back into a working copy."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from annotation_qa.data import load_export, save_export
from annotation_qa.fix import working_copy
from annotation_qa.rounds import record_merge


class MergeRefused(ValueError):
    """The reviewed export does not belong to this round; nothing was written."""


def _url(frame: dict) -> str:
    return str(frame.get("url") or "").lstrip("/")


def _few(names) -> str:
    names = sorted(names)
    return ", ".join(names[:3]) + (f" (+{len(names) - 3} more)" if len(names) > 3 else "")


def merge_reviewed(export_stem: str, reviewed_export: Path, out: Path, round_tag: str) -> int:
    """Replace the labels of the round's frames in OUT/work/<stem>.json; return frames merged.

    The reviewed export must be the export of this round's review project: its
    frame names must be exactly the round's items-file frames, with the same
    urls (the Random and TMI batches share frame names). Only those frames are
    merged. Raises MergeRefused otherwise.
    """
    out = Path(out)
    if not round_tag or not round_tag.startswith(f"{export_stem}_fix_"):
        raise MergeRefused(f"round tag {round_tag!r} is not a round of {export_stem} "
                           f"(expected {export_stem}_fix_<type>_v<N>)")
    items_path = out / "rounds" / f"{round_tag}_items.json"
    if not items_path.exists():
        raise MergeRefused(f"no items file for round {round_tag}: {items_path}")
    items = {f["name"]: f for f in load_export(items_path).frames}
    rev_frames = load_export(reviewed_export).frames
    dup = [n for n, c in Counter(f.get("name") for f in rev_frames).items() if c > 1]
    if dup:
        raise MergeRefused(f"reviewed export repeats frame names: {_few(dup)}")
    reviewed = {f["name"]: f for f in rev_frames}
    if not reviewed:
        raise MergeRefused(f"reviewed export {reviewed_export} has no frames")
    missing, extra = set(items) - set(reviewed), set(reviewed) - set(items)
    if missing or extra:
        raise MergeRefused(
            f"reviewed export frames do not match round {round_tag}: "
            f"{len(missing)} round frame(s) missing ({_few(missing)}), "
            f"{len(extra)} unexpected ({_few(extra)}); is this the export of project {round_tag}?")
    bad = [n for n in reviewed if _url(reviewed[n]) != _url(items[n])]
    if bad:
        raise MergeRefused(f"frame urls differ from round {round_tag} for {_few(bad)}; "
                           "is this the export of the right batch?")
    work_path = working_copy(export_stem, out)
    work = load_export(work_path)
    n, merged = 0, []
    for frame in work.frames:
        rf = reviewed.get(frame["name"])
        if rf is not None and _url(rf) == _url(frame):
            frame["labels"] = rf.get("labels") or []
            merged.append(frame["name"])
            n += 1
    if n != len(reviewed):
        lost = set(reviewed) - set(merged)
        raise MergeRefused(f"{len(lost)} reviewed frame(s) are not in the working copy of "
                           f"{export_stem}: {_few(lost)}")
    save_export(work, work_path)
    record_merge(out, export_stem, round_tag, Path(reviewed_export), merged)
    return n
