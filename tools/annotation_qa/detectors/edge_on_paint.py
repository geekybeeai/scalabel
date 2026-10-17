"""Edge-on-paint detector: a road edge drawn ON a painted line instead of the curb.

The user's rule: on a road divider (raised median / island) the curb outline follows
the island's curb edge INSIDE the white (or yellow) edge lines, not on them. The
``curb_road_edge`` / ``without_curb_road_edge`` belongs beyond the paint, on the
NON-road side. ``edge_position`` cannot see this: road -> paint is the strongest step.
Small refinements of an edge already at the curb are not wanted (MIN_MEDIAN_OFF).

Each poly is resampled at 3 px and judged in stretches of 20 samples (stride 10):

* Context (``_context``): road side and "divider". A ring's or an almost closed
  island outline's outside is road (and it is a divider). Else the lane-line labels
  beside the edge decide; else two appearance cues must agree (the darker / non-grass
  side BEYOND the paint, and ``edge_position.road_side``). A divider also has lane
  lines within DIVIDER_R on its non-road side. ``u`` = px away from the road.
* Paint at the line: the stretch-mean paint ridge (``imaging.profile``, white and
  yellow; the stronger channel wins) >= PAINT within -NEAR..+FAR px (u) of the line:
  on the paint, or on the asphalt just road-ward of it.
* Along the line, not across it (crosswalks, stop bars, arrows): >= ROW_FRAC of the
  stretch's rows must have paint at the line, and the per-row ridge peak must stay put
  (std <= PEAK_STD, first-half vs second-half drift <= PEAK_DRIFT px).
* On the paint, not beside it: the paint centre (sub-px ridge peak) must not lie
  road-ward of the line by more than -CENTRE_MIN px. A curb line on the curb side of
  its paint (the paint's far edge) is right.
* Paint, not concrete: a stripe wider than MAX_WIDTH (half-max edges: a curb band) or
  not WHITER than the material beyond it (a gutter band next to a sidewalk) is skipped.
* Target (the curb): the stripe's far half-max edge when the cell just beyond it is not
  road (gray / sat / green distance to the stretch's road strip > FLANK_D,
  ``edge_position``'s measure without texture); otherwise (asphalt between paint and
  curb) the first rise to non-road cells beyond, up to MAX_OFF px. No target: a lane
  line, not judged. Off a divider a WHITE stripe right against the curb is not judged
  either (it may be the curb's own white top); yellow paint and paint with asphalt
  between it and the curb are.
* A label is reported with >= MIN_ON on-paint stretches that make up >= MIN_FRAC of
  its judged stretches and a median target offset > MIN_MEDIAN_OFF px. Consistent
  targets (std <= SHIFT_STD_MAX) give a ``shift`` (position's smoothing) unless
  ``partial_shift`` cannot represent it or the moved outline folds over itself (a
  narrow island's nose); the rest a hand fix.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
from scipy.spatial import cKDTree

from annotation_qa.context import FrameContext
from annotation_qa.data import iter_polys
from annotation_qa.detectors.edge_position import (GREEN_MIN, PAINT_FLANK_D, PLANES, _dist,
                                                   _outward, _stretch_mean, _strip_stats,
                                                   road_side, stretch_ref)
from annotation_qa.detectors.edge_position import E as EP_E
from annotation_qa.detectors.position import STRETCH, _starts, smoothed_offsets
from annotation_qa.geometry import ShiftNotRepresentable, dense, length, partial_shift, resample
from annotation_qa.imaging import edge_profile, profile
from annotation_qa.issues import Issue

ISSUE = "edge_on_paint"
CATEGORIES = ("curb_road_edge", "without_curb_road_edge")

R = 25  # imaging.profile half width
NEAR = 2  # paint within +-2 px of the line is under it ...
FAR = 4  # ... and so is paint up to 4 px beyond it (the line on the paint's road-side edge)
PAINT = 25.0  # paint ridge threshold (position's)
LOC = 4  # the paint centre is searched within -LOC <= u <= FAR + 1
ROW_FRAC = 0.6
PEAK_STD, PEAK_DRIFT = 1.5, 2.0
CENTRE_MIN = -1.0  # paint centre further road-ward than this: the line is on the curb side
EDGE_SPAN = 10  # far paint edge within this many px of the paint centre
MIN_CONTRAST = 20.0
FLANK_D = PAINT_FLANK_D
MAX_OFF = 15
MIN_OFF = 3  # offsets of 3 px or less are never issues
MIN_ON = 5
MIN_FRAC = 0.4
SHIFT_STD_MAX = 3.0
RING_GAP = 40.0  # an open poly whose ends are this close (px) is an island outline
RING_MIN_LEN = 200.0
SIDE_STRIP = (10, 28)  # road-side strips, beyond the paint
SIDE_GRAY = 15.0  # a side this much darker is the road
WHITER = 30.0  # paint is this much whiter (raw score) than the kerb / island beyond it
MAX_WIDTH = 9.0  # px between the stripe's half-max edges: wider bands are concrete curbs
MIN_MEDIAN_OFF = 4.0  # a label's median move: smaller ones are refinements, not mistakes
LANE_CATS = ("solid_single_line", "dashed_white_line", "double_yellow_line",
             "center_turn_lane")
LANE_R = 300.0  # px: lane-line labels this close vote for the road side
LANE_MIN_VOTES, LANE_AGREE = 5, 0.75
LANE_GAP, LANE_ALONG = 12.0, 60.0
DIVIDER_R, DIVIDER_FRAC = 150.0, 0.5


def _lane_points(frame: dict) -> np.ndarray:
    """Points (about every 3 px) of the frame's painted-lane labels: they lie on the road."""
    pts = []
    for label, _pi, poly in iter_polys(frame):
        if label.get("category") in LANE_CATS:
            q = dense(poly)
            if len(q) >= 2 and np.isfinite(q).all():
                Pq, _n = resample(q)
                if Pq is not None:
                    pts.append(Pq)
    return np.concatenate(pts) if pts else np.zeros((0, 2))


