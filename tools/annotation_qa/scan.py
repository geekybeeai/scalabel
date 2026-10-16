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
from annotation_qa.issues import append_issues
from annotation_qa.render import issue_crop

log = logging.getLogger("annotation_qa.scan")

__all__ = ["FrameContext", "ScanSummary", "run_scan", "REVIEWER_FRAMES"]

REVIEWER_FRAMES = ["208199", "207851", "MLC 1", "100865_201054", "565_verification",
                   "860_verification", "105193_204737", "100292_200553"]
RANDOM_PER_EXPORT = 20


@dataclass
class ScanSummary:
    frames: int = 0
    skipped: list[str] = field(default_factory=list)  # unreadable images
    counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)  # exceptions / failed writes


def _subset(frames: list[dict], subset: str | None) -> list[dict]:
    if subset is None:
        return frames
    if subset != "reviewer+random20":
        raise ValueError(f"unknown subset: {subset}")
    is_rev = [any(s in f["name"] for s in REVIEWER_FRAMES) for f in frames]
    rest = [i for i, r in enumerate(is_rev) if not r]
    picked = set(random.Random(0).sample(rest, min(RANDOM_PER_EXPORT, len(rest))))
    return [f for i, f in enumerate(frames) if is_rev[i] or i in picked]


def _save_summary(out: Path, s: ScanSummary) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "scan_summary.json").write_text(json.dumps(
        {"frames": s.frames, "skipped": s.skipped, "counts": s.counts, "errors": s.errors},
        indent=1))


def run_scan(exports: list[Path], root: Path, out: Path, types: list[str],
             subset: str | None = None) -> ScanSummary:
    types = list(dict.fromkeys(types))
    unknown = [t for t in types if t not in REGISTRY]
    if unknown:
        raise ValueError(f"unknown issue types: {unknown}; known: {sorted(REGISTRY)}")
    out = Path(out)
    summary = ScanSummary(counts={t: 0 for t in types})
    out.mkdir(parents=True, exist_ok=True)
    for t in types:  # truncate; issues are then appended frame by frame
        (out / f"issues_{t}.jsonl").write_text("")

    def fail(msg: str) -> None:
        log.error(msg)
        summary.errors.append(msg)

    for path in exports:
        stem = Path(path).stem
        work = out / "work" / f"{stem}.json"
        try:
            exp = load_export(work if work.exists() else path)
            frames = _subset(exp.frames, subset)
        except Exception as e:
            fail(f"{stem}: cannot load export: {type(e).__name__}: {e}")
            continue
        seen: set[str] = set()
        for n, frame in enumerate(frames, 1):
            fname = frame.get("name", "?") if isinstance(frame, dict) else "?"
            tag = f"{exp.stem}|{fname}"
            try:
                img = FrameImage(image_path(frame, root), out / "roi_cache")
                img.bgr  # noqa: B018 - force the load
            except FileNotFoundError as e:
                log.warning("skipping %s: %s", tag, e)
                summary.skipped.append(tag)
                print(f"[{n}/{len(frames)}] {tag}: skipped", flush=True)
                continue
            except Exception as e:
                fail(f"{tag}: cannot open frame: {type(e).__name__}: {e}")
                continue
            ctx = FrameContext(exp, frame, img)
            k = 0
            try:
                for t in types:
                    try:
                        found = REGISTRY[t](ctx)
                    except Exception as e:
                        fail(f"{tag}: detector {t} failed: {type(e).__name__}: {e}")
                        continue
                    kept = []
                    for issue in found:
                        if issue.key in seen:
                            log.warning("duplicate issue key, keeping first: %s", issue.key)
                            continue
                        seen.add(issue.key)
                        try:
                            crop = issue_crop(img, frame, issue)
                            cdir = out / "crops" / t
                            cdir.mkdir(parents=True, exist_ok=True)
                            name = hashlib.sha1(issue.key.encode()).hexdigest()[:12] + ".jpg"
                            if not cv2.imwrite(str(cdir / name), crop):
                                fail(f"{tag}: crop write failed for {issue.key}")
                        except Exception as e:
                            fail(f"{tag}: crop failed for {issue.key}: {type(e).__name__}: {e}")
                        kept.append(issue)
                    append_issues(out / f"issues_{t}.jsonl", kept)
                    summary.counts[t] += len(kept)
                    k += len(kept)
            finally:
                img.release()
            summary.frames += 1
            print(f"[{n}/{len(frames)}] {tag}: {k} issues", flush=True)
        _save_summary(out, summary)
    _save_summary(out, summary)
    return summary
