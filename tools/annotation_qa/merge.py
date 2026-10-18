"""Merge a reviewed Scalabel export back into a working copy."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from annotation_qa.data import load_export, save_export
from annotation_qa.fix import working_copy
from annotation_qa.rounds import record_merge
from annotation_qa.reverts import round_reverts


class MergeRefused(ValueError):
    """The reviewed export does not belong to this round; nothing was written."""


def _url(frame: dict) -> str:
    return str(frame.get("url") or "").lstrip("/")


def _few(names) -> str:
    names = sorted(names)
    return ", ".join(names[:3]) + (f" (+{len(names) - 3} more)" if len(names) > 3 else "")


def apply_reverts(frames: list[dict], old_labels: dict[str, list], out: Path, round_tag: str,
                  merged: set[str]) -> tuple[int, int]:
    """Undo the tool changes the user marked "Revert" in View B; return (applied, conflicts).

    Only labels the user left as the tool's AFTER are restored; a label edited or deleted in
    Scalabel keeps the user's version (warning). A label the tool deleted is re-added.
    """
    from annotation_qa.viewb import _same, _snapshot  # lazy: viewb pulls in cv2 and rendering
    reverts = round_reverts(out, round_tag)
    if not reverts:
        return 0, 0
    try:
        changes = json.loads((Path(out) / "rounds" / f"{round_tag}_changes.json").read_text())
    except (OSError, ValueError):
        return 0, 0
    applied = conflicts = 0
    by_name = {f["name"]: f for f in frames}
    for fname, labs in reverts.items():
        frame = by_name.get(fname)
        if frame is None or fname not in merged:
            continue
        for lid in labs:
            rec = (changes.get(fname) or {}).get(lid)
            if not rec or rec.get("before") is None:
                continue  # unknown change, or an added label (nothing to restore)
            before, after = rec["before"], rec.get("after")
            cur = next((l for l in frame["labels"] if str(l.get("id")) == lid), None)
            if isinstance(after, list):  # a split: restore the original, drop the other pieces
                ids = [str(p.get("id")) for p in after[1:]]
                rest = [next((l for l in frame["labels"] if str(l.get("id")) == i), None)
                        for i in ids]
                if (cur is not None and _same(_snapshot(cur), after[0])
                        and all(r is not None and _same(_snapshot(r), p)
                                for r, p in zip(rest, after[1:]))):
                    cur["category"] = before["category"]
                    cur["poly2d"] = before["poly2d"]
                    frame["labels"] = [l for l in frame["labels"] if str(l.get("id")) not in ids]
                    applied += 1
                elif cur is not None and _same(_snapshot(cur), before) and not any(rest):
                    applied += 1  # already the original
                else:
                    conflicts += 1
                    print(f"WARNING: {fname}: split label {lid} was edited in Scalabel; "
                          "Scalabel edit wins over the revert")
                continue
            if after is None:
                if cur is None:
                    tmpl = next((l for l in old_labels.get(fname, []) if str(l.get("id")) == lid), None)
                    new = dict(tmpl) if tmpl else {"id": lid, "attributes": {}, "manualShape": True,
                                                   "box2d": None, "box3d": None}
                    new.update(category=before["category"], poly2d=before["poly2d"])
                    frame["labels"].append(new)
                    applied += 1
                else:
                    conflicts += 1
                    print(f"WARNING: {fname}: label {lid} was re-added in Scalabel; "
                          "Scalabel edit wins over the revert")
            elif cur is None:
                conflicts += 1
                print(f"WARNING: {fname}: label {lid} was deleted in Scalabel; left deleted "
                      "(the revert is not applied)")
            elif _same(_snapshot(cur), after):
                cur["category"] = before["category"]
                cur["poly2d"] = before["poly2d"]
                applied += 1
            elif _same(_snapshot(cur), before):
                applied += 1  # already the original
            else:
                conflicts += 1
                print(f"WARNING: {fname}: label {lid} was edited in Scalabel; "
                      "Scalabel edit wins over the revert")
    return applied, conflicts


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
    old_labels: dict[str, list] = {}
    for frame in work.frames:
        rf = reviewed.get(frame["name"])
        if rf is not None and _url(rf) == _url(frame):
            old_labels[frame["name"]] = frame.get("labels") or []
            frame["labels"] = rf.get("labels") or []
            merged.append(frame["name"])
            n += 1
    if n != len(reviewed):
        lost = set(reviewed) - set(merged)
        raise MergeRefused(f"{len(lost)} reviewed frame(s) are not in the working copy of "
                           f"{export_stem}: {_few(lost)}")
    applied, conflicts = apply_reverts(work.frames, old_labels, out, round_tag, set(merged))
    save_export(work, work_path)
    record_merge(out, export_stem, round_tag, Path(reviewed_export), merged,
                 {"reverted_applied": applied, "reverted_conflicts": conflicts})
    return n
