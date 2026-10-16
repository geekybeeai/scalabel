"""Yellow-line structure: double vs single yellow, and centre-turn-lane checks.

Width (``double_yellow_line`` and yellow ``solid_single_line``): the full width
at half maximum (FWHM, imaging.yellow_fwhm) of the yellow score at offset 0. A
double-yellow label narrower than ``fwhm_threshold`` gets "single yellow? check
class"; a yellow single wider than it gets "double yellow?" (both set_category,
the first at confidence 0.5). "Yellow" is position.select_channel == yellow.

Centre turn lane (``center_turn_lane``, at most one issue per label, paint first):
- paint: only when the line sits on yellow by the position detector's standard
  (median over the line of the max yellow ridge within +-3 px >= 10; otherwise
  nothing is emitted, position owns that case) a second yellow ridge
  (>= 12 at the 90th percentile, so sparse dashes count; real dashed yellow
  measures 12-40 on frame 101713) must lie 3-10 px to
  one side of the line's own ridge peak, else "no dashed yellow beside it; ...".
- partner: another CTL label in the frame, direction within 15 degrees,
  overlapping along the line, whose perpendicular distance is within +-35% of
  ``ctl_pair_distance_median``, else "missing other side of turn lane".

Global numbers come from the cached calibration step (R20, ``python3 -m
annotation_qa calibrate-yellow``), read from ``<OUT>/calibration/yellow_stats.json``
where OUT is the parent of ``ctx.image.roi_cache`` (scan puts the cache at
OUT/roi_cache), unless ``set_stats_path`` gives an explicit file. Loaded once per
path. If ``width_usable`` is false (calibration found double yellows less than 1.5x as wide as
yellow singles) the width check is skipped too. If the file is missing, one warning is logged and the width and partner
checks are skipped; the CTL dashed-beside check still runs. A label with several polys
is judged on its longest poly.
"""

from __future__ import annotations

import json
import logging
import warnings
from pathlib import Path

import numpy as np

from annotation_qa.context import FrameContext
from annotation_qa.data import iter_polys
from annotation_qa.detectors.position import SOLID, select_channel
from annotation_qa.geometry import dense
from annotation_qa.imaging import profile, yellow_fwhm
from annotation_qa.issues import Issue

log = logging.getLogger("annotation_qa.yellow_structure")

DOUBLE, CTL = "double_yellow_line", "center_turn_lane"
R = 25
LINE_R = 3  # the solid yellow must sit within this many px of the line
ON_PAINT_MIN = 10.0  # position detector's standard: near ridge >= 10 within +-3 px
SECOND_MIN = 12.0
YELLOW_CONTRAST_MIN = 80.0  # raw yellow contrast that is yellow paint beyond doubt
SECOND_PERCENTILE = 90
SECOND_NEAR, SECOND_FAR = 3, 10
PAIR_TOL = 0.35
PAIR_ANGLE = 15.0
STATS_NAME = "calibration/yellow_stats.json"

_cache: dict[Path, dict | None] = {}
_explicit: Path | None = None


def stats_path_for(out: Path) -> Path:
    return Path(out) / STATS_NAME


def set_stats_path(path: Path | None) -> None:
    """Use this stats file for every frame (None: derive from the ROI cache location)."""
    global _explicit
    _explicit = Path(path) if path is not None else None


def clear_cache(path: Path | None = None) -> None:
    if path is None:
        _cache.clear()
    else:
        _cache.pop(Path(path), None)


def load_stats(path: Path) -> dict | None:
    """The calibration stats at ``path`` or None (one warning per missing path)."""
    path = Path(path)
    if path not in _cache:
        try:
            _cache[path] = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            log.warning("yellow_stats not available (%s: %s); skipping width and "
                        "partner-distance checks; run `annotation_qa calibrate-yellow`",
                        path, type(e).__name__)
            _cache[path] = None
        else:
            st = _cache[path]
            if not st.get("width_usable", True):
                log.warning("yellow width check skipped (%s): double-yellow median FWHM %.1f px "
                            "is under 1.5x the yellow-single median %.1f px",
                            path, st.get("fwhm_double_median", float("nan")),
                            st.get("fwhm_single_yellow_median", float("nan")))
    return _cache[path]


