"""Calibration-bar report and the final deliverable copy."""

from __future__ import annotations

import json
from pathlib import Path

from annotation_qa import data
from annotation_qa.issues import read_marks
from annotation_qa.review_server import load_issues_tolerant
from annotation_qa.viewb import merged_tags, round_files

REVIEWER_FRAMES = ["208199", "207851", "MLC 1", "100865_201054",
                   "565_verification", "860_verification", "105193_204737", "100292_200553"]


def bar_report(out: Path) -> dict[str, dict]:
    """Per reviewer-frame substring: issue counts by type and how many are marked real."""
    out = Path(out)
    marks = read_marks(out / "marks.json")
    res = {s: {"types": {}, "real": 0} for s in REVIEWER_FRAMES}
    for p in sorted(out.glob("issues_*.jsonl")):
        t = p.stem[len("issues_"):]
        for iss in load_issues_tolerant(p):
            for sub, r in res.items():
                if sub in iss.frame_name:
                    r["types"][t] = r["types"].get(t, 0) + 1
                    r["real"] += marks.get(iss.key) == "real"
    return res


def finalize(out: Path) -> tuple[dict[str, int], dict[str, list[str]]]:
    """Export every stem whose rounds are ALL merged.

    Returns (exported: stem -> change count, unmerged: stem -> unmerged round tags).
    """
    out = Path(out)
    merged = merged_tags(out)
    counts: dict[str, int] = {}
    unmerged: dict[str, list[str]] = {}
    for r in round_files(out):
        if (r["stem"], r["tag"]) not in merged:
            unmerged.setdefault(r["stem"], []).append(r["tag"])
        else:
            n = sum(len(l) for l in json.loads(r["changes"].read_text()).values())
            counts[r["stem"]] = counts.get(r["stem"], 0) + n
    exported = {s: n for s, n in counts.items() if s not in unmerged}
    for stem in sorted(exported):
        src = out / "work" / f"{stem}.json"
        if not src.exists():
            raise FileNotFoundError(f"no working copy for {stem}: {src}")
        data.save_export(data.load_export(src), out / "final" / f"{stem}_qa_2026_10_17.json")
    return exported, unmerged
