"""Calibration-bar report and the final deliverable copy."""

from __future__ import annotations

import json
from pathlib import Path

from annotation_qa import data
from annotation_qa.data import REVIEWER_FRAMES, reviewer_match
from annotation_qa.issues import load_issues_tolerant, read_marks
from annotation_qa.rounds import merged_tags, round_files

__all__ = ["REVIEWER_FRAMES", "bar_report", "finalize"]


def bar_report(out: Path) -> dict[str, dict]:
    """Per reviewer frame: issue counts by type and how many are marked real."""
    out = Path(out)
    marks = read_marks(out / "marks.json")
    res = {s: {"types": {}, "real": 0} for s in REVIEWER_FRAMES}
    for p in sorted(out.glob("issues_*.jsonl")):
        t = p.stem[len("issues_"):]
        for iss in load_issues_tolerant(p):
            sub = reviewer_match(iss.frame_name)
            if sub is not None:
                r = res[sub]
                r["types"][t] = r["types"].get(t, 0) + 1
                r["real"] += marks.get(iss.key) == "real"
    return res


def finalize(out: Path, originals_dir: Path | None = None
             ) -> tuple[dict[str, int], dict[str, list[str]]]:
    """Write one corrected export per batch to OUT/final.

    A batch with rounds is exported only when ALL its rounds are merged; a batch
    without rounds is copied as it is (0 changes). Returns (exported: stem ->
    change count, unmerged: stem -> unmerged round tags).
    """
    out = Path(out)
    originals_dir = Path(originals_dir or data.ORIGINALS_DIR)
    stems = {p.stem for p in originals_dir.glob("*.json")}
    stems |= {p.stem for p in (out / "work").glob("*.json") if "_fix_" not in p.stem}
    merged = merged_tags(out)
    counts: dict[str, int] = {}
    unmerged: dict[str, list[str]] = {}
    for r in round_files(out):
        stems.add(r["stem"])
        if (r["stem"], r["tag"]) not in merged:
            unmerged.setdefault(r["stem"], []).append(r["tag"])
        else:
            n = sum(len(l) for l in json.loads(r["changes"].read_text()).values())
            counts[r["stem"]] = counts.get(r["stem"], 0) + n
    exported: dict[str, int] = {}
    for stem in sorted(stems - set(unmerged)):
        src = out / "work" / f"{stem}.json"
        if not src.exists():
            if stem in counts:
                raise FileNotFoundError(f"no working copy for {stem}: {src}")
            src = originals_dir / f"{stem}.json"  # never fixed: ships as reviewed on 2026-10-15
        data.save_export(data.load_export(src), out / "final" / f"{stem}_qa_2026_10_17.json")
        exported[stem] = counts.get(stem, 0)
    return exported, unmerged
