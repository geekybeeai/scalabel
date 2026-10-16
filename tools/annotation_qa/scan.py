"""Run detectors over Scalabel exports; write issues, crops and a summary."""

from __future__ import annotations

import hashlib
import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path

import cv2

from annotation_qa.context import FrameContext
from annotation_qa.data import image_path, load_export
from annotation_qa.detectors import REGISTRY
from annotation_qa.imaging import FrameImage
from annotation_qa.issues import write_issues
from annotation_qa.render import issue_crop

log = logging.getLogger("annotation_qa.scan")

__all__ = ["FrameContext", "ScanSummary", "run_scan", "REVIEWER_FRAMES"]

REVIEWER_FRAMES = ["208199", "207851", "MLC 1", "100865_201054", "565_verification",
                   "860_verification", "105193_204737", "100292_200553"]
RANDOM_PER_EXPORT = 20


@dataclass
class ScanSummary:
    frames: int = 0
    skipped: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def _subset(frames: list[dict], subset: str | None) -> list[dict]:
    if subset is None:
        return frames
    if subset != "reviewer+random20":
        raise ValueError(f"unknown subset: {subset}")
    is_rev = [any(s in f["name"] for s in REVIEWER_FRAMES) for f in frames]
    rest = [i for i, r in enumerate(is_rev) if not r]
    picked = set(random.Random(0).sample(rest, min(RANDOM_PER_EXPORT, len(rest))))
    return [f for i, f in enumerate(frames) if is_rev[i] or i in picked]


def run_scan(exports: list[Path], root: Path, out: Path, types: list[str],
             subset: str | None = None) -> ScanSummary:
    unknown = [t for t in types if t not in REGISTRY]
    if unknown:
        raise ValueError(f"unknown issue types: {unknown}; known: {sorted(REGISTRY)}")
    out = Path(out)
    summary = ScanSummary(counts={t: 0 for t in types})
    found = {t: [] for t in types}
    for path in exports:
        stem = Path(path).stem
        work = out / "work" / f"{stem}.json"
        exp = load_export(work if work.exists() else path)
        frames = _subset(exp.frames, subset)
        for n, frame in enumerate(frames, 1):
            img = FrameImage(image_path(frame, root), out / "roi_cache")
            try:
                img.bgr  # noqa: B018 - force the load
            except FileNotFoundError as e:
                log.warning("skipping %s|%s: %s", exp.stem, frame["name"], e)
                summary.skipped.append(f"{exp.stem}|{frame['name']}")
                print(f"[{n}/{len(frames)}] {exp.stem} {frame['name']}: skipped", flush=True)
                continue
            ctx = FrameContext(exp, frame, img)
            k = 0
            try:
                for t in types:
                    for issue in REGISTRY[t](ctx):
                        crop = issue_crop(img, frame, issue)
                        name = hashlib.sha1(issue.key.encode()).hexdigest()[:12] + ".jpg"
                        cdir = out / "crops" / t
                        cdir.mkdir(parents=True, exist_ok=True)
                        cv2.imwrite(str(cdir / name), crop)
                        found[t].append(issue)
                        k += 1
            finally:
                img.release()
            summary.frames += 1
            print(f"[{n}/{len(frames)}] {exp.stem} {frame['name']}: {k} issues", flush=True)
    for t in types:
        write_issues(out / f"issues_{t}.jsonl", found[t])
        summary.counts[t] = len(found[t])
    out.mkdir(parents=True, exist_ok=True)
    (out / "scan_summary.json").write_text(json.dumps(
        {"frames": summary.frames, "skipped": summary.skipped, "counts": summary.counts}, indent=1))
    return summary