def _lane_votes(P: np.ndarray, N: np.ndarray, lanes) -> np.ndarray:
    """(n, 3) per sample (every 5th): sign of the nearest lane point roughly abreast
    (|along| <= LANE_ALONG) and LANE_GAP..LANE_R px off -- a label on the very paint
    the edge is drawn on does not vote -- (0 = none), then whether there is one within
    DIVIDER_R on the +normal / the -normal side."""
    if lanes is None:
        return np.zeros((0, 3))
    tree, Q = lanes
    T = np.stack([N[:, 1], -N[:, 0]], axis=1)
    out = []
    for i in range(0, len(P), 5):
        idx = tree.query_ball_point(P[i], LANE_R)
        if not idx:
            out.append((0, 0, 0))
            continue
        v = Q[idx] - P[i]
        d, al = v @ N[i], v @ T[i]
        ok = (np.abs(d) >= LANE_GAP) & (np.abs(al) <= LANE_ALONG)
        if not ok.any():
            out.append((0, 0, 0))
            continue
        dd = d[ok]
        out.append((np.sign(dd[np.argmin(np.abs(dd))]),
                    float(((dd > 0) & (dd <= DIVIDER_R)).any()),
                    float(((dd < 0) & (dd >= -DIVIDER_R)).any())))
    return np.asarray(out, float).reshape(-1, 3)


def _lane_side(votes: np.ndarray) -> int | None:
    """Road side from the nearest lane-line labels beside the edge (``_lane_votes``)."""
    sg = votes[:, 0][votes[:, 0] != 0] if len(votes) else votes
    if len(sg) < LANE_MIN_VOTES:
        return None
    plus = float(np.mean(sg > 0))
    if plus >= LANE_AGREE:
        return 1  # lanes on the +normal (right) side: that side is road
    if plus <= 1 - LANE_AGREE:
        return 0
    return None


def _ring(poly: dict) -> int | None:
    """Road side of a ring or an almost closed island outline (its outside); None for
    other polys. The shoelace area is taken over the CLOSED outline (an open sum is not
    translation invariant)."""
    P = dense(poly)
    if len(P) < 3 or not np.isfinite(P).all():
        return None
    if not poly.get("closed") and (length(P) < RING_MIN_LEN
                                   or float(np.linalg.norm(P[0] - P[-1])) > RING_GAP):
        return None
    Q = np.vstack([P, P[:1]])
    area = 0.5 * float(np.sum(Q[:-1, 0] * Q[1:, 1] - Q[1:, 0] * Q[:-1, 1]))
    return 0 if area > 0 else 1  # positive: interior on +normal, road on the left


