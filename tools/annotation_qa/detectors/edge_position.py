"""Edge-position detector: road-edge labels that are not ON the road edge.

``curb_road_edge`` belongs on the asphalt -> curb boundary (the gutter / curb face
line), ``without_curb_road_edge`` where asphalt meets dirt, gravel or grass. Each
poly is resampled at 3 px and judged in stretches of 20 samples (stride 10), like
``position``:

* Road side (per poly): a closed ring's outside, else curb_soft's vote (darker,
  less saturated, smoother strip of the two 6..24 px side strips; smoothness is the
  median local texture), except that a green (grass) strip opposite a non-green one
  is never the road.
* Across-line profiles over offsets -28..+28 px (``imaging.edge_profile``: gray,
  saturation, local texture, paint ridge; NaN outside ROI / canvas). Offsets are
  turned into ``u`` = px outward (away from the road).
* Road-likeness of a cell = minus its distance to the stretch's own road surface
  (median of u in -28..-14): gray brighter than the road + 0.3 x gray darker than it
  (shadows, kerb-face shadow lines, wet gutters are not another surface) + |sat - ref|
  + |greenness - ref| + max(texture - ref, 0). Real asphalt is not always the least
  saturated surface, so a fixed "dark, grey, smooth" score is replaced by "looks like
  this road". A stretch whose road strip differs from the poly's median road by
  > REF_TOL (gray + sat) is occluded (parked car, tree, deep shadow) and not judged.
* Boundary strength S(u) = mean distance outside (u+2..u+8) - inside (u-8..u-2),
  plus GRAY_W x gray step (darker outside weighted 0.3), kept only when the outside
  is LESS road-like (so the road stays on its side); NaN when a window has < 3 valid
  cells.
* Painted lines: a paint-ridge run (>= 25) with road-like cells on both flanks is a
  line painted on the road; its cells (+-3 px) are skipped by the windows (which take
  the nearest kept cells instead) so it can neither attract the line nor hide the
  curb behind it, and no candidate may sit inside a ridge >= 25.
* Candidate: local maxima of S within |u| <= 19; of those >= max(FLOOR, 0.7 x best)
  the most road-ward one wins (the curb face, not the back of the curb band or the
  sidewalk); the boundary is the steepest outward rise of the distance within +-4 px
  of the centre of its >= 90 % plateau.
* A stretch is flagged when best >= RATIO x near (near = max S within +-2 px),
  best >= FLOOR and |off| > 3. Only flagged stretches in runs of >= 3 neighbours whose
  offsets change by <= 3 px count; a poly is reported with >= 5 of them (R26).
  Consistent offsets (std <= 3 px) become a ``shift`` (position's smoothing) unless
  ``partial_shift`` cannot represent it; the rest a hand fix.

The 2..8 px windows mean a line closer than ~8 px to a sharp boundary is never
flagged (near still sees part of the step).
"""

from __future__ import annotations

import warnings
from types import SimpleNamespace

import numpy as np

from annotation_qa.context import FrameContext
from annotation_qa.data import iter_polys
from annotation_qa.detectors.curb_soft import _cad_for_ctx, _ring_road_side, _road_side
from annotation_qa.detectors.position import STRETCH, STRIDE, _starts, smoothed_offsets
from annotation_qa.geometry import ShiftNotRepresentable, partial_shift
from annotation_qa.imaging import edge_profile
from annotation_qa.issues import Issue

CURB, SOFT = "curb_road_edge", "without_curb_road_edge"
CATEGORIES = (CURB, SOFT)
PLANES = ("gray", "sat", "texture", "green", "paint")  # EdgeProfile planes, in strength() order

