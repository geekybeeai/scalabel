"""Merge a reviewed Scalabel export back into a working copy."""

from __future__ import annotations

import copy
import json
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from annotation_qa import rounds
from annotation_qa.data import load_export, save_export
from annotation_qa.exclude import add_excluded
from annotation_qa.fix import working_copy
from annotation_qa.issues import frozen_calibration_keys, read_marks, write_marks
from annotation_qa.rounds import record_merge
from annotation_qa.reverts import round_reverts

CHANGED_MSG = ("work copy changed since the round was built \u2014 re-run the live-edit import "
               "after merge or rebuild")
_TAG_TYPE = re.compile(r"_fix_(?P<type>.+)_v\d+$")


@dataclass
class MergeResult:
    frames: int = 0
    reverted_applied: int = 0
    reverted_conflicts: int = 0
    unmatched_reverts: int = 0
    reverted_skipped_overlap: int = 0
    legacy: bool = False  # no base snapshot: the changed-frame check was skipped
    changed_allowed: list[str] = field(default_factory=list)  # --allow-changed-frames used
    excluded_added: int = 0  # I3: issue keys of changes rejected in review
    marks_dropped: int = 0  # I3: real marks of kept changes (re-detection needs new triage)
    suggest: bool = False  # a suggestion round: the user's edits were copied, nothing reverted
    backup: str = ""
    warnings: list[str] = field(default_factory=list)


class MergeRefused(ValueError):
    """The reviewed export does not belong to this round; nothing was written."""


def _url(frame: dict) -> str:
    return str(frame.get("url") or "").lstrip("/")


def _few(names) -> str:
    names = sorted(names)
    return ", ".join(names[:3]) + (f" (+{len(names) - 3} more)" if len(names) > 3 else "")


def _read_changes(out: Path, round_tag: str) -> dict | None:
    try:
        d = json.loads((Path(out) / "rounds" / f"{round_tag}_changes.json").read_text())
    except (OSError, ValueError):
        return None
    return rounds.split_changes(d)[0] if isinstance(d, dict) else None


def _is_suggest(out: Path, round_tag: str) -> bool:
    try:
        d = json.loads((Path(out) / "rounds" / f"{round_tag}_changes.json").read_text())
    except (OSError, ValueError):
        d = None
    base = rounds.read_base(out, round_tag) or {}
    return rounds.split_changes(d)[1] or base.get("mode") == "suggest"


def _overlapping_new(frame: dict, lid: str, restored: dict, known_ids: set[str]) -> str | None:
    """Id of a label drawn in review (id not in ``known_ids``) with the restored label's
    category that overlaps it (duplicates' overlap rule, either way >= PARTIAL), or None."""
    from annotation_qa.detectors.duplicates import PARTIAL, label_overlap  # lazy: scipy
    for lab in frame.get("labels") or []:
        oid = str(lab.get("id"))
        if oid == lid or oid in known_ids or lab.get("category") != restored.get("category"):
            continue
        if label_overlap(restored, lab) >= PARTIAL:
            return oid
    return None