def _context(ep, poly: dict, lanes=None) -> tuple[int | None, bool]:
    """(road side, divider). Side: 0 = the -normal (left) side is road, 1 = the right;
    None when undecided.

    A ring's (or an almost closed island outline's) outside is road. Else the lane-line
    labels beside the edge decide when they agree; else the two appearance cues must
    agree: the sides compared BEYOND the paint (SIDE_STRIP px out, so a bright stripe
    next to the line cannot make its own side look like the kerb: grass is never road,
    a clearly darker side is) and ``edge_position.road_side``.

    Divider: a ring / island outline, or lane lines within DIVIDER_R on the non-road side
    too for >= DIVIDER_FRAC of the samples (a median between two carriageways)."""
    ring = _ring(poly)
    if ring is not None:
        return ring, True
    votes = _lane_votes(ep.P, ep.Nrm, lanes)
    side = _lane_side(votes)
    if side is None:
        app = None
        left, right = _strip_stats(ep, *SIDE_STRIP, -1), _strip_stats(ep, *SIDE_STRIP, 1)
        if left is not None and right is not None:
            lg, rg = left.green > GREEN_MIN, right.green > GREEN_MIN
            if lg != rg:
                app = 1 if lg else 0
            elif abs(left.gray - right.gray) >= SIDE_GRAY:
                app = 0 if left.gray < right.gray else 1
        rs = road_side(ep, poly)
        side = app if app is not None and app == rs else None
    if side is None:
        return None, False
    beyond = votes[:, 2 if side == 1 else 1] if len(votes) else votes
    return side, bool(len(beyond)) and float(np.mean(beyond)) >= DIVIDER_FRAC


def _u(arr: np.ndarray, sign: int) -> np.ndarray:
    return arr if sign == 1 else arr[:, ::-1]


def _peak(pm: np.ndarray) -> float:
    """Sub-px offset (u) of the ridge maximum within -LOC <= u <= FAR + 1."""
    w = np.nan_to_num(pm[R - LOC:R + FAR + 2], nan=-1e9)
    k = int(np.argmax(w))
    u = float(k - LOC)
    if 0 < k < len(w) - 1 and min(w[k - 1], w[k + 1]) > -1e8:
        den = w[k - 1] - 2 * w[k] + w[k + 1]
        if den < 0:
            u += float(np.clip(0.5 * (w[k - 1] - w[k + 1]) / den, -0.5, 0.5))
    return u


def _crossing(s: np.ndarray, k: int, step: int) -> float | None:
    """Half-max crossing of ``s`` from its peak index ``k`` going ``step`` (+1 away from
    the road, -1 toward it) between the peak and the lowest cell within EDGE_SPAN + 2;
    u (index R = 0), None without a clear stripe edge."""
    stop = k + step * (EDGE_SPAN + 3)
    tail = s[k + 1:min(stop, len(s))] if step > 0 else s[max(stop + 1, 0):k]
    if not np.isfinite(tail).any():
        return None
    top, base = float(s[k]), float(np.nanmin(tail))
    if top - base < MIN_CONTRAST:
        return None
    half = (top + base) / 2
    for i in range(1, EDGE_SPAN + 1):
        j = k + step * i
        if not 0 <= j < len(s) or not np.isfinite(s[j]):
            return None
        if s[j] <= half:
            frac = (s[j - step] - half) / max(s[j - step] - s[j], 1e-6)
            return float(j - step + step * frac - R)
    return None


def _stripe(s: np.ndarray, u_p: float):
    """(road-side edge, far edge, peak index) of the paint stripe of the raw score ``s``
    around the ridge peak ``u_p``; edges None when unclear."""
    c = int(round(u_p)) + R
    lo, hi = max(c - 1, 0), min(c + 2, len(s))
    if not np.isfinite(s[lo:hi]).any():
        return None, None, c
    k = lo + int(np.nanargmax(s[lo:hi]))
    return _crossing(s, k, -1), _crossing(s, k, 1), k


