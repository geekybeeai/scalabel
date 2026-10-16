"""Apply user-accepted fixes to working copies and build review item files."""

from __future__ import annotations

import copy
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from annotation_qa.data import ORIGINALS_DIR, load_export, save_export
from annotation_qa.geometry import shift_poly
from annotation_qa.issues import Issue, read_issues, read_marks


@dataclass
class FixResult:
    changed: dict[str, list[str]] = field(default_factory=dict)
    hand: dict[str, list[str]] = field(default_factory=dict)


def working_copy(export_stem: str, out: Path) -> Path:
    """OUT/work/<stem>.json, copied from the original on first use."""
    work = Path(out) / "work" / f"{export_stem}.json"
    if not work.exists():
        work.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ORIGINALS_DIR / f"{export_stem}.json", work)
    return work


def _drop_vertices(poly: dict, indices: list[int]) -> dict:
    drop = set(indices)
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
        for iss in stem_issues:
            frame = frames.get(iss.frame_name)
            if frame is None:
                continue
            if iss.hand_fix or iss.fix is None:
                if iss.frame_name not in hand:
                    hand.append(iss.frame_name)
                continue
            labels = frame.get("labels") or []
            idx = next((k for k, l in enumerate(labels) if l["id"] == iss.label_id), None)
            if idx is None:
                continue
            if not _apply_op(labels[idx], iss.fix):
                del labels[idx]
                after = None
            else:
                after = labels[idx]
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