def apply_reverts(frames: list[dict], old_labels: dict[str, list], out: Path, round_tag: str,
                  merged: set[str], proposed_ids: dict[str, set[str]] | None = None,
                  stats: dict | None = None) -> tuple[int, int]:
    """Undo the tool changes the user marked "Revert" in View B; return (applied, conflicts).

    Only labels the user left as the tool's AFTER are restored; a label edited or deleted in
    Scalabel keeps the user's version (warning). A label the tool deleted is re-added --
    unless a label drawn in review (id in neither the old nor the proposed frame,
    ``proposed_ids``) of the same category overlaps it: then the user redrew it and the
    re-add is skipped (warning). A join (``rec["join"]``) is undone only when neither label
    was touched in Scalabel: the deleted piece is re-added and the target gets its old
    geometry back.

    Raises MergeRefused when the round has reverts but its changes.json cannot be read.
    Revert entries with no matching change (unknown frame, frame not merged, unknown label,
    an added label) are counted in ``stats["unmatched"]`` with a warning; skipped re-adds in
    ``stats["skipped_overlap"]``.
    """
    from annotation_qa.viewb import _same, _snapshot  # lazy: viewb pulls in cv2 and rendering
    stats = stats if stats is not None else {}
    stats.setdefault("unmatched", 0)
    stats.setdefault("skipped_overlap", 0)
    reverts = round_reverts(out, round_tag)
    if not reverts:
        return 0, 0
    changes = _read_changes(out, round_tag)
    if changes is None:
        n = sum(len(v) for v in reverts.values() if isinstance(v, dict))
        raise MergeRefused(f"round {round_tag} has {n} View B revert(s) but its changes.json "
                           "cannot be read, so they cannot be applied")
    applied = conflicts = 0
    by_name = {f["name"]: f for f in frames}
    proposed_ids = proposed_ids or {}

    def unmatched(fname: str, lid: str, why: str) -> None:
        stats["unmatched"] += 1
        print(f"WARNING: revert {fname} / {lid} not applied: {why}")

    for fname, labs in reverts.items():
        frame = by_name.get(fname)
        if frame is None or fname not in merged:
            for lid in labs:
                unmatched(fname, lid, "frame is not in this round's merge")
            continue
        for lid in labs:
            rec = (changes.get(fname) or {}).get(lid)
            if not rec:
                unmatched(fname, lid, "no such change in the round")
                continue
            if rec.get("before") is None:
                unmatched(fname, lid, "an added label (nothing to restore)")
                continue
            before, after = rec["before"], rec.get("after")
            cur = next((l for l in frame["labels"] if str(l.get("id")) == lid), None)
            if rec.get("join"):
                j = rec["join"]
                tid = str(j.get("target"))
                tgt = next((l for l in frame["labels"] if str(l.get("id")) == tid), None)
                if cur is None and tgt is not None and _same(_snapshot(tgt), j.get("after")):
                    tmpl = next((l for l in old_labels.get(fname, [])
                                 if str(l.get("id")) == lid), None)
                    new = dict(tmpl) if tmpl else {"id": lid, "attributes": {}, "manualShape": True,
                                                   "box2d": None, "box3d": None}
                    new.update(category=before["category"], poly2d=before["poly2d"])
                    tgt["category"] = j["before"]["category"]
                    tgt["poly2d"] = j["before"]["poly2d"]
                    frame["labels"].append(new)
                    applied += 1
                elif (cur is not None and _same(_snapshot(cur), before) and tgt is not None
                      and _same(_snapshot(tgt), j.get("before"))):
                    applied += 1  # already the original
                else:
                    conflicts += 1
                    print(f"WARNING: {fname}: joined labels {lid} / {tid} were edited in "
                          "Scalabel; Scalabel edit wins over the revert")
                continue
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
                    known = {str(l.get("id")) for l in old_labels.get(fname, [])} \
                        | set(proposed_ids.get(fname, set()))
                    redrawn = _overlapping_new(frame, lid, before, known)
                    if redrawn is not None:
                        stats["skipped_overlap"] += 1
                        print(f"WARNING: {fname}: revert of deleted label {lid} skipped: label "
                              f"{redrawn} drawn in review overlaps it (same class)")
                        continue
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


def _kept_as_proposed(rec: dict, frame: dict | None, lid: str) -> bool:
    """Did the reviewed frame keep the tool's AFTER of this change unchanged?"""
    from annotation_qa.viewb import _same, _snapshot  # lazy: cv2
    labels = {str(l.get("id")): l for l in (frame or {}).get("labels") or []}
    cur = labels.get(lid)
    after = rec.get("after")
    if rec.get("join"):
        j = rec["join"]
        tgt = labels.get(str(j.get("target")))
        return cur is None and tgt is not None and _same(_snapshot(tgt), j.get("after"))
    if isinstance(after, list):  # a split: the first piece keeps the id
        return cur is not None and all(
            str(p.get("id")) in labels and _same(_snapshot(labels[str(p.get("id"))]), p)
            for p in after)
    if after is None:
        return cur is None
    return cur is not None and _same(_snapshot(cur), after)


def _review_outcome(out: Path, stem: str, tag: str, changes: dict, reviewed: dict[str, dict],
                    reverted: dict) -> tuple[int, int]:
    """I3: issue keys of changes the user CHANGED or REVERTED go to excluded.json ("rejected
    in review (<tag>)"); real marks of changes kept as proposed are dropped (unless the key is
    in a frozen calibration sample), so a re-detection after the merge needs new triage.
    Returns (excluded keys added, marks dropped)."""
    m = _TAG_TYPE.search(tag)
    tag_type = m["type"] if m else ""
    rejected: dict[str, dict] = {}
    kept: set[str] = set()
    for fname, labs in changes.items():
        if fname not in reviewed or not isinstance(labs, dict):
            continue
        for lid, rec in labs.items():
            if not isinstance(rec, dict):
                continue
            types = rec.get("issue_types") or [rec.get("issue_type") or tag_type]
            keys = [f"{stem}|{fname}|{lid}|{t}" for t in types if t]
            if lid in (reverted.get(fname) or {}) or not _kept_as_proposed(rec, reviewed[fname], lid):
                for k in keys:
                    rejected[k] = {"reason": f"rejected in review ({tag})", "round": tag}
            else:
                kept.update(keys)
    added = add_excluded(out, rejected) if rejected else 0
    marks_path = Path(out) / "marks.json"
    marks = read_marks(marks_path)
    protected = frozen_calibration_keys(out)
    drop = [k for k in kept if k in marks and k not in protected]
    if drop:
        for k in drop:
            marks.pop(k)
        write_marks(marks_path, marks)
    return added, len(drop)


