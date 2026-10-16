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
NEAR_BAND = (1, 6)  # px outside the line searched for a curb top / gutter
BAND_CONTRAST = 20.0
DIRT_SAT_MAX = 60  # saturated, smooth colour is paint, never dirt
SHADOW_GRAY = 60
ROAD_GRAY_MAX = 190  # a bright concrete walkway is never the road
DIRT_GREEN_MIN = -15  # magenta paint has G far below (R+B)/2; reddish clay only a little
STEP_MIN, STEP_WINDOW, STEP_NEAR, STEP_FAR = 30.0, 4, 3, 12
STEP_RISE_MAX_OFFSET = 3  # a kerb rise must start within this many px outside the line
PAINT_MAX_PX = 8  # bright then dark again within this: a painted line, not a kerb
MIN_FLAG_VOTES, CONF_FULL_VOTES = 2, 4
MIN_STEP_SAMPLES = 5
MIN_VOTES, MIN_EACH = 4, 2


def _stretches(poly: dict) -> list[tuple[dict, tuple[float, float]]]:
    """Sub-polys of ~150 px (dense L-polylines) with their centre points.

    ``geometry.dense`` already closes a closed poly's ring, so the closing
    segment is part of the stretches.
    """
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


def _ring_road_side(poly: dict) -> int:
    """Closed poly: the road is the ring's OUTSIDE. 0 = left (-normal) strip.

    With normal (-ty, tx), a positive shoelace area (same algebra in image
    coordinates) puts the interior on the +normal side, so the outside is left.
    """
    P = dense(poly)
    area = 0.5 * float(np.sum(P[:-1, 0] * P[1:, 1] - P[1:, 0] * P[:-1, 1]))
    return 0 if area > 0 else 1


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