def _stats(ctx: FrameContext) -> dict | None:
    path = _explicit or stats_path_for(Path(ctx.image.roi_cache).parent)
    return load_stats(path)


# ---- CTL pair geometry (shared with calibrate) ----
def line_geom(poly: dict) -> dict | None:
    """Centroid, unit direction (PCA) and longitudinal span of a poly."""
    pts = dense(poly)
    if len(pts) < 2 or not np.isfinite(pts).all():
        return None
    c = pts.mean(axis=0)
    _u, _s, vt = np.linalg.svd(pts - c, full_matrices=False)
    d = vt[0]
    t = (pts - c) @ d
    return {"c": c, "d": d, "t0": float(t.min()), "t1": float(t.max())}


def pair_distance(a: dict, b: dict) -> float | None:
    """Perpendicular distance between two roughly parallel lines, else None.

    Direction difference must be under 15 degrees and the lines must overlap
    along a's direction. Measured at the middle of the overlap interval: the
    distance from that point on a to b's line (symmetric in a and b).
    """
    cos = float(a["d"] @ b["d"])
    if np.degrees(np.arccos(min(abs(cos), 1.0))) >= PAIR_ANGLE:
        return None
    sgn = 1.0 if cos >= 0 else -1.0
    shift = float((b["c"] - a["c"]) @ a["d"])  # b's centroid along a's direction
    t_b0, t_b1 = sorted((shift + sgn * b["t0"], shift + sgn * b["t1"]))
    lo, hi = max(t_b0, a["t0"]), min(t_b1, a["t1"])
    if hi <= lo:
        return None
    p = a["c"] + 0.5 * (lo + hi) * a["d"]
    v = p - b["c"]
    return abs(float(v[0] * b["d"][1] - v[1] * b["d"][0]))


def nearest_partner(geoms: dict[str, dict], lid: str) -> tuple[str, float] | None:
    best = None
    for other, g in geoms.items():
        if other == lid:
            continue
        d = pair_distance(geoms[lid], g)
        if d is not None and (best is None or d < best[1]):
            best = (other, d)
    return best


def longest_polys(frame: dict, cats: tuple[str, ...]) -> dict[str, tuple[int, dict, dict]]:
    """label id -> (poly_index, poly, label): the longest poly of each matching label."""
    best: dict[str, tuple[float, int, dict, dict]] = {}
    for label, pi, poly in iter_polys(frame):
        if label.get("category") not in cats:
            continue
        xs = [v[0] for v in poly["vertices"]]
        ys = [v[1] for v in poly["vertices"]]
        span = float(np.hypot(max(xs) - min(xs), max(ys) - min(ys))) if xs else 0.0
        if label["id"] not in best or span > best[label["id"]][0]:
            best[label["id"]] = (span, pi, poly, label)
    return {k: (v[1], v[2], v[3]) for k, v in best.items()}


def is_yellow(img, poly: dict, res: tuple[float, int, float] | None = None) -> bool:
    """A solid line is yellow when position.select_channel picks the yellow
    channel, or when the raw yellow contrast is unmistakable. The second test
    covers bands wide enough that the ridge vanishes inside them. It is kept
    high on purpose: real yellow paint here has contrast 20-45, but warm-tinted
    white paint and tan asphalt reach 20-60, so a lower bar would call them yellow."""
    prof = profile(img, poly, R=R)
    if prof is None:
        return False
    if select_channel(SOLID, prof)[0] == "yellow":
        return True
    res = res if res is not None else yellow_fwhm(img, poly, R=R)
    return res is not None and res[2] >= YELLOW_CONTRAST_MIN


