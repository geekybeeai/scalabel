"""Apply user-accepted fixes to working copies and build review item files."""

from __future__ import annotations

import copy
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from annotation_qa import rounds
from annotation_qa.data import ORIGINALS_DIR, load_export, save_export
from annotation_qa.geometry import ShiftNotRepresentable, _segments, partial_shift
from annotation_qa.issues import (Issue, calibration_status, file_sha1, format_calibration,
                                  load_issues_tolerant, read_marks, read_meta)

log = logging.getLogger(__name__)


@dataclass
class FixResult:
    changed: dict[str, list[str]] = field(default_factory=dict)
    hand: dict[str, list[str]] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)  # issues not applied, with the reason
    forced: list[str] = field(default_factory=list)  # guard refusals overridden by force
    tags: list[str] = field(default_factory=list)  # round tags written


class FixRefused(Exception):
    """apply() would be unsafe; nothing was written. ``reasons`` says why."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def working_copy(export_stem: str, out: Path) -> Path:
    """OUT/work/<stem>.json, copied from the original on first use."""
    work = Path(out) / "work" / f"{export_stem}.json"
    if not work.exists():
        work.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ORIGINALS_DIR / f"{export_stem}.json", work)
    return work


def droppable_vertices(poly: dict) -> set[int]:
    """Raw vertex indices that are plain line points: not a Bezier start, control or end."""
    n = len(poly["vertices"])
    types = poly.get("types") or "L" * n
    in_curve: set[int] = set()
    for kind, idx in _segments(poly):
        if kind == "C":
            in_curve.update(idx)
    return {i for i in range(n) if types[i] == "L" and i not in in_curve}


def _drop_vertices(poly: dict, indices: list[int]) -> dict:
    n = len(poly["vertices"])
    drop = set(indices)
    bad = sorted(i for i in drop if not 0 <= i < n)
    if bad:
        raise ValueError(f"drop_vertices index out of range: {bad}")
    bad = sorted(drop - droppable_vertices(poly))
    if bad:
        raise ValueError(f"drop_vertices indices {bad} are not plain L vertices "
                         "(Bezier start/control/end points cannot be dropped)")
    if n - len(drop) < (3 if poly.get("closed") else 2):
        raise ValueError("drop_vertices would leave fewer than 2 vertices")
    keep = [i for i in range(len(poly["vertices"])) if i not in drop]
    out = dict(poly)
    out["vertices"] = [poly["vertices"][i] for i in keep]
    out["types"] = "".join(poly["types"][i] for i in keep)
    return out


def _apply_op(label: dict, fix: dict) -> bool:
    """Mutate ``label``; return False when the label should be deleted."""
    op = fix["op"]
    if op == "delete":
        return False
    if op == "set_category":
        label["category"] = fix["to"]
    elif op == "shift":
        i = fix.get("poly_index", 0)
        label["poly2d"][i] = partial_shift(label["poly2d"][i], np.asarray(fix["offsets"], float))
    elif op == "drop_vertices":
        i = fix.get("poly_index", 0)
        label["poly2d"][i] = _drop_vertices(label["poly2d"][i], fix["indices"])
    else:
        raise ValueError(f"unknown fix op: {op}")
    return True


def _snapshot(label: dict | None) -> dict | None:
    if label is None:
        return None
    return {"category": label["category"], "poly2d": copy.deepcopy(label.get("poly2d"))}


def calibration_refusal(issues: list[Issue], marks: dict[str, str], issue_type: str,
                        out: Path) -> str | None:
    """None when the type's calibration sample PASSes, else the numbers (I10)."""
    st = calibration_status(out, issue_type, issues, marks)
    if st["status"] == "PASS":
        return None
    return (format_calibration(issue_type, st)
            + f"; answer the sample at /calibrate?type={issue_type} first")


def _scanned_path(out: Path, stem: str) -> Path:
    work = Path(out) / "work" / f"{stem}.json"
    return work if work.exists() else ORIGINALS_DIR / f"{stem}.json"


def _staleness(out: Path, issue_type: str, stem: str, meta: dict | None) -> str | None:
    """Why the issues of ``stem`` may point at stale labels, or None."""
    cur = _scanned_path(out, stem)
    if not cur.exists():
        return None  # working_copy() reports the missing original
    rescan = (f"re-scan first: python3 -m annotation_qa scan --since-merge "
              f"--types {issue_type} --exports <originals>")
    if meta is None:
        # no metadata (a scan from before it was recorded): that scan read the
        # original, so the issues are current only while the work copy still equals it
        orig = ORIGINALS_DIR / f"{stem}.json"
        if cur != orig and orig.exists() and file_sha1(cur) != file_sha1(orig):
            return (f"{stem}: no scan metadata for {issue_type} and the working copy has "
                    f"changed since the original was scanned; {rescan}")
        return None
    rec = meta.get(stem)
    if rec is None:
        return f"{stem}: not part of the last {issue_type} scan; {rescan}"
    if rec.get("sha1") != file_sha1(cur):
        return (f"{stem}: the working copy changed since the last {issue_type} scan "
                f"(a merged round?); {rescan}")
    return None


