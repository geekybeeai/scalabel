"""Images whose labelled lines lie under tree canopy or deep shadow: candidates to discard.

Roshan's issue list (tools/batch_review/Annotation_imgs_issues.xlsx) marks "Forest Area"
images -- narrow roads under canopy -- for removal. For each image of the working copies this
measures the share of its labelled line length that is COVERED: canopy (green: hue 30-95,
saturation >= 35, value >= 25, G >= R) or deep shadow (value < 55) on the line and on both
sides of it (``SIDE_PX`` away), so grass along a verge -- on one side of a curb -- does not
count. The images are measured at ``WORK_SIDE`` px on their longer side.

``python3 -m annotation_qa occlusion --out OUT [--min 0.10] [--project NAME]`` writes
OUT/discard/candidates.json (covered share >= --min, worst first) and, with --project, a
Scalabel project of those images (their current labels) for the /discard review page, where
each image is answered Discard / Keep (OUT/discard/answers.json).
"""

from __future__ import annotations

import json
import os
import sys
from multiprocessing import Pool
from pathlib import Path

import cv2
import numpy as np

from annotation_qa.data import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "scalabel" / "tools"))
from draw_poly2d import poly_to_points  # noqa: E402

WORK_SIDE = 1200  # px, the longer side the image is measured at
SIDE_PX = 5  # px (at WORK_SIDE) either side of the line that must be covered too
STEP = 2.0  # px between samples along a line (at WORK_SIDE)
MAX_MARKS = 400  # covered points kept per image for the page's highlight


def covered_mask(img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(valid, covered) masks: valid = inside the road strip (the image is black outside),
    covered = canopy or deep shadow."""
    valid = img.max(axis=2) > 8
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    g, r = img[..., 1], img[..., 2]
    canopy = (H >= 30) & (H <= 95) & (S >= 35) & (V >= 25) & (g >= r)
    return valid, valid & (canopy | (V < 55))


def measure_frame(img: np.ndarray, labels: list[dict]) -> dict:
    """Covered share of an image's labelled lines; ``img`` is the full-size BGR image."""
    h, w = img.shape[:2]
    s = min(1.0, WORK_SIDE / max(h, w))
    small = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else img
    valid, cov = covered_mask(small)
    cov_d = cv2.dilate(cov.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    H_, W_ = small.shape[:2]
    total = hit = 0
    marks = []
    for lab in labels:
        for poly in lab.get("poly2d") or []:
            P = np.asarray(poly_to_points(poly), float) * s
            if len(P) < 2:
                continue
            seg = np.diff(P, axis=0)
            cum = np.r_[0, np.cumsum(np.hypot(seg[:, 0], seg[:, 1]))]
            t = np.arange(0, cum[-1], STEP)
            if len(t) < 2:
                continue
            fx, fy = np.interp(t, cum, P[:, 0]), np.interp(t, cum, P[:, 1])
            dx, dy = np.gradient(fx), np.gradient(fy)
            nn = np.hypot(dx, dy) + 1e-9
            nx, ny = -dy / nn, dx / nn

            def at(off):
                return ((fx + off * nx).astype(int).clip(0, W_ - 1),
                        (fy + off * ny).astype(int).clip(0, H_ - 1))
            (x, y), (xl, yl), (xr, yr) = at(0), at(-SIDE_PX), at(SIDE_PX)
            ok = valid[y, x] & valid[yl, xl] & valid[yr, xr]
            c = ok & cov_d[y, x] & cov[yl, xl] & cov[yr, xr]
            total += int(ok.sum())
            hit += int(c.sum())
            if c.any():
                marks += [[float(a / s), float(b / s)] for a, b in zip(fx[c], fy[c])]
    n = max(int(valid.sum()), 1)
    if len(marks) > MAX_MARKS:
        marks = [marks[k] for k in np.linspace(0, len(marks) - 1, MAX_MARKS).astype(int)]
    return {"line_cover": hit / total if total else None, "strip_cover": float(cov.sum() / n),
            "line_samples": total, "marks": marks}


def _job(args):
    stem, index, name, labels = args
    img = cv2.imread(str(REPO_ROOT / "local-data" / name))
    if img is None:
        return None
    return {"stem": stem, "index": index, "name": name, **measure_frame(img, labels)}


def measure_all(out: Path, workers: int = 8) -> list[dict]:
    """Every frame of the working copies (OUT/work/<stem>.json, not round files)."""
    jobs = []
    for p in sorted((Path(out) / "work").glob("*.json")):
        if "_fix_" in p.name:
            continue
        for i, f in enumerate(json.loads(p.read_text())["frames"]):
            jobs.append((p.stem, i, f["name"], f.get("labels") or []))
    with Pool(max(1, workers)) as pool:
        return [r for r in pool.imap_unordered(_job, jobs, chunksize=4) if r]


def write_candidates(out: Path, rows: list[dict], min_cover: float) -> list[dict]:
    keep = sorted((r for r in rows if (r["line_cover"] or 0) >= min_cover),
                  key=lambda r: -r["line_cover"])
    d = Path(out) / "discard"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "candidates.json.tmp"
    tmp.write_text(json.dumps({"min_cover": min_cover, "measured": len(rows), "candidates": keep}))
    os.replace(tmp, d / "candidates.json")
    return keep


def write_items(out: Path, cands: list[dict], path: Path) -> Path:
    """An export of the candidate frames (worst first) from the working copies."""
    frames_by = {}
    for stem in {c["stem"] for c in cands}:
        frames_by[stem] = {f["name"]: f for f in
                           json.loads((Path(out) / "work" / f"{stem}.json").read_text())["frames"]}
    frames = [frames_by[c["stem"]][c["name"]] for c in cands]
    path.write_text(json.dumps({"frames": frames, "config": {}}))
    return path


def read_candidates(out: Path) -> dict:
    p = Path(out) / "discard" / "candidates.json"
    return json.loads(p.read_text()) if p.exists() else {"candidates": []}


def answers_path(out: Path) -> Path:
    return Path(out) / "discard" / "answers.json"


def read_answers(out: Path) -> dict:
    p = answers_path(out)
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def set_answer(out: Path, key: str, value: str | None, comment: str | None = None) -> dict:
    """value: "discard" | "keep" | "" (clear); comment kept as given (None = unchanged)."""
    if value not in ("discard", "keep", "", None):
        raise ValueError("value must be discard, keep or empty")
    a = read_answers(out)
    cur = a.get(key, {})
    if value is not None:
        cur["value"] = value
    if comment is not None:
        cur["comment"] = comment
    if not cur.get("value") and not cur.get("comment"):
        a.pop(key, None)
    else:
        a[key] = cur
    p = answers_path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(a, indent=1))
    os.replace(tmp, p)
    return a
