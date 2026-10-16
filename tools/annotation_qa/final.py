"""Calibration-bar report and the final deliverable copy."""

from __future__ import annotations

import json
from pathlib import Path

from annotation_qa import data
from annotation_qa.issues import read_marks
from annotation_qa.review_server import load_issues_tolerant
from annotation_qa.viewb import round_files

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


def change_counts(out: Path) -> dict[str, int]:
    """Stem -> number of label changes over every round (stems with a round map to >= 0)."""
    counts: dict[str, int] = {}
    for r in round_files(out):
        n = sum(len(labels) for labels in json.loads(r["changes"].read_text()).values())
        counts[r["stem"]] = counts.get(r["stem"], 0) + n
    return counts


def finalize(out: Path) -> dict[str, int]:
    """Copy work/<stem>.json of every stem with a round to final/<stem>_qa_2026_10_17.json."""
    out = Path(out)
    counts = change_counts(out)
    for stem in sorted(counts):
        src = out / "work" / f"{stem}.json"
        if not src.exists():
            raise FileNotFoundError(f"no working copy for {stem}: {src}")
        data.save_export(data.load_export(src), out / "final" / f"{stem}_qa_2026_10_17.json")
    return counts