def _user_acted(rec: dict, frame: dict | None) -> bool:
    """Suggestion round: did the user change the suggested label (or, for a join, either
    label) in the reviewed frame? Missing counts as changed."""
    from annotation_qa.viewb import _same, _snapshot  # lazy: cv2
    labels = {str(l.get("id")): l for l in (frame or {}).get("labels") or []}
    pairs = [(rec.get("_lid"), rec.get("before"))]
    j = rec.get("proposed_join") or rec.get("join")
    if j:
        pairs.append((str(j.get("target")), j.get("before")))
    return any(lid not in labels or not _same(_snapshot(labels[lid]), snap) for lid, snap in pairs)


def _suggest_outcome(out: Path, stem: str, tag: str, changes: dict,
                     reviewed: dict[str, dict]) -> int:
    """I3 for a suggestion round: every suggested issue goes to excluded.json -- "handled by
    user in suggestion round" when its label was changed, "user left as is (suggestion
    declined)" when not -- so it is not suggested again. Returns the keys added."""
    m = _TAG_TYPE.search(tag)
    tag_type = m["type"] if m else ""
    entries: dict[str, dict] = {}
    for fname, labs in changes.items():
        if fname not in reviewed or not isinstance(labs, dict):
            continue
        for lid, rec in labs.items():
            if not isinstance(rec, dict):
                continue
            acted = _user_acted({**rec, "_lid": str(lid)}, reviewed[fname])
            why = ("handled by user in suggestion round" if acted
                   else "user left as is (suggestion declined)")
            types = rec.get("issue_types") or [rec.get("issue_type") or tag_type]
            for t in types:
                if t:
                    entries[f"{stem}|{fname}|{lid}|{t}"] = {"reason": why, "round": tag}
    return add_excluded(out, entries) if entries else 0


