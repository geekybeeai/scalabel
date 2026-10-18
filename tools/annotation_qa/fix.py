"""Apply user-accepted fixes to working copies and build review item files."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from annotation_qa import progress, reverts, rounds
from annotation_qa.data import ORIGINALS_DIR, load_export, save_export
from annotation_qa.exclude import read_excluded
from annotation_qa.geometry import (LINE_WIDTH, ShiftNotRepresentable, _segments, change_anchor,
                                      max_move, partial_shift, point_at_arc, split_poly)
from annotation_qa.issues import (Issue, calibration_status, file_sha1, format_calibration,
                                  load_issues_tolerant, read_marks, read_meta)

log = logging.getLogger(__name__)

# Detectors dropped by calibration this revision (R30 edge_position, R32 edge_on_paint,
# R35 curb_soft_split, R37 yellow_structure, R38 solid_dashed): scan and calibrate still
# work, but `fix` refuses to build a round from them. There is no override flag.
DISABLED_FOR_ROUNDS = frozenset({"edge_position", "edge_on_paint", "curb_soft_split",
                                 "yellow_structure", "solid_dashed"})
NAME_RE = re.compile(r"^[A-Za-z0-9_+-]+$")


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


_C_RUN = re.compile(r"C+")


def types_problem(types: str, n: int, closed: bool) -> str | None:
    """Why ``types`` is not a valid Scalabel types string for ``n`` vertices, or None.

    One letter per vertex, "L" first, only "L"/"C"; every "C" run is exactly 2 long (the two
    control points) and is followed by an "L" (its end point) -- or, for a closed poly, by
    the end of the string (the curve ends on vertex 0)."""
    if len(types) != n:
        return f"{n} vertices but {len(types)} types"
    if not types or set(types) - {"L", "C"} or types[0] != "L":
        return f"bad types {types!r}"
    for m in _C_RUN.finditer(types):
        if len(m.group()) != 2:
            return f"a C run of {len(m.group())} at vertex {m.start()} (must be 2)"
        if m.end() == n and not closed:
            return f"curve at vertex {m.start()} has no end point (open poly ends in C)"
    return None


def _set_poly(poly: dict, fix: dict) -> dict:
    """op "set_poly": replace the poly's geometry (vertices, types, closed; other keys kept)."""
    verts = [[float(x), float(y)] for x, y in fix["vertices"]]
    types = str(fix.get("types") or "L" * len(verts))
    closed = bool(fix.get("closed", poly.get("closed")))
    if len(types) != len(verts):
        raise ValueError(f"set_poly: {len(verts)} vertices but {len(types)} types")
    if len(verts) < (3 if closed else 2) or not np.isfinite(np.asarray(verts, float)).all():
        raise ValueError("set_poly: too few or non-finite vertices")
    why = types_problem(types, len(verts), closed)
    if why:
        raise ValueError(f"set_poly: {why}")
    out = dict(poly)
    out["vertices"], out["types"], out["closed"] = verts, types, closed
    return out


_ID_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def new_label_id(seed: str, taken: set[str]) -> str:
    """A 16-char [A-Za-z0-9] id like Scalabel's, derived from ``seed`` (so a rebuilt round
    gets the same ids) and not in ``taken``; it is added to ``taken``."""
    salt = 0
    while True:
        h = hashlib.sha256(f"{seed}|{salt}".encode()).digest()
        nid = "".join(_ID_CHARS[b % len(_ID_CHARS)] for b in h[:16])
        if nid not in taken:
            taken.add(nid)
            return nid
        salt += 1


