"""Solid <-> dashed detector for ``solid_single_line`` / ``dashed_white_line``.

The pattern is read from the ridge at offset 0 along the line (channel chosen
by position.select_channel). A sample is ON at ridge >= 15, OFF below it, and
UNKNOWN where it is outside the ROI or darker than gray 50 (cars, shadows). An
unknown sample breaks a run, so an occluder neither makes a gap nor bridges one.

Dashed: >= 3 off-runs of >= 12 px, CV of the interior on-runs <= 0.5, on-fraction
in [0.15, 0.75]. Solid: on-fraction >= 0.85. Lines under 120 px are skipped.
Guard: a line whose paint sits consistently off to one side is a position issue,
so a label is judged only when the ridge within +-3 px is close to the best ridge
within +-25 px. A solid label over dashes is only flagged on the white channel
(``dashed_white_line`` is white). A label with several polys is judged on its
longest poly. Fix: ``set_category``.
"""

from __future__ import annotations

import warnings

import numpy as np

from annotation_qa.context import FrameContext
from annotation_qa.data import iter_polys
from annotation_qa.detectors.position import DASHED, SOLID, select_channel
from annotation_qa.imaging import profile
from annotation_qa.issues import Issue

R = 25
STEP = 3.0  # px between profile samples
ON_MIN = 15.0
DARK_GRAY = 50.0
MIN_LENGTH = 120.0
OFF_RUN_MIN = 12.0  # px
MIN_OFF_RUNS = 3
CV_MAX = 0.5
DASH_FRAC = (0.15, 0.75)
SOLID_FRAC = 0.85
NEAR_R = 3
MIN_DASHES = 4  # interior on-runs needed to call a pattern dashed
UNKNOWN_MAX = 0.25  # skip when more than this share of in-ROI samples is dark
NEAR_RATIO = 0.7  # ridge within +-3 px must be this share of the best ridge within +-25


def _runs(state: np.ndarray):
    """[(value, start, stop)] runs of equal state; -1 (unknown) runs are dropped."""
    out, i, n = [], 0, len(state)
    while i < n:
        j = i
        while j < n and state[j] == state[i]:
            j += 1
        if state[i] >= 0:
            out.append((int(state[i]), i, j))
        i = j
    return out


def _judge_poly(cat: str, poly: dict, ctx: FrameContext):
    prof = profile(ctx.image, poly, R=R)
    if prof is None or (len(prof.P) - 1) * STEP < MIN_LENGTH:
        return None
    channel, ridge = select_channel(cat, prof)
    col = ridge[:, R]
    P = prof.P
    ix = np.clip(np.rint(P[:, 0]).astype(int), 0, ctx.image.shape[1] - 1)
    iy = np.clip(np.rint(P[:, 1]).astype(int), 0, ctx.image.shape[0] - 1)
    gray = ctx.image.bgr[iy, ix].astype(np.float32).mean(axis=1)
    state = np.where(np.isnan(col) | (gray < DARK_GRAY), -1, (col >= ON_MIN).astype(int))
    known = state >= 0
    in_roi = ~np.isnan(col)
    unknown_fraction = float((in_roi & ~known).sum() / max(in_roi.sum(), 1))
    if unknown_fraction > UNKNOWN_MAX:
        return None
    if known.sum() * STEP < MIN_LENGTH:
        return None
    on_fraction = float(state[known].mean())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        near = np.nanmax(ridge[:, R - NEAR_R:R + NEAR_R + 1], axis=1)
        full = np.nanmax(ridge, axis=1)
        near_p, full_p = np.nanpercentile(near[known], 90), np.nanpercentile(full[known], 90)
    runs = _runs(state)
    off_runs = [(a, b) for v, a, b in runs if v == 0 and (b - a) * STEP >= OFF_RUN_MIN]
    last = len(state) - 1
    on_len = [(b - a) * STEP for v, a, b in runs if v == 1 and a > 0 and b <= last
              and state[a - 1] == 0 and state[b] == 0]  # interior runs only (ends are cut)
    cv = float(np.std(on_len) / np.mean(on_len)) if len(on_len) >= 2 else float("inf")
    on_paint = on_fraction >= DASH_FRAC[0] and near_p >= NEAR_RATIO * full_p
    if not on_paint:
        return None
    dashed = len(off_runs) >= MIN_OFF_RUNS and len(on_len) >= MIN_DASHES and cv <= CV_MAX and DASH_FRAC[0] <= on_fraction <= DASH_FRAC[1]
    solid = on_fraction >= SOLID_FRAC
    if dashed == solid:
        return None
    if cat == SOLID and dashed and channel == "white":
        to, conf = DASHED, (1 - cv / CV_MAX) * 0.5 + 0.5
    elif cat == DASHED and solid:
        to, conf = SOLID, float(np.clip((on_fraction - SOLID_FRAC) / (1 - SOLID_FRAC), 0, 1)) * 0.5 + 0.5
    else:
        return None
    mid = P[len(P) // 2]
    return {"to": to, "conf": float(np.clip(conf, 0, 1)), "anchor": (float(mid[0]), float(mid[1])),
            "length": (len(P) - 1) * STEP,
            "features": {"on_fraction": on_fraction, "n_off_runs": len(off_runs), "on_cv": cv,
                         "n_on_runs": len(on_len), "unknown_fraction": unknown_fraction, "near_ridge": float(near_p),
                         "best_ridge": float(full_p), "channel": channel}}


def detect(ctx: FrameContext) -> list[Issue]:
    stem, frame_name = ctx.export.stem, ctx.frame.get("name", "")
    longest: dict[str, tuple[float, int, dict, dict]] = {}
    for label, pi, poly in iter_polys(ctx.frame):
        if label.get("category") not in (SOLID, DASHED):
            continue
        # judge the longest poly of a label (proxy: diagonal of its vertex bbox)
        xs = [v[0] for v in poly["vertices"]]
        ys = [v[1] for v in poly["vertices"]]
        span = float(np.hypot(max(xs) - min(xs), max(ys) - min(ys))) if xs else 0.0
        if label["id"] not in longest or span > longest[label["id"]][0]:
            longest[label["id"]] = (span, pi, poly, label)
    issues = []
    for lid, (_s, pi, poly, label) in longest.items():
        r = _judge_poly(label["category"], poly, ctx)
        if r is None:
            continue
        f = r["features"]
        what = ("dashes" if r["to"] == DASHED else "continuous paint")
        issues.append(Issue(
            issue_type="solid_dashed", export_stem=stem, frame_name=frame_name, label_id=lid,
            category=label["category"], confidence=r["conf"],
            reason=f"{label['category']} lies over {what} (on {f['on_fraction']:.2f}, "
                   f"{f['n_off_runs']} gaps)",
            anchor=r["anchor"], fix={"op": "set_category", "to": r["to"]}, hand_fix=False,
            features={**f, "poly_index": pi, "length": r["length"]}))
    return issues
