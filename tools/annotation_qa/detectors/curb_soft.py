"""Curb <-> soft-edge detector.

Flags ``curb_road_edge`` polylines that border dirt/grass and
``without_curb_road_edge`` polylines that border a hard (concrete) surface.
Every poly is judged in ~150 px stretches so mixed edges are seen per stretch.
"""

from __future__ import annotations

import cv2
import numpy as np

from annotation_qa.context import FrameContext
from annotation_qa.data import iter_polys
from annotation_qa.geometry import dense, resample
from annotation_qa.imaging import side_strips
from annotation_qa.issues import Issue

CURB, SOFT = "curb_road_edge", "without_curb_road_edge"
STRETCH_PTS = 50  # 150 px at the 3 px resample step
MIN_TAIL_PTS = 17  # a last run shorter than ~50 px merges into the previous one
MIN_GRAY_DIFF = 8.0
FLAG_SHARE = 0.6
MIXED_SHARE = 0.4
# Calibrated on real orthomosaics (the brief's 30 / 18 / 15 were far too tight:
# real concrete is warm and grainy, sat 15-30, R-B 15-28, gray std 20-35).
CURB_SAT_MAX, CURB_TEXTURE_MAX, SOFT_BROWN_MIN = 35, 40, 30
STEP_MIN, STEP_WINDOW, STEP_REACH = 30.0, 4, 10


def _stretches(poly: dict) -> list[tuple[dict, tuple[float, float]]]:
    """Sub-polys of ~150 px (dense L-polylines) with their centre points."""
    pts = dense(poly)
    if not np.isfinite(pts).all():
        return []
    P, _ = resample(pts)
    if P is None:
        return []
    cuts = list(range(0, len(P), STRETCH_PTS))
    runs = [P[a:b] for a, b in zip(cuts, cuts[1:] + [len(P)])]
    if len(runs) > 1 and len(runs[-1]) < MIN_TAIL_PTS:
        runs[-2] = np.vstack([runs[-2], runs[-1]])
        runs.pop()
    out = []
    for run in runs:
        if len(run) < 2:
            continue
        sub = {"vertices": run.tolist(), "types": "L" * len(run), "closed": False}
        out.append((sub, tuple(run[len(run) // 2])))
    return out


def _road_side(left, right) -> int | None:
    """0 if the left strip is road, 1 if the right one; None when unknown."""
    if abs(left.gray - right.gray) < MIN_GRAY_DIFF:
        return None
    votes = 0  # >0: left is road
    votes += 1 if left.gray < right.gray else -1
    if abs(left.sat - right.sat) > 5:
        votes += 1 if left.sat < right.sat else -1
    if abs(left.texture - right.texture) > 3:
        votes += 1 if left.texture < right.texture else -1
    if votes == 0:  # tie: fall back to the less saturated side
        votes = 1 if left.sat <= right.sat else -1
    return 0 if votes > 0 else 1


def _sharp_step(ctx: FrameContext, sub: dict) -> bool:
    """Mean gray profile across the line has a >30 step within 4 px."""
    P, N = resample(dense(sub))
    if P is None:
        return False
    h, w = ctx.image.shape
    pad = STEP_REACH + 2
    x0, y0 = max(int(P[:, 0].min()) - pad, 0), max(int(P[:, 1].min()) - pad, 0)
    x1, y1 = min(int(P[:, 0].max()) + pad + 1, w), min(int(P[:, 1].max()) + pad + 1, h)
    c = ctx.image.crop(x0, y0, x1, y1)
    if c.size == 0:
        return False
    gray = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY).astype(np.float32)
    off = np.arange(-STEP_REACH, STEP_REACH + 1)
    X = (P[:, 0:1] + off[None, :] * N[:, 0:1] - x0).astype(np.float32)
    Y = (P[:, 1:2] + off[None, :] * N[:, 1:2] - y0).astype(np.float32)
    prof = cv2.remap(gray, X, Y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).mean(axis=0)
    return bool((np.abs(prof[STEP_WINDOW:] - prof[:-STEP_WINDOW]) > STEP_MIN).any())


def _vote(ctx: FrameContext, sub: dict):
    """(vote, strength) for one stretch; vote is CURB, SOFT or None (unknown/tie)."""
    strips = side_strips(ctx.image, sub)
    if strips is None:
        return None
    side = _road_side(*strips)
    if side is None:
        return None
    road, o = strips[side], strips[1 - side]
    curb = 1.0 if (o.gray - road.gray > 25 and o.sat < CURB_SAT_MAX and o.texture < CURB_TEXTURE_MAX) else 0.0
    soft = 1.0 if (o.green > 8 or (o.brown > SOFT_BROWN_MIN and o.texture > 18)) else 0.0
    if _sharp_step(ctx, sub):
        curb += 0.5
    if curb == soft:
        return (None, 0.0)  # valid stretch, no verdict
    return (CURB, curb - soft) if curb > soft else (SOFT, soft - curb)


def detect(ctx: FrameContext) -> list[Issue]:
    stem = ctx.export.stem
    frame_name = ctx.frame.get("name", "")
    per_label: dict[str, list] = {}
    labels: dict[str, dict] = {}
    for label, _pi, poly in iter_polys(ctx.frame):
        if label.get("category") not in (CURB, SOFT):
            continue
        labels[label["id"]] = label
        votes = per_label.setdefault(label["id"], [])
        for sub, centre in _stretches(poly):
            v = _vote(ctx, sub)
            if v is not None:
                votes.append((v[0], v[1], centre))

    issues = []
    for lid, votes in per_label.items():
        n = len(votes)
        if n == 0:
            continue
        cat = labels[lid]["category"]
        n_curb = sum(1 for v in votes if v[0] == CURB)
        n_soft = sum(1 for v in votes if v[0] == SOFT)
        base = dict(issue_type="curb_soft", export_stem=stem, frame_name=frame_name,
                    label_id=lid, category=cat)
        if n_curb / n >= MIXED_SHARE and n_soft / n >= MIXED_SHARE:
            anchor = max(votes, key=lambda v: v[1])[2]
            issues.append(Issue(**base, confidence=0.5,
                                reason="edge changes type partway; split by hand",
                                anchor=anchor, fix=None, hand_fix=True))
            continue
        wrong = SOFT if cat == CURB else CURB
        share = (n_soft if cat == CURB else n_curb) / n
        if share < FLAG_SHARE:
            continue
        anchor = max((v for v in votes if v[0] == wrong), key=lambda v: v[1])[2]
        kind = "grass/dirt" if wrong == SOFT else "a hard surface"
        issues.append(Issue(
            **base, confidence=float(np.clip((share - 0.5) * 2, 0, 1)),
            reason=f"{cat} borders {kind} on {share:.0%} of {n} stretches",
            anchor=(float(anchor[0]), float(anchor[1])),
            fix={"op": "set_category", "to": wrong}, hand_fix=False))
    return issues