def _split(label: dict, fix: dict, new_labels: list, taken: set[str]) -> None:
    """op "split": cut poly ``poly_index`` at ``cuts`` (arc px along its dense curve). The
    first piece stays in ``label`` (same id, attributes and other polys); every other piece
    becomes a new label (new unique id, same attributes) appended to ``new_labels``."""
    i = fix.get("poly_index", 0)
    cats = list(fix["categories"])
    pieces = split_poly(label["poly2d"][i], fix["cuts"])
    if len(cats) != len(pieces):
        raise ValueError(f"split: {len(pieces)} pieces but {len(cats)} categories")
    taken.add(str(label["id"]))
    for k, (piece, cat) in enumerate(zip(pieces[1:], cats[1:]), 1):
        new = copy.deepcopy(label)
        new["id"] = new_label_id(f"{label['id']}|{i}|{k}|{fix['cuts']}", taken)
        new["category"] = cat
        new["poly2d"] = [piece]
        new_labels.append(new)
    label["poly2d"][i] = pieces[0]
    label["category"] = cats[0]


def _apply_op(label: dict, fix: dict, new_labels: list | None = None,
              taken: set[str] | None = None) -> bool:
    """Mutate ``label``; return False when the label should be deleted. A "split" appends
    its extra pieces (new labels, ids not in ``taken``) to ``new_labels``."""
    op = fix["op"]
    if op == "delete":
        return False
    if op == "join_into":  # this label goes; its target's poly is set by _join_target
        if not fix.get("target"):
            raise ValueError("join_into: no target label")
        _set_poly({"vertices": [], "types": "", "closed": False}, fix)  # validate geometry
        return False
    if op == "split":
        _split(label, fix, new_labels if new_labels is not None else [],
               taken if taken is not None else set())
    elif op == "set_category":
        label["category"] = fix["to"]
    elif op == "shift":
        i = fix.get("poly_index", 0)
        label["poly2d"][i] = partial_shift(label["poly2d"][i], np.asarray(fix["offsets"], float))
    elif op == "drop_vertices":
        i = fix.get("poly_index", 0)
        label["poly2d"][i] = _drop_vertices(label["poly2d"][i], fix["indices"])
    elif op == "set_poly":
        i = fix.get("poly_index", 0)
        label["poly2d"][i] = _set_poly(label["poly2d"][i], fix)
    else:
        raise ValueError(f"unknown fix op: {op}")
    return True


def _join_target(labels: list[dict], fix: dict) -> tuple[int, dict]:
    """op "join_into": (index, updated copy) of the target label, its poly ``poly_index``
    set to the fix's geometry. Raises ValueError when it cannot be applied."""
    tid = str(fix["target"])
    k = next((j for j, l in enumerate(labels) if str(l["id"]) == tid), None)
    if k is None:
        raise ValueError(f"join target {tid} not found")
    tgt = copy.deepcopy(labels[k])
    i = fix.get("poly_index", 0)
    if not 0 <= i < len(tgt.get("poly2d") or []):
        raise ValueError(f"join target {tid} has no poly {i}")
    tgt["poly2d"][i] = _set_poly(tgt["poly2d"][i], fix)
    return k, tgt


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


ROUND_SIDE_FILES = ("items", "changes", "verify", "base")


@dataclass
class _Refusals:
    hard: list[str] = field(default_factory=list)  # no flag overrides these
    stale: list[str] = field(default_factory=list)  # --force-stale
    calibration: list[str] = field(default_factory=list)  # --force-uncalibrated


def _guards(types: list[str], name: str, out: Path, version: int,
            by_stem: dict[str, list[Issue]], issues_by_type: dict[str, list[Issue]],
            marks: dict[str, str]) -> _Refusals:
    ref = _Refusals()
    for t in types:  # every listed type must have passed its calibration
        cal = calibration_refusal(issues_by_type[t], marks, t, out)
        if cal:
            ref.calibration.append(cal)
    merged = rounds.merged_tags(out)
    all_rounds = rounds.round_files(out)
    metas = {t: read_meta(out, t) for t in types}
    rev, prog = reverts.read_reverts(out), progress.read_progress(out)
    for stem in sorted(by_stem):
        tag = rounds.round_tag(stem, name, version)
        open_ = [r["tag"] for r in all_rounds
                 if r["stem"] == stem and r["tag"] != tag and (stem, r["tag"]) not in merged]
        if open_:
            ref.hard.append(f"{stem}: round(s) not merged yet: {', '.join(open_)}; review and "
                            f"merge them first (merge --stem {stem} --reviewed <export> --round "
                            "<tag>), or set one aside with archive-round --round <tag>")
        exists = [p.name for p in [out / "rounds" / f"{tag}_{k}.json" for k in ROUND_SIDE_FILES]
                  + [out / "work" / f"{tag}.json"] if p.exists()]
        exists += [f"{f}[{tag}]" for f, d in (("reverts.json", rev),
                                              ("viewb_progress.json", prog)) if tag in d]
        if exists:
            ref.hard.append(f"{stem}: round {tag} already exists ({', '.join(exists)}); use the "
                            f"next --version, or archive-round --round {tag} to rebuild it")
        elif (stem, tag) in merged:
            ref.hard.append(f"{stem}: round {tag} was merged before (merge marker in "
                            "rounds/merged.json); use the next --version")
        for t in types:
            if not any(i.issue_type == t for i in by_stem[stem]):
                continue
            stale = _staleness(out, t, stem, metas[t])
            if stale:
                ref.stale.append(stale)
    return ref


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


