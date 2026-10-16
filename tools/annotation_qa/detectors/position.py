"""Off-position detector: painted lane-line labels that are not on their paint.

Each poly is judged per ~60 px stretch (20 resampled points, stride 10) on the
ridge profile across the line, so a line that drifts off paint only part of the
way is still found. A consistent offset becomes a ``shift`` fix; anything else
is a hand fix. Road edges are excluded until a boundary score is calibrated.
"""

from __future__ import annotations

import warnings

import numpy as np

from annotation_qa.context import FrameContext
from annotation_qa.data import iter_polys
from annotation_qa.imaging import profile
from annotation_qa.issues import Issue

YELLOW_CATS = ("double_yellow_line", "center_turn_lane")
DASHED = "dashed_white_line"
SOLID = "solid_single_line"
CATEGORIES = YELLOW_CATS + (DASHED, SOLID)

R = 25
STRETCH, STRIDE = 20, 10
NEAR_R = 3  # a line within this many px of its paint is on it
NEAR_MAX, PEAK_MIN = 10.0, 25.0
SHIFT_STD_MAX, SHIFT_COVERAGE_MIN = 3.0, 0.3
SMOOTH = 15
DASH_PERCENTILE = 80
CONF_SCALE = 80.0


def _starts(m: int) -> list[int]:
    """Stretch start rows (stride 10); a last stretch is added so no tail row is unjudged."""
    starts = list(range(0, max(m - STRETCH, 0) + 1, STRIDE))
    if m > STRETCH and starts[-1] + STRETCH < m:
        starts.append(m - STRETCH)
    return starts


def _stretch_profiles(ridge: np.ndarray, dashed: bool):
    """[(row_start, row_stop, profile over offsets)] for stretches with data.

    Plain stretches use the mean; dashed lines the 80th percentile, so the
    gaps between dashes do not lower the ridge under the line.
    """
    m = len(ridge)
    starts = _starts(m)
    out = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for a in starts:
            seg = ridge[a:a + STRETCH]
            if dashed:
                prof = np.nanpercentile(seg, DASH_PERCENTILE, axis=0)
            else:
                prof = np.nanmean(seg, axis=0)
            if not np.isnan(prof).all():
                out.append((a, a + len(seg), prof))
    return out


def _channel(cat: str, prof) -> tuple[str, np.ndarray]:
    if cat in YELLOW_CATS:
        return "yellow", prof.yellow
    if cat == DASHED:
        return "white", prof.white
    with warnings.catch_warnings():  # solid: the channel with paint under the line
        warnings.simplefilter("ignore", RuntimeWarning)
        w = np.nanmax(np.nanmean(_windows(prof.white), axis=1)) if prof.white.size else np.nan
        y = np.nanmax(np.nanmean(_windows(prof.yellow), axis=1)) if prof.yellow.size else np.nan
    return ("yellow", prof.yellow) if (np.nan_to_num(y, nan=-1e9) > np.nan_to_num(w, nan=-1e9)) \
        else ("white", prof.white)


def _windows(ridge: np.ndarray) -> np.ndarray:
    """Offset-0 column grouped into stretches: (n_stretches, <=STRETCH)."""
    col = ridge[:, R]
    starts = _starts(len(col))
    width = min(STRETCH, len(col))
    return np.stack([col[a:a + width] for a in starts])


def _judge_poly(cat: str, poly: dict, ctx: FrameContext):
    """(confidence, summary dict) for the worst stretch of one poly, or None."""
    prof = profile(ctx.image, poly, R=R)
    if prof is None:
        return None
    channel, ridge = _channel(cat, prof)
    stretches = _stretch_profiles(ridge, cat == DASHED)
    if not stretches:
        return None
    ctr = R  # offset 0 column
    flagged = []
    for a, b, p in stretches:
        near = np.nanmax(p[ctr - NEAR_R:ctr + NEAR_R + 1]) if not np.isnan(
            p[ctr - NEAR_R:ctr + NEAR_R + 1]).all() else np.nan
        k = int(np.nanargmax(p))
        peak, off = float(p[k]), int(prof.offsets[k])
        if np.isnan(near):
            continue
        if near < NEAR_MAX and peak >= PEAK_MIN and abs(off) > NEAR_R:
            flagged.append((a, b, float(near), peak, off))
    if not flagged:
        return None
    worst = max(flagged, key=lambda f: f[3] - f[2])
    m = len(prof.P)
    offs = np.array([f[4] for f in flagged], float)
    covered = np.zeros(m, bool)
    total, count = np.zeros(m), np.zeros(m)
    for a, b, _n, _p, off in flagged:
        covered[a:b] = True
        total[a:b] += off
        count[a:b] += 1
    coverage = float(covered.mean())
    std = float(offs.std())
    per_point = np.where(count > 0, total / np.maximum(count, 1), 0.0)
    pad = SMOOTH // 2
    padded = np.pad(per_point, pad, mode="edge")
    smooth = np.convolve(padded, np.ones(SMOOTH) / SMOOTH, mode="valid")
    a, b = worst[0], worst[1]
    centre = prof.P[min((a + b) // 2, m - 1)]
    return {
        "conf": float(np.clip((worst[3] - worst[2]) / CONF_SCALE, 0, 1)),
        "anchor": (float(centre[0]), float(centre[1])),
        "consistent": std <= SHIFT_STD_MAX and coverage >= SHIFT_COVERAGE_MIN,
        "offsets": smooth.tolist(),
        "features": {"near": worst[2], "peak": worst[3], "off": worst[4],
                     "n_flagged_stretches": len(flagged), "n_stretches": len(stretches),
                     "coverage": coverage, "offset_std": std, "channel": channel},
    }


def detect(ctx: FrameContext) -> list[Issue]:
    stem = ctx.export.stem
    frame_name = ctx.frame.get("name", "")
    best: dict[str, tuple[float, int, dict, dict]] = {}
    for label, pi, poly in iter_polys(ctx.frame):
        cat = label.get("category")
        if cat not in CATEGORIES:
            continue
        r = _judge_poly(cat, poly, ctx)
        if r is not None and (label["id"] not in best or r["conf"] > best[label["id"]][0]):
            best[label["id"]] = (r["conf"], pi, r, label)
    issues = []
    for lid, (conf, pi, r, label) in best.items():
        f = r["features"]
        what = (f"{label['category']} is {abs(f['off'])} px off its {f['channel']} paint "
                f"on {f['n_flagged_stretches']} of {f['n_stretches']} stretches")
        if r["consistent"]:
            fix, hand, reason = ({"op": "shift", "poly_index": pi, "offsets": r["offsets"]},
                                 False, what)
        else:
            fix, hand, reason = None, True, what + "; not on paint; redraw"
        issues.append(Issue(
            issue_type="position", export_stem=stem, frame_name=frame_name, label_id=lid,
            category=label["category"], confidence=conf, reason=reason, anchor=r["anchor"],
            fix=fix, hand_fix=hand, features={**f, "poly_index": pi}))
    return issues