def _guards(issue_type: str, out: Path, version: int, stems: list[str],
            issues: list[Issue], marks: dict[str, str]) -> list[str]:
    reasons = []
    cal = calibration_refusal(issues, marks, issue_type, out)
    if cal:
        reasons.append(cal)
    merged = rounds.merged_tags(out)
    all_rounds = rounds.round_files(out)
    meta = read_meta(out, issue_type)
    for stem in stems:
        tag = rounds.round_tag(stem, issue_type, version)
        open_ = [r["tag"] for r in all_rounds
                 if r["stem"] == stem and r["tag"] != tag and (stem, r["tag"]) not in merged]
        if open_:
            reasons.append(f"{stem}: round(s) not merged yet: {', '.join(open_)}; review and "
                           f"merge them first (merge --stem {stem} --reviewed <export> --round <tag>)")
        exists = [p.name for p in (out / "rounds" / f"{tag}_items.json",
                                   out / "rounds" / f"{tag}_changes.json",
                                   out / "work" / f"{tag}.json") if p.exists()]
        if exists:
            reasons.append(f"{stem}: round {tag} already exists ({', '.join(exists)}); "
                           "use the next --version")
        stale = _staleness(out, issue_type, stem, meta)
        if stale:
            reasons.append(stale)
    return reasons


def apply(issue_type: str, out: Path, version: int, force: bool = False) -> FixResult:
    """Apply the real-marked fixes of one type to the working copies, one round per stem.

    Refuses (FixRefused, nothing written) when the type's calibration did not PASS,
    a stem has an unmerged earlier round, the round tag exists already, or the
    issues were detected on a different version of the working copy. ``force``
    overrides every refusal (they are returned in ``FixResult.forced``).
    """
    out = Path(out)
    issues = load_issues_tolerant(out / f"issues_{issue_type}.jsonl")
    marks = read_marks(out / "marks.json")
    real = [i for i in issues if marks.get(i.key) == "real"]
    by_stem: dict[str, list[Issue]] = {}
    for i in real:
        if i.fix is not None or i.hand_fix:
            by_stem.setdefault(i.export_stem, []).append(i)

    res = FixResult()
    reasons = _guards(issue_type, out, version, sorted(by_stem), issues, marks)
    if reasons and not force:
        raise FixRefused(reasons)
    res.forced = reasons
    for stem, stem_issues in sorted(by_stem.items()):
        exp = load_export(working_copy(stem, out))
        frames = {f["name"]: f for f in exp.frames}
        before = {n: copy.deepcopy(f) for n, f in frames.items()}
        changes: dict[str, dict[str, dict]] = {}
        changed: list[str] = []
        hand: list[str] = []
        done_labels: set[tuple[str, str]] = set()
        for iss in sorted(stem_issues, key=lambda i: -i.confidence):
            frame = frames.get(iss.frame_name)
            if frame is None:
                continue
            if iss.hand_fix or iss.fix is None:
                if iss.frame_name not in hand:
                    hand.append(iss.frame_name)
                continue
            where = f"{iss.key} ({iss.fix.get('op')})"

            def skip(why: str) -> None:
                msg = f"{where}: {why}"
                res.skipped.append(msg)
                log.warning("skipped %s", msg)
                print(f"warning: skipped {msg}", flush=True)

            labels = frame.get("labels") or []
            idx = next((k for k, l in enumerate(labels) if l["id"] == iss.label_id), None)
            if idx is None:
                skip("label not found")
                continue
            if (iss.frame_name, iss.label_id) in done_labels:
                skip("another fix already applied to this label")
                continue
            label = labels[idx]
            try:
                trial = copy.deepcopy(label)
                keep_label = _apply_op(trial, iss.fix)
            except ShiftNotRepresentable as e:  # goes to review as a draw-by-hand frame
                skip(str(e))
                if iss.frame_name not in hand:
                    hand.append(iss.frame_name)
                continue
            except Exception as e:  # one bad issue must not abort the round
                skip(f"{type(e).__name__}: {e}")
                continue
            done_labels.add((iss.frame_name, iss.label_id))
            if not keep_label:
                del labels[idx]
                after = None
            else:
                labels[idx] = trial
                after = trial
            rec = changes.setdefault(iss.frame_name, {}).setdefault(
                iss.label_id, {"before": _snapshot(
                    next(l for l in before[iss.frame_name]["labels"] if l["id"] == iss.label_id))})
            rec["after"] = _snapshot(after)
            if iss.frame_name not in changed:
                changed.append(iss.frame_name)
        hand = [n for n in hand if n not in changed]
        if not changed and not hand:
            continue  # nothing to review: no round files for this stem
        tag = rounds.round_tag(stem, issue_type, version)
        if rounds.drop_markers(out, stem, tag):  # a forced rebuild needs a fresh merge
            print(f"warning: {tag} rebuilt; its old merge marker was removed", flush=True)
        save_export(exp, out / "work" / f"{tag}.json")
        keep = set(changed) | set(hand)
        subset = type(exp)(exp.path, [f for f in exp.frames if f["name"] in keep], exp.config)
        save_export(subset, out / "rounds" / f"{tag}_items.json")
        chg = out / "rounds" / f"{tag}_changes.json"
        tmp = chg.with_name(chg.name + ".tmp")
        tmp.write_text(json.dumps(changes))
        tmp.replace(chg)
        res.tags.append(tag)
        if changed:
            res.changed[stem] = changed
        if hand:
            res.hand[stem] = hand
    return res