_GEOMETRY_OPS = ("shift", "drop_vertices", "set_poly")


def apply(issue_type: str | list[str], out: Path, version: int, force_stale: bool = False,
          force_uncalibrated: bool = False, name: str | None = None) -> FixResult:
    """Apply the real-marked fixes of one or more types to the working copies, one round per stem.

    ``issue_type`` may list several types ("curb_soft,edge_on_paint"): a combined round,
    tagged ``<stem>_fix_<name>_v<N>`` (``name`` defaults to the types joined by "+").
    One type keeps the "first fix per label (by confidence) only" rule. Several types are
    composed per label: set_category first, then the geometry fix; a delete wins over
    everything; two geometry fixes of different types keep the higher-confidence one.

    A ``join_into`` (duplicates overlapping pieces) changes two labels at once: the issue
    label is deleted and the target's poly is replaced, both or neither. It counts as a
    delete of the issue label, and is skipped when the target is also changed by another
    fix of the round (or joined with another piece already); changes.json records it under
    the issue label as {"before": A, "after": null, "join": {"target", "poly_index",
    "before": B, "after": B'}}.

    Refuses (FixRefused, nothing written) when a type is in DISABLED_FOR_ROUNDS, ``name``
    is not [A-Za-z0-9_+-]+, a stem has an unmerged earlier round, the round tag exists
    already (any of its files or review state) or was merged before -- these always --, when
    the issues were detected on a different version of the working copy (``force_stale``
    overrides), or when a type's calibration did not PASS (``force_uncalibrated``
    overrides). Overridden refusals are returned in ``FixResult.forced``.

    Each round also gets ``rounds/<tag>_base.json``: when it was built and the sha1 of each
    round frame's labels in the working copy, so ``merge`` can tell when they changed.
    """
    out = Path(out)
    types = split_types(issue_type)
    off = [t for t in types if t in DISABLED_FOR_ROUNDS]
    if off:
        raise FixRefused([f"{t}: dropped by calibration this revision; no fix rounds are built "
                          f"from it (scan and calibrate still work)" for t in off])
    name = name or "+".join(types)
    if not NAME_RE.match(name):
        raise FixRefused([f"--name {name!r} must match [A-Za-z0-9_+-]+"])
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
                why = (excluded[i.key] or {}).get("reason") or "auto-fix rejected by verifier"
                msg = f"{i.key}: excluded: {why}"
                res.skipped.append(msg)
                log.warning("skipped %s", msg)
            else:
                kept.setdefault(stem, []).append(i)
    by_stem = kept
    ref = _guards(types, name, out, version, by_stem, issues_by_type, marks)
    blocking = ref.hard + ([] if force_stale else ref.stale) \
        + ([] if force_uncalibrated else ref.calibration)
    if blocking:
        raise FixRefused(blocking)
    res.forced = ref.stale + ref.calibration
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

        touched: dict[str, set[str]] = {}  # labels with a fix of their own, per frame
        for fn, ln in groups:
            touched.setdefault(fn, set()).add(str(ln))
        claimed: dict[str, set[str]] = {}  # join targets already taken, per frame

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
                    if iss.fix["op"] == "join_into":
                        tid = str(iss.fix.get("target"))
                        why = None
                        if tid in touched.get(fname, set()) - {str(lid)}:
                            why = f"join target {tid} is also changed by another fix in this round"
                        elif tid in claimed.get(fname, set()):
                            why = f"join target {tid} is already joined with another piece"
                        else:
                            try:
                                _join_target(labels, iss.fix)
                            except Exception as e:  # noqa: BLE001
                                why = f"{type(e).__name__}: {e}"
                        if why:
                            skip(iss, why)
                            continue
                        claimed.setdefault(fname, set()).add(tid)
                    try:
                        trial = copy.deepcopy(label)
                        _apply_op(trial, iss.fix)
                        if iss.fix["op"] == "shift":
                            pi = iss.fix.get("poly_index", 0)
                            if max_move(label["poly2d"][pi], trial["poly2d"][pi]) <= LINE_WIDTH:
                                skip(iss, "move \u2264 4 px (within line width) \u2014 no change needed")
                                continue
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
                dels = [i for i in reps if i.fix["op"] in ("delete", "join_into")]
                splits = [i for i in reps if i.fix["op"] == "split"]
                if dels or splits:  # a delete wins, then a split (it sets the classes)
                    plan = (dels or splits)[:1]
                    verb = "deleted" if dels else "split"
                    for i in reps:
                        if i is not plan[0]:
                            skip(i, f"label {verb} by {plan[0].key}")
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
            pieces: list[dict] = []
            taken = {str(l["id"]) for l in labels}
            for iss in plan:
                try:
                    keep_label = _apply_op(trial, iss.fix, pieces, taken)
                except Exception as e:  # noqa: BLE001
                    skip(iss, f"{type(e).__name__}: {e}")
                    continue
                applied.append(iss)
            if not applied:
                continue
            join = next((i for i in applied if i.fix["op"] == "join_into"), None)
            jrec = None
            if join is not None:  # both labels or neither
                try:
                    tk, tgt = _join_target(labels, join.fix)
                except Exception as e:  # noqa: BLE001
                    skip(join, f"{type(e).__name__}: {e}")
                    continue
                tid = str(tgt["id"])
                bsnap = _snapshot(next(l for l in before[fname]["labels"] if str(l["id"]) == tid))
                labels[tk] = tgt
                jf = join.features or {}
                jrec = {"target": tid, "poly_index": join.fix.get("poly_index", 0),
                        "before": bsnap, "after": _snapshot(tgt), "mode": jf.get("join"),
                        "splices": jf.get("join_splices") or [], "gaps": jf.get("join_gaps") or []}
            if not keep_label:
                del labels[idx]
                after = None
            else:
                labels[idx] = trial
                labels[idx + 1:idx + 1] = pieces  # split pieces follow the original
                after = trial
            snap = _snapshot(next(l for l in before[fname]["labels"] if l["id"] == lid))
            if pieces:  # a split: "after" is the list of piece labels, the first keeps the id
                rec = {"before": snap,
                       "after": [{"id": l["id"], **_snapshot(l)} for l in [trial] + pieces]}
                sp = next(i.fix for i in applied if i.fix["op"] == "split")
                bpoly = snap["poly2d"][sp.get("poly_index", 0)]
                rec["anchor"] = [int(round(v)) for v in point_at_arc(bpoly, sp["cuts"][0])]
                rec["cut_points"] = [[round(v, 2) for v in point_at_arc(bpoly, c)]
                                     for c in sp["cuts"]]
            elif jrec is not None:
                rec = {"before": snap, "after": None, "join": jrec}
                sp = jrec["splices"]
                rec["anchor"] = ([int(round(v)) for v in sp[0]] if sp
                                 else change_anchor(snap, None))
            else:
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
        save_export(exp, out / "work" / f"{tag}.json")
        keep = set(changed) | set(hand)
        rounds.write_base(out, stem, tag, {n: before[n].get("labels") or [] for n in keep})
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