def _target(d: np.ndarray, u_e: float) -> tuple[float, bool] | None:
    """The curb beyond a paint stripe whose far edge is at ``u_e``: the first rise to
    non-road cells (> FLANK_D over the next 4 cells) beyond the paint, up to MAX_OFF px.
    When the very next cell is non-road the curb is the paint edge itself; otherwise
    (asphalt between paint and curb) the steepest rise of ``d`` there. ``d`` = distance
    to the road strip, u order, index EP_E = 0. Returns (u, curb right at the paint);
    None when there is no such rise."""
    j0 = int(math.ceil(u_e)) + EP_E
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for j in range(j0 + 1, min(MAX_OFF + EP_E + 1, len(d) - 4)):
            if not np.isfinite(d[j]):
                return None
            if d[j] > FLANK_D and np.nanmean(d[j:j + 4]) > FLANK_D:
                if j == j0 + 1:
                    return u_e, True
                rise = d[j - 2:j + 2] - d[j - 3:j + 1]
                if not np.isfinite(rise).any():
                    return float(j - EP_E) - 0.5, False
                return float(j - 3 + int(np.nanargmax(rise)) - EP_E) + 0.5, False
    return None


def judge_stretch(ridges: dict, raws: dict, eplanes: list | None, divider: bool = True):
    """One stretch. ``ridges`` / ``raws``: channel -> (rows, 2R+1) u order; ``eplanes``:
    edge_profile stretch-mean planes (u order) or None. Returns None when unscorable,
    else a dict with ``on`` (the line is on paint) and, when on, ``u_p`` (paint centre),
    ``target`` (u of the curb) and ``channel``. Off a divider a white stripe right
    against the kerb is not judged: it may be the kerb's own (white) top."""
    best = None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for ch, seg in ridges.items():
            pm = np.nanmean(seg, axis=0)
            w = pm[R - NEAR:R + FAR + 1]
            if np.isnan(w).all():
                continue
            near = float(np.nanmax(w))
            if best is None or near > best[1]:
                best = (ch, near, seg, pm)
        if best is None:
            return None
        ch, near, seg, pm = best
        if near < PAINT:
            return {"on": False, "why": "no_paint", "near": near}
        rn = np.nanmax(seg[:, R - NEAR:R + FAR + 1], axis=1)
    fin = np.isfinite(rn)
    on_rows = fin & (rn >= PAINT)
    if on_rows.sum() < max(ROW_FRAC * fin.sum(), 3):
        return {"on": False, "why": "across", "near": near}
    pk = np.argmax(np.nan_to_num(seg[on_rows, R - LOC:R + FAR + 2], nan=-1e9), axis=1) - LOC
    h = len(pk) // 2
    if pk.std() > PEAK_STD or abs(pk[:h].mean() - pk[h:].mean()) > PEAK_DRIFT:
        return {"on": False, "why": "across", "near": near}
    u_p = _peak(pm)
    if u_p < CENTRE_MIN:
        return {"on": False, "why": "curb_side", "near": near, "u_p": u_p}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        s = np.nanmean(raws[ch], axis=0)
    u_r, u_e, k = _stripe(s, u_p)
    if u_e is not None and u_r is not None and u_e - u_r > MAX_WIDTH:
        return {"on": False, "why": "wide", "near": near, "u_p": u_p}  # a curb band
    if u_e is not None:  # paint, not a concrete gutter / curb band: whiter than beyond
        lo = R + int(math.ceil(u_e)) + 3
        beyond = s[lo:min(lo + 10, len(s))]  # median: a narrow island's far paint is not it
        if np.isfinite(beyond).sum() >= 3 and float(s[k]) - float(np.nanmedian(beyond)) < WHITER:
            return {"on": False, "why": "not_whiter", "near": near, "u_p": u_p}
    hit = None
    if u_e is not None and eplanes is not None:
        ref = stretch_ref(*eplanes[:4])
        if ref is not None:
            g, sa, _t, gn = eplanes[:4]
            # no texture term: a 5x5 texture window lights up every cell of a narrow
            # asphalt gap between paint and curb
            d = _dist(g, sa, gn, ref[0], ref[1], ref[3])
            hit = _target(d, u_e)
    if hit is None or not (0 < hit[0] <= MAX_OFF):  # no curb beyond: a lane line
        return {"on": False, "why": "no_curb", "near": near, "u_p": u_p}
    target, adjacent = hit
    if adjacent and ch == "white" and not divider:
        return {"on": False, "why": "white_band", "near": near, "u_p": u_p}
    return {"on": True, "near": near, "u_p": u_p, "u_e": u_e, "target": target, "channel": ch}