R = 20  # candidate offsets -R..R
W_NEAR, W_FAR = 2, 8  # boundary windows: u+2..u+8 outside, u-8..u-2 inside
E = R + W_FAR  # profile half width
REF = (-28, -14)  # road reference strip (u)
STRIP = (6, 24)  # road-side vote strips (px each side), curb_soft's vote
MIN_WIN = 3  # valid cells per window
MIN_ROW_SHARE = 0.5  # a stretch offset needs this share of valid rows
GRAY_W = 0.5
DARK_W = 0.3  # weight of "darker than the road" (shadows) vs brighter / other colour
REF_TOL = 30.0  # gray + sat distance of a stretch's road strip from the edge's median road
MIN_RUN, RUN_STEP = 3, 3.0
PAINT_RIDGE = 25.0  # imaging.profile ridge of painted lines
PAINT_PAD = 3
PAINT_FLANK = (3, 6)  # flank cells beyond a paint run
PAINT_FLANK_D = 20.0  # a flank this close to the road reference is road
FLOOR = 40.0
RATIO = 1.8
STRONG_FRAC = 0.7
PLATEAU = 0.9
NEAR_R = 2
MIN_OFF = 3  # offsets of 3 px or less are never issues
MIN_FLAGGED_STRETCHES = 5  # R26
SHIFT_STD_MAX = 3.0
ROAD_GRAY_MAX = 190
GREEN_MIN = 8.0  # curb_soft's grass test: G - (R+B)/2 above this
CONF_SCALE = 60.0


def _strip_stats(ep, lo: int, hi: int, sign: int):
    cols = (ep.offsets * sign >= lo) & (ep.offsets * sign <= hi)
    g, s, t, gr = ep.gray[:, cols], ep.sat[:, cols], ep.texture[:, cols], ep.green[:, cols]
    ok = np.isfinite(g)
    if ok.sum() < 5:
        return None
    # texture = median LOCAL texture: a strip that straddles the boundary (line off on
    # the far side) must not look rough just because it holds two surfaces
    return SimpleNamespace(gray=float(g[ok].mean()), sat=float(s[ok].mean()),
                           texture=float(np.median(t[ok])), green=float(np.mean(gr[ok])))


def road_side(ep, poly: dict) -> int | None:
    """0 = the -normal (left) side is road, 1 = the right; None when undecided."""
    if poly.get("closed"):
        return _ring_road_side(poly)
    left, right = _strip_stats(ep, *STRIP, -1), _strip_stats(ep, *STRIP, 1)
    if left is None or right is None:
        return None
    lg, rg = left.green > GREEN_MIN, right.green > GREEN_MIN
    if lg != rg:  # grass is never the road (blue-tinted asphalt can out-vote it)
        return 1 if lg else 0
    side = _road_side(left, right)
    if side is None or (left, right)[side].gray > ROAD_GRAY_MAX:
        return None
    return side


def _outward(ep, side: int):
    """(sign, {plane: (M, 2E+1) in u order}); u = px away from the road."""
    sign = 1 if side == 0 else -1  # road left -> outside is +N
    sl = slice(None) if sign == 1 else slice(None, None, -1)
    return sign, {k: getattr(ep, k)[:, sl] for k in PLANES}