def _sharp_step(ctx: FrameContext, sub: dict, side: int) -> bool:
    """Kerb signature at the line, on the outside (non-road) side: a gray rise >30
    within 4 px (outside brighter than the road) or a dark shadow line that recovers.

    ``side`` is the road side (0 = left). The profile spans -3..+6 px outward
    of the line; ROI-invalid samples are NaN so black padding is never a step.
    """
    P, N = resample(dense(sub))
    if P is None:
        return False
    h, w = ctx.image.shape
    pad = STEP_FAR + 3
    x0, y0 = max(int(P[:, 0].min()) - pad, 0), max(int(P[:, 1].min()) - pad, 0)
    x1, y1 = min(int(P[:, 0].max()) + pad + 1, w), min(int(P[:, 1].max()) + pad + 1, h)
    c = ctx.image.crop(x0, y0, x1, y1)
    if c.size == 0:
        return False
    gray = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY).astype(np.float32)
    # the left strip is the -normal side, so road left -> outside is +N
    out = 1.0 if side == 0 else -1.0
    off = np.arange(-STEP_NEAR, STEP_FAR + 1)
    X = P[:, 0:1] + out * off[None, :] * N[:, 0:1]
    Y = P[:, 1:2] + out * off[None, :] * N[:, 1:2]
    samp = cv2.remap(gray, (X - x0).astype(np.float32), (Y - y0).astype(np.float32),
                     cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    ix, iy = np.rint(X).astype(int), np.rint(Y).astype(int)
    inb = (ix >= 0) & (iy >= 0) & (ix < w) & (iy < h)
    ok = np.zeros(inb.shape, bool)
    ok[inb] = ctx.image.roi.mask[iy[inb], ix[inb]]
    cnt = ok.sum(axis=0)
    prof = np.where(cnt >= MIN_STEP_SAMPLES,
                    np.where(ok, samp, 0.0).sum(axis=0) / np.maximum(cnt, 1), np.nan)
    if np.isnan(prof).all():
        return False
    n_start = STEP_NEAR + STEP_RISE_MAX_OFFSET + 1  # candidate starts: offsets <= 3
    for i in range(min(n_start, len(prof) - STEP_WINDOW)):
        if not prof[i + STEP_WINDOW] - prof[i] > STEP_MIN:
            continue
        seg = prof[i + STEP_WINDOW:i + PAINT_MAX_PX + 1]
        k = int(np.nanargmax(seg)) + i + STEP_WINDOW
        after = prof[k + 1:k + 1 + STEP_WINDOW]
        painted = after.size and not np.isnan(after).all() and np.nanmin(after) < prof[k] - STEP_MIN
        if not painted:
            return True
    # a dark shadow line at the kerb: a dip that recovers within the window
    w = prof[:STEP_NEAR + PAINT_MAX_PX + 1]
    k = int(np.nanargmin(w))
    return bool(np.nanmax(w[:k + 1]) - w[k] > STEP_MIN and np.nanmax(w[k:]) - w[k] > STEP_MIN)


def _soft_like(st) -> bool:
    """Grass, or dirt/gravel: brown, moderately saturated and textured.
    Smooth saturated colour is paint, never dirt."""
    dirt = (st.brown > SOFT_BROWN_MIN and st.sat < DIRT_SAT_MAX and st.texture > 18
            and st.green > DIRT_GREEN_MIN)
    return bool(st.green > 8 or dirt)


def _vote(ctx: FrameContext, sub: dict, ring_road: int | None = None):
    """(vote, strength, outside-strip info) for one stretch; None when the stretch cannot be judged.

    R14: curb evidence comes from the curb itself (a narrow bright low-sat band
    1-6 px outside the line and/or a masked sharp step at the line). With that
    evidence the stretch votes curb whatever lies beyond. Soft needs no curb
    evidence AND dirt/gravel/grass outside.
    """
    strips = side_strips(ctx.image, sub)
    if strips is None:
        return None
    side = ring_road if ring_road is not None else _road_side(*strips)
    if side is None:
        return None
    road, o = strips[side], strips[1 - side]
    if road.gray < SHADOW_GRAY or o.gray < SHADOW_GRAY:
        return None  # deep shadow: not judged
    if ring_road is None and road.gray > ROAD_GRAY_MAX:
        return None  # the 'road' side is bright concrete: side not trustworthy
    near = side_strips(ctx.image, sub, near=NEAR_BAND[0], far=NEAR_BAND[1])
    band = near_soft = False
    if near is not None:
        nb = near[1 - side]
        band = nb.gray - road.gray > BAND_CONTRAST and nb.sat < CURB_SAT_MAX
        near_soft = _soft_like(nb)
    curb = 0.0
    step = False
    if band:
        curb += 1.0
    elif o.gray - road.gray > 25 and o.sat < CURB_SAT_MAX and o.texture < CURB_TEXTURE_MAX:
        curb += 1.0
    # a gray step / shadow line next to dirt or grass is the ground edge, not a kerb;
    # on its own it only counts when the far strip is a hard surface too
    if (band or not near_soft) and _sharp_step(ctx, sub, side):
        step = True
        if curb:
            curb += 0.5
        elif not _soft_like(o):
            curb = 1.0
    info = {"gray": o.gray, "sat": o.sat, "green": o.green, "brown": o.brown,
            "texture": o.texture, "band": band, "step": step}
    if curb:
        return (CURB, curb, info)
    if _soft_like(o):
        return (SOFT, 1.0, info)
    return (None, 0.0, info)  # judged, no verdict


def _features(votes: list, deciding: list, share: float) -> dict:
    """Vote counts plus the mean outside-strip stats of the deciding stretches,
    so thresholds can be refit from marks without rescanning."""
    n = len(votes)
    f = {"n_votes": n, "curb_votes": sum(1 for v in votes if v[0] == CURB),
         "soft_votes": sum(1 for v in votes if v[0] == SOFT), "share": float(share),
         "near_band_votes": sum(1 for v in votes if v[3]["band"]),
         "step_votes": sum(1 for v in votes if v[3]["step"])}
    for k in ("gray", "sat", "green", "brown", "texture"):
        f[f"outside_{k}"] = float(np.mean([v[3][k] for v in deciding]))
    return f


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
        ring_road = _ring_road_side(poly) if poly.get("closed") else None
        for sub, centre in _stretches(poly):
            v = _vote(ctx, sub, ring_road)
            if v is not None and v[0] is not None:  # only stretches that cast a vote
                votes.append((v[0], v[1], centre, v[2]))

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
        wrong = SOFT if cat == CURB else CURB
        share = (n_soft if cat == CURB else n_curb) / n
        if share >= FLAG_SHARE and n >= MIN_FLAG_VOTES:
            anchor = max((v for v in votes if v[0] == wrong), key=lambda v: v[1])[2]
            kind = "grass/dirt" if wrong == SOFT else "a hard surface"
            deciding = [v for v in votes if v[0] == wrong]
            issues.append(Issue(
                **base, confidence=float(np.clip((share - 0.5) * 2, 0, 1) * min(1.0, n / CONF_FULL_VOTES)),
                reason=f"{cat} borders {kind} on {share:.0%} of {n} voting stretches",
                anchor=(float(anchor[0]), float(anchor[1])),
                fix={"op": "set_category", "to": wrong}, hand_fix=False,
                features=_features(votes, deciding, share)))
        elif (n >= MIN_VOTES and min(n_curb, n_soft) >= MIN_EACH
              and n_curb / n >= MIXED_SHARE and n_soft / n >= MIXED_SHARE):
            anchor = max(votes, key=lambda v: v[1])[2]
            issues.append(Issue(**base, confidence=0.5,
                                reason="edge changes type partway; split by hand",
                                anchor=anchor, fix=None, hand_fix=True,
                                features=_features(votes, votes, max(n_curb, n_soft) / n)))
    return issues