def _judge_poly(poly: dict, img, rows_sel: np.ndarray | None = None,
                ctx: tuple[int, bool] | None = None, lanes=None):
    """Stretch verdicts of one poly; ``ctx`` = (road side, divider) to reuse (the
    verifier's after line), else ``_context``."""
    pr = profile(img, poly, R=R, raw=True)
    ep = edge_profile(img, poly, E=EP_E)
    if pr is None or ep is None:
        return None
    side, divider = _context(ep, poly, lanes) if ctx is None else ctx
    if side is None:
        return None
    sign, eplanes = _outward(ep, side)
    ridges = {"white": _u(pr.white, sign), "yellow": _u(pr.yellow, sign)}
    raws = {"white": _u(pr.white_raw, sign), "yellow": _u(pr.yellow_raw, sign)}
    m = len(pr.P)
    if rows_sel is None:
        spans = [(a, min(a + STRETCH, m)) for a in _starts(m)]
        idx = None
    else:  # the verifier: stretches over the selected rows only
        idx = np.flatnonzero(rows_sel[:m])
        spans = [(a, min(a + STRETCH, len(idx))) for a in range(0, len(idx), STRETCH)]
    rows = []
    for a, b in spans:
        sel = slice(a, b) if idx is None else idx[a:b]
        if (b - a) < 3:
            continue
        r = judge_stretch({k: v[sel] for k, v in ridges.items()},
                          {k: v[sel] for k, v in raws.items()},
                          [_stretch_mean(eplanes[k][sel]) for k in PLANES], divider)
        if r is not None:
            rows.append((a, b, r))
    return {"rows": rows, "side": side, "divider": divider, "sign": sign, "m": m,
            "P": pr.P}


def _crossings(poly: dict, step: float = 6.0) -> int:
    """Number of crossings between non-neighbouring segments of the poly (resampled at
    ``step`` px; a closed poly includes its closing segment)."""
    P = dense(poly)
    if len(P) < 4 or not np.isfinite(P).all():
        return 0
    P, _n = resample(P, step)
    if P is None or len(P) < 4:
        return 0
    if poly.get("closed") and np.linalg.norm(P[0] - P[-1]) > 1e-6:
        P = np.vstack([P, P[:1]])
    A, B = P[:-1], P[1:]
    n = len(A)

    def orient(p, q, r):
        return np.sign((q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1])
                       - (q[..., 1] - p[..., 1]) * (r[..., 0] - p[..., 0]))

    count = 0
    for i0 in range(0, n, 256):
        a, b = A[i0:i0 + 256, None], B[i0:i0 + 256, None]
        c, d = A[None], B[None]
        hit = ((orient(a, b, c) * orient(a, b, d) < 0)
               & (orient(c, d, a) * orient(c, d, b) < 0))
        ii = np.arange(i0, min(i0 + 256, n))[:, None]
        jj = np.arange(n)[None]
        far = (jj > ii + 1) & ~((ii == 0) & (jj == n - 1))
        count += int((hit & far).sum())
    return count


