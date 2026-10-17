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
from annotation_qa.exclude import read_excluded
from annotation_qa.geometry import ShiftNotRepresentable, _segments, change_anchor, partial_shift
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


def _guards(types: list[str], name: str, out: Path, version: int,
            by_stem: dict[str, list[Issue]], issues_by_type: dict[str, list[Issue]],
            marks: dict[str, str]) -> list[str]:
    reasons = []
    for t in types:  # every listed type must have passed its calibration
        cal = calibration_refusal(issues_by_type[t], marks, t, out)
        if cal:
            reasons.append(cal)
    merged = rounds.merged_tags(out)
    all_rounds = rounds.round_files(out)
    metas = {t: read_meta(out, t) for t in types}
    for stem in sorted(by_stem):
        tag = rounds.round_tag(stem, name, version)
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
        for t in types:
            if not any(i.issue_type == t for i in by_stem[stem]):
                continue
            stale = _staleness(out, t, stem, metas[t])
            if stale:
                reasons.append(stale)
    return reasons


def split_types(issue_type: str | list[str]) -> list[str]:
    """``"a,b"`` -> ["a", "b"] (order kept, duplicates and blanks dropped)."""
    parts = issue_type.split(",") if isinstance(issue_type, str) else list(issue_type)
    out: list[str] = []
    for p in (x.strip() for x in parts):
        if p and p not in out:
            out.append(p)
    if not out:
        raise ValueError("no issue type given")
    return out


_GEOMETRY_OPS = ("shift", "drop_vertices")


def apply(issue_type: str | list[str], out: Path, version: int, force: bool = False,
          name: str | None = None) -> FixResult:
    """Apply the real-marked fixes of one or more types to the working copies, one round per stem.

    ``issue_type`` may list several types ("curb_soft,edge_on_paint"): a combined round,
    tagged ``<stem>_fix_<name>_v<N>`` (``name`` defaults to the types joined by "+").
    One type keeps the "first fix per label (by confidence) only" rule. Several types are
    composed per label: set_category first, then the geometry fix; a delete wins over
    everything; two geometry fixes of different types keep the higher-confidence one.

    Refuses (FixRefused, nothing written) when a type's calibration did not PASS,
    a stem has an unmerged earlier round, the round tag exists already, or the
    issues were detected on a different version of the working copy. ``force``
    overrides every refusal (they are returned in ``FixResult.forced``).
    """
    out = Path(out)
    types = split_types(issue_type)
    name = name or "+".join(types)
    combined = len(types) > 1
    issues_by_type = {t: load_issues_tolerant(out / f"issues_{t}.jsonl") for t in types}
    marks = read_marks(out / "marks.json")
    real = [i for t in types for i in issues_by_type[t] if marks.get(i.key) == "real"]
    by_stem: dict[str, list[Issue]] = {}
    for i in real:
        if i.fix is not None or i.hand_fix:
            by_stem.setdefault(i.export_stem, []).append(i)

    res = FixResult()
    excluded = read_excluded(out)
    kept: dict[str, list[Issue]] = {}
    for stem, lst in by_stem.items():
        for i in lst:
            if i.key in excluded:
                msg = f"{i.key}: excluded: auto-fix rejected by verifier"
                res.skipped.append(msg)
                log.warning("skipped %s", msg)
            else:
                kept.setdefault(stem, []).append(i)
    by_stem = kept
    reasons = _guards(types, name, out, version, by_stem, issues_by_type, marks)
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
        groups: dict[tuple[str, str], list[Issue]] = {}
        for iss in sorted(stem_issues, key=lambda i: -i.confidence):
            if frames.get(iss.frame_name) is None:
                continue
            if iss.hand_fix or iss.fix is None:
                if iss.frame_name not in hand:
                    hand.append(iss.frame_name)
                continue
            groups.setdefault((iss.frame_name, iss.label_id), []).append(iss)

        def skip(iss: Issue, why: str) -> None:
            msg = f"{iss.key} ({iss.fix.get('op')}): {why}"
            res.skipped.append(msg)
            log.warning("skipped %s", msg)
            print(f"warning: skipped {msg}", flush=True)

        for (fname, lid), grp in groups.items():
            frame = frames[fname]
            labels = frame.get("labels") or []
            idx = next((k for k, l in enumerate(labels) if l["id"] == lid), None)
            if idx is None:
                for iss in grp:
                    skip(iss, "label not found")
                continue
            label = labels[idx]
            # one fix per type: the highest-confidence one that can be applied at all
            reps: list[Issue] = []
            for t in types:
                cands = [i for i in grp if i.issue_type == t]
                for k, iss in enumerate(cands):
                    try:
                        _apply_op(copy.deepcopy(label), iss.fix)
                    except ShiftNotRepresentable as e:  # goes to review as a draw-by-hand frame
                        skip(iss, str(e))
                        if fname not in hand:
                            hand.append(fname)
                        continue
                    except Exception as e:  # one bad issue must not abort the round
                        skip(iss, f"{type(e).__name__}: {e}")
                        continue
                    reps.append(iss)
                    for other in cands[k + 1:]:
                        skip(other, "another fix already applied to this label")
                    break
            if not reps:
                continue
            reps.sort(key=lambda i: -i.confidence)
            plan = reps
            if combined and len(reps) > 1:
                dels = [i for i in reps if i.fix["op"] == "delete"]
                if dels:
                    plan = dels[:1]
                    for i in reps:
                        if i is not plan[0]:
                            skip(i, f"label deleted by {plan[0].key}")
                else:
                    plan, geo = [], None
                    for i in reps:
                        if i.fix["op"] in _GEOMETRY_OPS:
                            if geo is not None:
                                skip(i, f"conflicting geometry fix (kept {geo.key})")
                                continue
                            geo = i
                        plan.append(i)
                    cats = [i for i in plan if i.fix["op"] == "set_category"]
                    for i in cats[1:]:
                        skip(i, f"conflicting category fix (kept {cats[0].key})")
                    plan = cats[:1] + [i for i in plan if i.fix["op"] in _GEOMETRY_OPS]
            elif not combined:
                plan = reps[:1]
            trial = copy.deepcopy(label)
            applied: list[Issue] = []
            keep_label = True
            for iss in plan:
                try:
                    keep_label = _apply_op(trial, iss.fix)
                except Exception as e:  # noqa: BLE001
                    skip(iss, f"{type(e).__name__}: {e}")
                    continue
                applied.append(iss)
            if not applied:
                continue
            if not keep_label:
                del labels[idx]
                after = None
            else:
                labels[idx] = trial
                after = trial
            snap = _snapshot(next(l for l in before[fname]["labels"] if l["id"] == lid))
            rec = {"before": snap, "after": _snapshot(after)}
            rec["anchor"] = change_anchor(rec["before"], rec["after"])
            rec["issue_types"] = [i.issue_type for i in applied]
            rec["ops"] = [i.fix["op"] for i in applied]
            if not combined:
                rec["issue_type"] = types[0]
            changes.setdefault(fname, {})[lid] = rec
            if fname not in changed:
                changed.append(fname)
        hand = [n for n in hand if n not in changed]
        if not changed and not hand:
            continue  # nothing to review: no round files for this stem
        tag = rounds.round_tag(stem, name, version)
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
