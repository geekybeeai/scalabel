"""Apply user-accepted fixes to working copies and build review item files."""

from __future__ import annotations

import copy
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from annotation_qa.data import ORIGINALS_DIR, load_export, save_export
from annotation_qa.geometry import _segments, shift_poly
from annotation_qa.issues import Issue, read_issues, read_marks

log = logging.getLogger(__name__)


@dataclass
class FixResult:
    changed: dict[str, list[str]] = field(default_factory=dict)
    hand: dict[str, list[str]] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)  # issues not applied, with the reason


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
        label["poly2d"][i] = shift_poly(label["poly2d"][i], np.asarray(fix["offsets"], float))
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


def apply(issue_type: str, out: Path, version: int) -> FixResult:
    out = Path(out)
    issues = read_issues(out / f"issues_{issue_type}.jsonl")
    marks = read_marks(out / "marks.json")
    real = [i for i in issues if marks.get(i.key) == "real"]
    by_stem: dict[str, list[Issue]] = {}
    for i in real:
        if i.fix is not None or i.hand_fix:
            by_stem.setdefault(i.export_stem, []).append(i)

    res = FixResult()
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
        tag = f"{stem}_fix_{issue_type}_v{version}"
        save_export(exp, out / "work" / f"{tag}.json")
        keep = set(changed) | set(hand)
        subset = type(exp)(exp.path, [f for f in exp.frames if f["name"] in keep], exp.config)
        save_export(subset, out / "rounds" / f"{tag}_items.json")
        with open(out / "rounds" / f"{tag}_changes.json", "w") as f:
            json.dump(changes, f)
        if changed:
            res.changed[stem] = changed
        if hand:
            res.hand[stem] = hand
    return res