def _stretch_mean(arr: np.ndarray) -> np.ndarray:
    ok = np.isfinite(arr)
    n = ok.sum(axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        m = np.nanmean(arr, axis=0)
    return np.where(n >= MIN_ROW_SHARE * len(arr), m, np.nan)


def _windows(v: np.ndarray, keep: np.ndarray):
    """(inside, outside) nan-means for u in -R..R over the WIDTH nearest kept cells at
    u-2 and below / u+2 and above. Cells not in ``keep`` (paint on the road) are
    skipped as if absent; NaN cells (ROI) still use up their place. NaN with < MIN_WIN
    valid cells."""
    width = W_FAR - W_NEAR + 1
    kept = np.flatnonzero(keep)
    us = np.arange(-R, R + 1) + E  # array index of u
    j_in = np.searchsorted(kept, us - W_NEAR, side="right")  # kept[:j_in] <= u-2
    j_out = np.searchsorted(kept, us + W_NEAR, side="left")  # kept[j_out:] >= u+2
    k = np.arange(width)[None, :]
    res = []
    for idx in (j_in[:, None] - 1 - k, j_out[:, None] + k):
        ok = (idx >= 0) & (idx < len(kept))
        if not len(kept):
            return np.full(len(us), np.nan), np.full(len(us), np.nan)
        cells = kept[np.clip(idx, 0, len(kept) - 1)]
        vals = np.where(ok, v[cells], np.nan)
        fin = np.isfinite(vals)
        n = fin.sum(axis=1)
        res.append(np.where(n >= MIN_WIN, np.where(fin, vals, 0).sum(axis=1) / np.maximum(n, 1),
                            np.nan))
    return res[0], res[1]


def _paint_runs(core: np.ndarray) -> list[tuple[int, int]]:
    runs, i = [], 0
    while i < len(core):
        if core[i]:
            j = i
            while j + 1 < len(core) and core[j + 1]:
                j += 1
            runs.append((i, j))
            i = j + 1
        else:
            i += 1
    return runs


def stretch_ref(g, s, t, gn):
    """Road reference (gray, sat, texture, green) of a stretch: medians over u in REF;
    None if empty."""
    u = np.arange(-E, E + 1)
    ref = (u >= REF[0]) & (u <= REF[1])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        r = tuple(float(np.nanmedian(v[ref])) for v in (g, s, t, gn))
    return r if np.isfinite(r).all() else None


def _dist(g, s, gn, gr, sr, gnr):
    """Gray + sat + greenness distance to the road; darker-than-road counts DARK_W
    (shadows, kerb-face shadow lines and wet gutters are not a different surface)."""
    return (np.maximum(g - gr, 0) + DARK_W * np.maximum(gr - g, 0) + np.abs(s - sr)
            + np.abs(gn - gnr))


def strength(g, s, t, gn, p, ref=None):
    """(S, core, appearance) for one stretch from its mean profiles (u order, length
    2E+1). S and core cover u in -R..R: S is the boundary strength, core marks paint
    ridges >= PAINT_RIDGE. ``appearance`` (2E+1) is the colour distance to the
    road reference with paint-on-road cells NaN, used to place the boundary. None
    without a road reference."""
    u = np.arange(-E, E + 1)
    ref = ref if ref is not None else stretch_ref(g, s, t, gn)
    if ref is None:
        return None
    gr, sr, tr, gnr = ref
    look = _dist(g, s, gn, gr, sr, gnr)
    d = look + np.maximum(t - tr, 0)
    core = np.nan_to_num(p, nan=-1e9) >= PAINT_RIDGE
    keep = np.ones(len(u), bool)
    for a, b in _paint_runs(core):
        fl = d[max(a - PAINT_FLANK[1], 0):max(a - PAINT_FLANK[0] + 1, 0)]
        fr = d[b + PAINT_FLANK[0]:b + PAINT_FLANK[1] + 1]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            road_both = (fl.size and fr.size and np.nanmean(fl) <= PAINT_FLANK_D
                         and np.nanmean(fr) <= PAINT_FLANK_D)
        if road_both:  # a line painted on the road surface: invisible to the windows
            keep[max(a - PAINT_PAD, 0):b + PAINT_PAD + 1] = False
    din, dout = _windows(d, keep)
    gin, gout = _windows(g, keep)
    delta = dout - din
    step = np.maximum(gout - gin, 0) + DARK_W * np.maximum(gin - gout, 0)
    S = np.where(np.isfinite(delta), np.where(delta > 0, delta + GRAY_W * step, 0.0), np.nan)
    return S, core[E - R:E + R + 1], np.where(keep, look, np.nan)


def _place(look: np.ndarray, k: int) -> float:
    """Boundary near candidate u=k: the steepest outward rise of ``look`` within +-4 px."""
    j = np.arange(max(k - 4, -R), min(k + 4, R - 1)) + E  # the boundary stays inside -R..R
    rise = look[j + 1] - look[j]
    if not np.isfinite(rise).any():
        return float(k)
    return float(j[int(np.nanargmax(rise))] - E) + 0.5


def judge_stretch(S: np.ndarray, core: np.ndarray, look: np.ndarray | None = None):
    """(near, best, off_u) for one stretch, or None when the line itself cannot be scored."""
    c = R  # u = 0
    w = S[c - NEAR_R:c + NEAR_R + 1]
    if np.isnan(w).all():
        return None
    near = float(np.nanmax(w))
    cand = np.where(core, np.nan, S)
    cand[0] = cand[-1] = np.nan  # the window edge is not a local maximum
    if np.isnan(cand).all():
        return near, float("nan"), 0.0
    top = float(np.nanmax(cand))
    thr = max(FLOOR, STRONG_FRAC * top)
    v = np.nan_to_num(cand, nan=-1e9)
    for k in range(1, len(v) - 1):  # most road-ward strong local maximum
        if v[k] >= thr and v[k] >= v[k - 1] and v[k] >= v[k + 1]:
            lo = hi = k
            while lo > 0 and v[lo - 1] >= PLATEAU * v[k]:
                lo -= 1
            while hi < len(v) - 1 and v[hi + 1] >= PLATEAU * v[k]:
                hi += 1
            off = (lo + hi) / 2 - c
            if look is not None:
                off = _place(look, int(round(off)))
            return near, float(v[k]), float(off)
    return near, top, 0.0


def _in_runs(flagged: list) -> list:
    """Flagged stretches that belong to a run of >= MIN_RUN neighbouring stretches whose
    offsets change by <= RUN_STEP px from one to the next: a misplaced edge is off over
    a length, a parked car or a patch only for a stretch or two."""
    out, run = [], []
    for f in flagged:
        if run and f[0] - run[-1][0] <= STRIDE and abs(f[4] - run[-1][4]) <= RUN_STEP:
            run.append(f)
            continue
        if len(run) >= MIN_RUN:
            out += run
        run = [f]
    if len(run) >= MIN_RUN:
        out += run
    return out


def _judge_poly(cat: str, poly: dict, ctx: FrameContext):
    ep = edge_profile(ctx.image, poly, E=E)
    if ep is None:
        return None
    side = road_side(ep, poly)
    if side is None:
        return None
    sign, planes = _outward(ep, side)
    m = len(ep.P)
    profs = []
    for a in _starts(m):
        b = min(a + STRETCH, m)
        prof = [_stretch_mean(planes[k][a:b]) for k in PLANES]
        profs.append((a, b, prof, stretch_ref(*prof[:4])))
    refs = np.array([r for *_x, r in profs if r is not None], float)
    if not len(refs):
        return None
    G, Sa = np.median(refs[:, 0]), np.median(refs[:, 1])
    rows = []
    for a, b, prof, ref in profs:
        # a stretch whose road strip does not look like this edge's road is occluded
        # (parked car, tree, deep shadow): not judged
        if ref is None or abs(ref[0] - G) + abs(ref[1] - Sa) > REF_TOL:
            continue
        r = strength(*prof, ref=ref)
        if r is None:
            continue
        j = judge_stretch(*r)
        if j is not None:
            rows.append((a, b) + j)
    if not rows:
        return None
    raw = [r for r in rows if np.isfinite(r[3]) and r[3] >= FLOOR
           and r[3] >= RATIO * max(r[2], 0.0) and abs(r[4]) > MIN_OFF]
    flagged = _in_runs(raw)
    if not flagged:
        return None
    worst = max(flagged, key=lambda f: f[3] - f[2])
    offs_u = np.array([f[4] for f in flagged], float)
    smooth, coverage = smoothed_offsets(m, [(f[0], f[1], sign * f[4]) for f in flagged])
    std = float(offs_u.std())
    a, b = worst[0], worst[1]
    centre = ep.P[min((a + b) // 2, m - 1)]
    return {
        "conf": float(np.clip((worst[3] - worst[2]) / CONF_SCALE, 0, 1)),
        "anchor": (float(centre[0]), float(centre[1])),
        "consistent": std <= SHIFT_STD_MAX,
        "offsets": smooth.tolist(),
        "features": {"near": worst[2], "best": worst[3], "off": worst[4],
                     "n_flagged_stretches": len(flagged), "n_stretches": len(rows),
                     "coverage": coverage, "offset_std": std, "road_side": side,
                     "median_off": float(np.median(offs_u)), "n_raw_flagged": len(raw)},
    }


def keep_issue(issue: Issue) -> bool:
    """R26-style rule on a (stored) edge_position issue; uses only its features."""
    f = issue.features
    return (f.get("n_flagged_stretches", 0) >= MIN_FLAGGED_STRETCHES
            and f.get("best", 0.0) >= FLOOR and abs(f.get("off", 0)) > MIN_OFF)


def detect(ctx: FrameContext) -> list[Issue]:
    stem = ctx.export.stem
    frame_name = ctx.frame.get("name", "")
    best: dict[str, tuple[float, int, dict, dict]] = {}
    for label, pi, poly in iter_polys(ctx.frame):
        cat = label.get("category")
        if cat not in CATEGORIES:
            continue
        r = _judge_poly(cat, poly, ctx)
        if r is None or r["features"]["n_flagged_stretches"] < MIN_FLAGGED_STRETCHES:
            continue
        if r["consistent"]:  # fix.apply would refuse a shift it cannot represent
            try:
                partial_shift(poly, np.asarray(r["offsets"], float))
            except ShiftNotRepresentable:
                r["consistent"] = False
        if label["id"] not in best or r["conf"] > best[label["id"]][0]:
            best[label["id"]] = (r["conf"], pi, r, label)
    if not best:
        return []
    cad = _cad_for_ctx(ctx)
    cad_soft = None
    if cad is not None:
        try:
            cad_soft = cad(frame_name)
        except Exception:  # noqa: BLE001 - the CAD flag is informative only
            cad_soft = None
    issues = []
    for lid, (conf, pi, r, label) in best.items():
        f = {**r["features"], "poly_index": pi, "cad_soft": cad_soft}
        what = (f"{label['category']} is {abs(f['off'])} px off the road boundary "
                f"on {f['n_flagged_stretches']} of {f['n_stretches']} stretches")
        if r["consistent"]:
            fix, hand, reason = ({"op": "shift", "poly_index": pi, "offsets": r["offsets"]},
                                 False, what)
        else:
            fix, hand, reason = None, True, what + "; edge not on the road boundary; redraw"
        issues.append(Issue(
            issue_type="edge_position", export_stem=stem, frame_name=frame_name, label_id=lid,
            category=label["category"], confidence=conf, reason=reason, anchor=r["anchor"],
            fix=fix, hand_fix=hand, features=f))
    return [i for i in issues if keep_issue(i)]


def line_score(img, poly: dict, rows: np.ndarray | None = None, side: int | None = None):
    """(mean boundary strength AT the line, road side) over ``rows`` (bool per resampled
    point; all when None), in stretches of up to STRETCH selected points. Used by the
    verifier to compare a line before / after a shift; (nan, side) when unscorable."""
    ep = edge_profile(img, poly, E=E)
    if ep is None:
        return float("nan"), side
    side = road_side(ep, poly) if side is None else side
    if side is None:
        return float("nan"), None
    _sign, planes = _outward(ep, side)
    idx = np.flatnonzero(rows if rows is not None else np.ones(len(ep.P), bool))
    idx = idx[idx < len(ep.P)]
    vals = []
    for a in range(0, len(idx), STRETCH):
        sel = idx[a:a + STRETCH]
        if len(sel) < 3:
            continue
        prof = [_stretch_mean(planes[k][sel]) for k in PLANES]
        r = strength(*prof)
        if r is None:
            continue
        w = r[0][R - NEAR_R:R + NEAR_R + 1]
        if np.isfinite(w).any():
            vals.append(float(np.nanmax(w)))
    return (float(np.mean(vals)) if vals else float("nan")), side