def _summarise(j: dict, poly: dict):
    rows = j["rows"]
    if not rows:
        return None
    on = [(a, b, r) for a, b, r in rows if r["on"]]
    if not on:
        return None
    tg = [(a, b, r["target"]) for a, b, r in on if r["target"] is not None]
    offs = np.array([t for *_x, t in tg], float)
    med = float(np.median(offs)) if len(offs) else float("nan")
    std = float(offs.std()) if len(offs) else float("nan")
    chans = [r["channel"] for *_x, r in on]
    channel = max(set(chans), key=chans.count)
    frac = len(on) / len(rows)
    consistent = len(tg) == len(on) and len(offs) > 0 and std <= SHIFT_STD_MAX
    smooth, coverage = smoothed_offsets(j["m"], [(a, b, j["sign"] * t) for a, b, t in tg])
    if consistent:
        try:
            moved = partial_shift(poly, smooth)
        except ShiftNotRepresentable:
            consistent = False
        else:  # a narrow island's outline moved inward must not fold over itself
            consistent = _crossings(moved) <= _crossings(poly)
    a, b, _r = on[len(on) // 2]
    centre = j["P"][min((a + b) // 2, j["m"] - 1)]
    why = [r.get("why") for *_x, r in rows if not r["on"]]
    return {
        "anchor": (float(centre[0]), float(centre[1])),
        "consistent": consistent, "offsets": smooth.tolist(),
        "features": {"on_paint_frac": frac, "n_on_paint": len(on), "n_stretches": len(rows),
                     "n_targets": len(tg), "median_off": med, "off": med, "offset_std": std,
                     "channel": channel, "road_side": j["side"], "divider": j["divider"],
                     "coverage": coverage, "n_white_band": why.count("white_band"),
                     "n_wide": why.count("wide"),
                     "n_across": why.count("across"), "n_curb_side": why.count("curb_side"),
                     "n_no_curb": why.count("no_curb"), "n_not_whiter": why.count("not_whiter"),
                     "mean_near": float(np.mean([r["near"] for *_x, r in on]))},
    }


def keep_issue(issue: Issue) -> bool:
    """The reporting rule on a (stored) edge_on_paint issue; uses only its features."""
    return _keep(issue.features)


def _keep(f: dict) -> bool:
    med = f.get("median_off")
    return (f.get("n_on_paint", 0) >= MIN_ON and f.get("on_paint_frac", 0.0) >= MIN_FRAC
            and med is not None and np.isfinite(med) and med > max(MIN_OFF, MIN_MEDIAN_OFF))


def detect(ctx: FrameContext) -> list[Issue]:
    stem = ctx.export.stem
    frame_name = ctx.frame.get("name", "")
    best: dict[str, tuple[float, int, dict, dict]] = {}
    lanes = None
    for label, pi, poly in iter_polys(ctx.frame):
        if label.get("category") not in CATEGORIES:
            continue
        if lanes is None:
            Q = _lane_points(ctx.frame)
            lanes = (cKDTree(Q), Q) if len(Q) else False
        j = _judge_poly(poly, ctx.image, lanes=lanes or None)
        r = _summarise(j, poly) if j is not None else None
        if r is None:
            continue
        f = r["features"]
        conf = float(np.clip(f["on_paint_frac"], 0, 1))
        if not _keep(f):
            continue
        if label["id"] not in best or conf > best[label["id"]][0]:
            best[label["id"]] = (conf, pi, r, label)
    issues = []
    for lid, (conf, pi, r, label) in best.items():
        f = {**r["features"], "poly_index": pi}
        what = (f"{label['category']} drawn on a {f['channel']} painted line on "
                f"{f['n_on_paint']} of {f['n_stretches']} stretches; the curb is "
                f"{f['median_off']:.1f} px further from the road")
        if r["consistent"]:
            fix, hand, reason = ({"op": "shift", "poly_index": pi, "offsets": r["offsets"]},
                                 False, what)
        else:
            fix, hand, reason = (None, True,
                                 what + "; edge drawn on painted line; move it onto the curb")
        issues.append(Issue(
            issue_type=ISSUE, export_stem=stem, frame_name=frame_name, label_id=lid,
            category=label["category"], confidence=conf, reason=reason, anchor=r["anchor"],
            fix=fix, hand_fix=hand, features=f))
    return issues


def line_score(img, poly: dict, rows: np.ndarray | None = None,
               ctx: tuple[int, bool] | None = None, frame: dict | None = None):
    """(share of the judged stretches of ``rows`` whose line is on paint with the curb
    > MIN_OFF px beyond it, (road side, divider)). Used by the verifier before / after a
    shift, reusing the before line's context; (nan, ctx) when unscorable."""
    lanes = None
    if ctx is None and frame is not None:
        Q = _lane_points(frame)
        lanes = (cKDTree(Q), Q) if len(Q) else None
    j = _judge_poly(poly, img, rows_sel=rows, ctx=ctx, lanes=lanes)
    if j is None or not j["rows"]:
        return float("nan"), (ctx if j is None else (j["side"], j["divider"]))
    bad = [r for *_x, r in j["rows"] if r["on"] and r["target"] > MIN_OFF]
    return len(bad) / len(j["rows"]), (j["side"], j["divider"])