# ---- checks ----
def _mid(poly: dict) -> tuple[float, float]:
    pts = dense(poly)
    p = pts[len(pts) // 2]
    return float(p[0]), float(p[1])


def _issue(ctx, label, pi, poly, conf, reason, fix, features, anchor=None):
    return Issue(
        issue_type="yellow_structure", export_stem=ctx.export.stem,
        frame_name=ctx.frame.get("name", ""), label_id=label["id"], category=label["category"],
        confidence=float(np.clip(conf, 0, 1)), reason=reason, anchor=anchor or _mid(poly),
        fix=fix, hand_fix=fix is None, features={**features, "poly_index": pi})


def _width_issue(ctx, label, pi, poly, stats):
    cat = label["category"]
    res = yellow_fwhm(ctx.image, poly, R=R)
    if res is None:
        return None
    if cat == SOLID and not is_yellow(ctx.image, poly, res):
        return None
    fwhm, n, _centre = res
    thr = float(stats["fwhm_threshold"])
    feats = {"fwhm": fwhm, "threshold": thr, "n_samples": n}
    if cat == DOUBLE and fwhm < thr:
        return _issue(ctx, label, pi, poly, 0.5, "single yellow? check class",
                      {"op": "set_category", "to": SOLID}, feats)
    if cat == SOLID and fwhm > thr:
        conf = 0.5 + 0.5 * min((fwhm - thr) / max(thr, 1e-6), 1.0)
        return _issue(ctx, label, pi, poly, conf, "double yellow?",
                      {"op": "set_category", "to": DOUBLE}, feats)
    return None


def _paint(ctx, poly):
    """(first ridge, second ridge, peak offset) over the line, or None."""
    prof = profile(ctx.image, poly, R=R)
    if prof is None:
        return None
    ridge = prof.yellow[prof.valid]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        near = ridge[:, R - LINE_R:R + LINE_R + 1]
        if np.isnan(near).all():
            return None
        first = float(np.nanmedian(np.nanmax(near, axis=1)))
        med = np.nanmedian(near, axis=0)
        # centre of the plateau around the peak (a 5 px stripe peaks on several offsets)
        k = int(round(float(np.mean(np.flatnonzero(med >= np.nanmax(med) - 0.1 * abs(np.nanmax(med)))) - LINE_R)))
        second = -np.inf
        for sign in (-1, 1):
            cols = [R + k + sign * s for s in range(SECOND_NEAR, SECOND_FAR + 1)
                    if 0 <= R + k + sign * s < ridge.shape[1]]
            if not cols:
                continue
            side = np.nanmax(ridge[:, cols], axis=1)
            if not np.isnan(side).all():
                second = max(second, float(np.nanpercentile(side, SECOND_PERCENTILE)))
    return first, (second if np.isfinite(second) else 0.0), k


def _ctl_issue(ctx, lid, label, pi, poly, geoms, stats):
    paint = _paint(ctx, poly)
    paint_feats = {}
    if paint is not None:
        first, second, k = paint
        paint_feats = {"first_ridge": first, "second_ridge": second, "peak_offset": k}
        # a line off its paint is the position detector's case: emit nothing for it here
        if first >= ON_PAINT_MIN and second < SECOND_MIN:
            return _issue(ctx, label, pi, poly, 0.6,
                          "no dashed yellow beside it; is this a turn lane?", None, paint_feats)
    if stats is None or geoms.get(lid) is None:
        return None
    med = float(stats["ctl_pair_distance_median"])
    lo, hi = med * (1 - PAIR_TOL), med * (1 + PAIR_TOL)
    ds = [d for o, g in geoms.items() if o != lid and g is not None
          for d in [pair_distance(geoms[lid], g)] if d is not None]
    ok = [d for d in ds if lo <= d <= hi]
    feats = {"ctl_pair_distance_median": med, "partner_distance": min(ok, key=lambda d: abs(d - med)) if ok else None,
             "nearest_distance": min(ds) if ds else None, **paint_feats}
    if ok:
        return None
    return _issue(ctx, label, pi, poly, 0.6, "missing other side of turn lane", None, feats)


def detect(ctx: FrameContext) -> list[Issue]:
    stats = _stats(ctx)
    issues = []
    lines = longest_polys(ctx.frame, (DOUBLE, SOLID, CTL))
    geoms = {lid: line_geom(poly) for lid, (_pi, poly, lab) in lines.items()
             if lab["category"] == CTL}
    for lid, (pi, poly, label) in lines.items():
        cat = label["category"]
        if cat == CTL:
            i = _ctl_issue(ctx, lid, label, pi, poly, geoms, stats)
        elif stats is not None and stats.get("width_usable", True):
            i = _width_issue(ctx, label, pi, poly, stats)
        else:
            i = None
        if i is not None:
            issues.append(i)
    return issues