def merge_reviewed(export_stem: str, reviewed_export: Path, out: Path, round_tag: str,
                   remerge: bool = False, allow_changed_frames=()) -> MergeResult:
    """Replace the labels of the round's frames in OUT/work/<stem>.json.

    The reviewed export must be the export of this round's review project: its
    frame names must be exactly the round's items-file frames, with the same
    urls (the Random and TMI batches share frame names). Only those frames are
    merged. Raises MergeRefused (nothing written) when it is not, and when:

    - the round is merged already (``remerge`` allows it);
    - another round of the same stem was merged after this round was built (merging would
      overwrite that merge with older labels);
    - a round frame's labels in the work copy differ from the round's base snapshot (a
      live-edit import, say), unless the frame is in ``allow_changed_frames``. A re-merge
      also accepts the labels its last merge wrote. A legacy round without a base snapshot
      skips this check (warning);
    - the round has View B reverts but its changes.json cannot be read.

    The work copy is backed up to OUT/backup/work/<stem>.<timestamp>.json before it is saved.
    """
    out = Path(out)
    res = MergeResult()

    def warn(msg: str) -> None:
        res.warnings.append(msg)
        print(f"WARNING: {msg}", flush=True)

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
    # order: merged already? another round of the stem merged after this one was built?
    markers = rounds.merge_markers(out)
    mine = [m for m in markers if (m.get("stem"), m.get("tag")) == (export_stem, round_tag)]
    if mine and not remerge:
        raise MergeRefused(f"round {round_tag} is already merged (at {mine[-1].get('timestamp')}); "
                           "pass --remerge to merge it again")
    base = rounds.read_base(out, round_tag)
    built = rounds.built_at(out, round_tag, base)
    if built is not None:
        later = [m for m in markers if m.get("stem") == export_stem
                 and m.get("tag") != round_tag
                 and (rounds.marker_time(m) or built) > built]
        if later:
            raise MergeRefused(
                f"round {round_tag} was built at {built.isoformat()}, but "
                f"{', '.join(sorted({str(m.get('tag')) for m in later}))} of {export_stem} was "
                "merged after that; merging would overwrite it with older labels. Archive this "
                "round (archive-round) and rebuild it from the current work copy")
    work_path = working_copy(export_stem, out)
    work = load_export(work_path)
    work_frames = {f["name"]: f for f in work.frames if f.get("name") in reviewed
                   and _url(f) == _url(reviewed[f["name"]])}
    allow = {str(a) for a in allow_changed_frames or ()}
    if base is None:
        res.legacy = True
        warn(f"{round_tag}: legacy round (no base snapshot): changed-frame check skipped")
    else:
        merged_sha = (mine[-1].get("result_sha1") or {}) if (mine and remerge) else {}
        changed = []
        for name, want in (base.get("frames") or {}).items():
            f = work_frames.get(name)
            if f is None:
                continue  # reported below as not in the working copy
            now = rounds.labels_sha1(f.get("labels") or [])
            if now != want and now != merged_sha.get(name):
                changed.append(name)
        unknown = sorted(allow - set(changed))
        if unknown:
            warn(f"--allow-changed-frames: not a changed frame of {round_tag}: {_few(unknown)}")
        blocked = sorted(set(changed) - allow)
        if blocked:
            raise MergeRefused(f"{len(blocked)} round frame(s) of {round_tag}: {CHANGED_MSG} "
                               f"({_few(blocked)}); --allow-changed-frames <names> merges "
                               "them anyway")
        res.changed_allowed = sorted(set(changed) & allow)
        for name in res.changed_allowed:
            warn(f"{name}: {CHANGED_MSG}; merged anyway (--allow-changed-frames)")
    n, merged = 0, []
    old_labels: dict[str, list] = {}
    for frame in work.frames:
        rf = reviewed.get(frame["name"])
        if rf is not None and _url(rf) == _url(frame):
            old_labels[frame["name"]] = frame.get("labels") or []
            frame["labels"] = copy.deepcopy(rf.get("labels") or [])  # reverts edit this copy
            merged.append(frame["name"])
            n += 1
    if n != len(reviewed):
        lost = set(reviewed) - set(merged)
        raise MergeRefused(f"{len(lost)} reviewed frame(s) are not in the working copy of "
                           f"{export_stem}: {_few(lost)}")
    suggest = _is_suggest(out, round_tag)
    res.suggest = suggest
    stats: dict = {}
    proposed = {k: {str(l.get("id")) for l in f.get("labels") or []} for k, f in items.items()}
    if suggest:  # nothing was edited by the tool: there is nothing to revert
        applied, conflicts, stats = 0, 0, {"unmatched": 0, "skipped_overlap": 0}
    else:
        applied, conflicts = apply_reverts(work.frames, old_labels, out, round_tag,
                                           set(merged), proposed, stats)
    res.frames, res.reverted_applied, res.reverted_conflicts = n, applied, conflicts
    res.unmatched_reverts = stats["unmatched"]
    res.reverted_skipped_overlap = stats["skipped_overlap"]
    if res.unmatched_reverts:
        warn(f"{round_tag}: {res.unmatched_reverts} revert(s) matched no change and were not applied")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = out / "backup" / "work" / f"{export_stem}.{ts}.json"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(work_path, backup)
    res.backup = str(backup)
    save_export(work, work_path)
    after = {f["name"]: f for f in work.frames if f["name"] in set(merged)}
    record_merge(out, export_stem, round_tag, Path(reviewed_export), merged,
                 {"reverted_applied": applied, "reverted_conflicts": conflicts,
                  "unmatched_reverts": res.unmatched_reverts,
                  "remerge": bool(mine), **({"mode": "suggest"} if suggest else {}), "legacy_round": res.legacy,
                  "changed_frames_allowed": res.changed_allowed, "backup": str(backup),
                  "result_sha1": {k: rounds.labels_sha1(f.get("labels") or [])
                                  for k, f in sorted(after.items())}})
    changes = _read_changes(out, round_tag)
    if changes is None:
        warn(f"{round_tag}: changes.json unreadable; review outcome not recorded "
             "(excluded.json / marks.json unchanged)")
    elif suggest:
        res.excluded_added = _suggest_outcome(out, export_stem, round_tag, changes, reviewed)
    else:
        res.excluded_added, res.marks_dropped = _review_outcome(
            out, export_stem, round_tag, changes, reviewed, round_reverts(out, round_tag))
    return res
