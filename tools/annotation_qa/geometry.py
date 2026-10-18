"""Curve geometry for polylines with cubic Bezier segments.

Dense sampling always goes through ``poly_to_points`` from
``scalabel/tools/draw_poly2d.py``; it is imported, never re-implemented.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import numpy as np

_DRAW_DIR = Path(__file__).resolve().parents[2] / "scalabel" / "tools"
if str(_DRAW_DIR) not in sys.path:
    sys.path.insert(0, str(_DRAW_DIR))

from draw_poly2d import poly_to_points  # noqa: E402

MIN_LENGTH = 15.0


def dense(poly: dict) -> np.ndarray:
    return poly_to_points(poly)


def length(pts: np.ndarray) -> float:
    pts = np.asarray(pts, dtype=float)
    if len(pts) < 2:
        return 0.0
    return float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())


def _arc(pts: np.ndarray) -> np.ndarray:
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    return np.concatenate([[0.0], np.cumsum(seg)])


def _resample(pts: np.ndarray, step: float):
    """Return (s, P, Nrm) or None: arc positions, points and unit normals."""
    pts = np.asarray(pts, dtype=float)
    if len(pts) < 2 or length(pts) < MIN_LENGTH:
        return None
    cum = _arc(pts)
    total = cum[-1]
    s = np.arange(0.0, total, step)
    if total - s[-1] > 1e-6:
        s = np.append(s, total)
    P = np.stack([np.interp(s, cum, pts[:, 0]), np.interp(s, cum, pts[:, 1])], axis=1)
    T = np.gradient(P, axis=0)
    norm = np.linalg.norm(T, axis=1, keepdims=True)
    T = T / np.where(norm == 0, 1, norm)
    N = np.stack([-T[:, 1], T[:, 0]], axis=1)
    return s, P, N


def resample(pts: np.ndarray, step: float = 3.0):
    """Resample at ~``step`` px; returns (P, unit normals (-ty, tx)) or (None, None)."""
    r = _resample(pts, step)
    if r is None:
        return None, None
    return r[1], r[2]


def _segments(poly: dict):
    """Yield (kind, vertex_indices) per segment, mirroring poly_to_points' walk.

    kind "L": (start, end). kind "C": (start, c1, c2, end).
    """
    n = len(poly["vertices"])
    types = poly.get("types") or "L" * n
    i = 1
    while i < n:
        if types[i] == "C":
            yield "C", (i - 1, i, (i + 1) % n, (i + 2) % n)
            i += 3
        else:
            yield "L", (i - 1, i)
            i += 1
    if poly.get("closed"):
        yield "L", (n - 1, 0)


def _bezier_t(cum, i0, s):
    """Bezier parameter t of arc positions ``s`` inside the segment whose dense
    samples (uniform in t) start at dense index ``i0``."""
    i1 = i0 + 1
    while i1 < len(cum) - 1 and cum[i1] < s.max():
        i1 += 1
    return np.interp(s, cum[i0:i1 + 1], np.linspace(0.0, 1.0, i1 - i0 + 1))


def _basis(u):
    return (1 - u) ** 3, 3 * u * (1 - u) ** 2, 3 * u**2 * (1 - u), u**3


def _fit_controls(p0, p3, u, targets, c1_old, c2_old):
    """Least-squares cubic control points through ``targets`` with fixed ends.

    ``u`` are initial Bezier parameters of the targets; they are re-projected
    onto the fitted curve a few times so the fit minimises point distance.
    """
    grid = np.linspace(0.0, 1.0, 400)
    for _ in range(5):
        b0, b1, b2, b3 = _basis(u)
        rhs = targets - np.outer(b0, p0) - np.outer(b3, p3)
        sol, *_ = np.linalg.lstsq(np.stack([b1, b2], axis=1), rhs, rcond=None)
        if not np.all(np.isfinite(sol)):
            return c1_old, c2_old
        g0, g1, g2, g3 = _basis(grid)
        curve = (np.outer(g0, p0) + np.outer(g1, sol[0])
                 + np.outer(g2, sol[1]) + np.outer(g3, p3))
        d = np.linalg.norm(targets[:, None, :] - curve[None], axis=2)
        u = grid[np.argmin(d, axis=1)]
    return sol[0], sol[1]


def _spans(poly: dict, verts: np.ndarray, cum: np.ndarray):
    """[(kind, vertex_indices, start dense index, start arc, end arc)] per segment."""
    out, count = [], 1
    for kind, idx in _segments(poly):
        sub = {"vertices": verts[list(idx)].tolist(),
               "types": "LCCL" if kind == "C" else "LL", "closed": False}
        i0 = count - 1
        count += len(dense(sub)) - 1
        out.append((kind, idx, i0, cum[min(i0, len(cum) - 1)], cum[min(count - 1, len(cum) - 1)]))
    return out


def shift_poly(poly: dict, offsets: np.ndarray, step: float = 3.0) -> dict:
    """Return a copy of ``poly`` moved by signed perpendicular ``offsets``.

    ``offsets`` has one value per point of ``resample(dense(poly))``. Line
    vertices and Bezier end points take the offset/normal of the nearest
    resampled point; a Bezier's two control points are least-squares fitted so
    the segment follows its resampled points moved by their offsets, so
    control points and end points move together.
    """
    verts = np.asarray(poly["vertices"], dtype=float)
    pts = dense(poly)
    r = _resample(pts, step)
    if r is None:
        raise ValueError("poly too short to shift")
    s, P, N = r
    offsets = np.asarray(offsets, dtype=float)
    if len(offsets) != len(s):
        raise ValueError(f"expected {len(s)} offsets, got {len(offsets)}")

    cum = _arc(pts)
    spans = _spans(poly, verts, cum)
    segs = [(k, idx) for k, idx, _, _, _ in spans]
    starts = [i0 for _, _, i0, _, _ in spans]
    ends = [e for _, _, _, _, e in spans]

    def vec_at(si: float):
        """Offset vector at arc position ``si``; mitred at corners."""
        j = int(np.argmin(np.abs(s - si)))
        n1, n2 = N[max(j - 2, 0)], N[min(j + 2, len(N) - 1)]
        return offsets[j] * (n1 + n2) / max(1.0 + float(n1 @ n2), 0.2)

    new = verts.copy()
    if poly.get("closed"):
        # closing corner: mitre the offset of the first and last edges
        n1, n2 = N[0], N[-1]
        off = 0.5 * (offsets[0] + offsets[-1])
        new[0] = verts[0] + off * (n1 + n2) / max(1.0 + float(n1 @ n2), 0.2)
    else:
        new[0] = verts[0] + vec_at(0.0)
    prev_pos = 0.0
    for (kind, idx), end_pos, i0 in zip(segs, ends, starts):
        end = idx[-1]
        if end != 0:
            new[end] = verts[end] + vec_at(end_pos)
        if kind == "C":
            m = (s >= prev_pos) & (s <= end_pos)
            k = np.flatnonzero(m)
            if len(k) >= 8:
                k = k[2:-2]  # end samples sit on corners with ambiguous normals
                m = np.zeros_like(m)
                m[k] = True
            if m.sum() >= 4:
                new[idx[1]], new[idx[2]] = _fit_controls(
                    new[idx[0]], new[end], _bezier_t(cum, i0, s[m]),
                    P[m] + offsets[m, None] * N[m], verts[idx[1]],
                    verts[idx[2]])
            else:  # tiny segment: use the mean offset/normal
                nrm = N[m].mean(axis=0) if m.any() else N[0]
                nn = np.linalg.norm(nrm)
                nrm = nrm / nn if nn else nrm
                off = offsets[m].mean() if m.any() else offsets[0]
                new[idx[1]] = verts[idx[1]] + off * nrm
                new[idx[2]] = verts[idx[2]] + off * nrm
        prev_pos = end_pos

    out = copy.deepcopy(poly)
    out["vertices"] = new.tolist()
    return out


# ---- partial shifts (the position fix on part of a line) ----
SHIFT_RESIDUAL_MAX = 3.5  # px between the shifted curve and its target, anywhere (calibrated 2026-10-17; kept 2026-10-18 under the 4 px training line width; was 2.0)
BREAK_TOL = 0.5  # px: Douglas-Peucker tolerance for the inserted break vertices
SHARP_COS = float(np.cos(np.radians(25.0)))  # corner samples: normal turns more than this


class ShiftNotRepresentable(ValueError):
    """The requested offsets cannot be reached by moving this poly's vertices."""


def _dp(points: np.ndarray, tol: float) -> list[int]:
    """Douglas-Peucker: indices (into ``points``) of the interior points to keep."""
    keep: list[int] = []
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        seg = points[b] - points[a]
        rel = points[a + 1:b] - points[a]
        ln = float(np.hypot(*seg))
        if ln < 1e-9:
            d = np.hypot(rel[:, 0], rel[:, 1])
        else:
            d = np.abs(seg[0] * rel[:, 1] - seg[1] * rel[:, 0]) / ln
        k = int(np.argmax(d))
        if d[k] > tol:
            m = a + 1 + k
            keep.append(m)
            stack += [(a, m), (m, b)]
    return sorted(keep)


def _split_cubic(p0, p1, p2, p3, ts):
    """de Casteljau: split one cubic at increasing parameters ``ts`` (0<t<1).

    Returns the vertex list after p0: [c1, c2, mid, c1, c2, mid, ..., c1, c2, p3]."""
    out, prev = [], 0.0
    for t in ts:
        u = (t - prev) / (1.0 - prev)  # parameter inside the remaining piece
        a, b, c = p0 + u * (p1 - p0), p1 + u * (p2 - p1), p2 + u * (p3 - p2)
        d, e = a + u * (b - a), b + u * (c - b)
        m = d + u * (e - d)
        out += [a, d, m]
        p0, p1, p2, prev = m, e, c, t
    return out + [p1, p2, p3]


MIN_PIECE = 4  # resampled points per Bezier piece, so its controls can be fitted


MAX_BEZIER_PIECES = 3  # a Bezier is never split into more pieces than this


def _cap_splits(keep: list[int], chain: np.ndarray, s: np.ndarray, offsets: np.ndarray,
                k: int) -> list[int]:
    """At most ``k`` of the sample indices ``keep``: greedily the one the offset profile
    (arc length -> offset, linearly interpolated between the chosen ones) fits worst."""
    chosen: list[int] = []
    ends = [int(chain[0]), int(chain[-1])]
    while len(chosen) < k and len(chosen) < len(keep):
        knots = sorted(ends + chosen)
        approx = np.interp(s[chain], s[knots], offsets[knots])
        err = np.abs(approx - offsets[chain])
        best = max((j for j in keep if j not in chosen),
                   key=lambda j: err[int(np.searchsorted(chain, j))])
        chosen.append(best)
    return sorted(chosen)


def insert_breakpoints(poly: dict, offsets: np.ndarray, step: float = 3.0,
                       tol: float = BREAK_TOL) -> dict:
    return _insert(poly, offsets, step, tol)[0]


def _insert(poly: dict, offsets: np.ndarray, step: float = 3.0,
            tol: float = BREAK_TOL) -> tuple[dict, list[bool]]:
    """Copy of ``poly`` with break vertices where the shift target bends: where the
    offset becomes non-zero or zero again (the edges of the flagged runs) and where
    it otherwise kinks. L segments get plain L vertices on the segment; a Bezier is
    split (de Casteljau) into pieces of the same curve. The drawn line is unchanged,
    so ``offsets`` (one per resampled point) still describe the result."""
    verts = np.asarray(poly["vertices"], dtype=float)
    n = len(verts)
    types = poly.get("types") or "L" * n
    pts = dense(poly)
    r = _resample(pts, step)
    if r is None:
        raise ValueError("poly too short to shift")
    s, P, N = r
    offsets = np.asarray(offsets, dtype=float)
    if len(offsets) != len(s):
        raise ValueError(f"expected {len(s)} offsets, got {len(offsets)}")
    T = P + offsets[:, None] * N
    nz = np.abs(offsets) > 1e-6
    cum = _arc(pts)
    line_ins: dict[int, list[int]] = {}  # vertex index b -> samples inserted before it
    curve_ins: dict[int, list[float]] = {}  # Bezier start index -> split parameters
    for kind, idx, i0, s0, s1 in _spans(poly, verts, cum):
        if kind == "L" and idx[1] == 0:  # the closing seam of a ring: leave it
            continue
        k = np.flatnonzero((s > s0 + 0.5 * step) & (s < s1 - 0.5 * step))
        if not len(k):
            continue
        ja, jb = int(np.argmin(np.abs(s - s0))), int(np.argmin(np.abs(s - s1)))
        chain = np.concatenate([[ja], k, [jb]])
        if kind == "L":  # straight: keep the target's corners
            picked = {int(chain[i]) for i in _dp(T[chain], tol)}
        else:  # curved: keep the offset profile's corners
            picked = {int(chain[i]) for i in _dp(np.stack([s[chain], offsets[chain]], 1), tol)}
        for j in k:  # zero <-> non-zero transitions: keep the zero side
            if nz[j] != nz[j - 1]:
                picked.add(int(j - 1) if nz[j] else int(j))
        picked = sorted(j for j in picked if k[0] <= j <= k[-1])
        if kind == "L":
            if picked:
                line_ins[idx[1]] = picked
            continue
        keep, last = [], ja  # Bezier pieces need MIN_PIECE samples each
        for j in picked:
            if j - last >= MIN_PIECE and jb - j >= MIN_PIECE:
                keep.append(j)
                last = j
        if len(keep) > MAX_BEZIER_PIECES - 1:
            keep = _cap_splits(keep, chain, s, offsets, MAX_BEZIER_PIECES - 1)
        if keep:
            sub = {"vertices": verts[list(idx)].tolist(), "types": "LCCL", "closed": False}
            i1 = i0 + len(dense(sub)) - 1  # dense samples i0..i1 are uniform in t
            ts = np.interp(s[keep], cum[i0:i1 + 1], np.linspace(0.0, 1.0, i1 - i0 + 1))
            ts = [float(t) for t in ts if 1e-3 < t < 1 - 1e-3]
            if ts:
                curve_ins[idx[0]] = sorted(set(ts))
    if not line_ins and not curve_ins:
        return copy.deepcopy(poly), [False] * n
    new_v: list[list[float]] = []
    new_t: list[str] = []
    ins: list[bool] = []
    i = 0
    while i < n:
        for j in line_ins.get(i, []):
            new_v.append(P[j].tolist())
            new_t.append("L")
            ins.append(True)
        new_v.append(list(poly["vertices"][i]))
        new_t.append(types[i])
        ins.append(False)
        if i in curve_ins and i + 3 <= n - 1:  # (a curve wrapping a ring's seam stays whole)
            pieces = _split_cubic(*(verts[i + q] for q in range(4)), curve_ins[i])
            for q, v in enumerate(pieces[:-1]):
                new_v.append(np.asarray(v).tolist())
                new_t.append("L" if q % 3 == 2 else "C")
                ins.append(False)
            i += 3  # the original end point comes next
            continue
        i += 1
    out = copy.deepcopy(poly)
    out["vertices"], out["types"] = new_v, "".join(new_t)
    return out, ins


def _dist_to_polyline(q: np.ndarray, line: np.ndarray, chunk: int = 64) -> np.ndarray:
    """Distance from each point of ``q`` to the polyline ``line``."""
    a, b = line[:-1], line[1:]
    ab = b - a
    l2 = np.maximum((ab ** 2).sum(axis=1), 1e-12)
    out = np.empty(len(q))
    for c in range(0, len(q), chunk):
        qq = q[c:c + chunk, None, :]
        t = np.clip(((qq - a) * ab).sum(axis=2) / l2, 0.0, 1.0)
        d = np.linalg.norm(a + t[..., None] * ab - qq, axis=2)
        out[c:c + chunk] = d.min(axis=1)
    return out


LINE_WIDTH = 4.0  # px: the downstream training line width; moves within it are pointless


def max_move(before: dict, after: dict) -> float:
    """Largest distance (px) between the dense curves of two polys, either direction."""
    B, A = np.asarray(dense(before), float), np.asarray(dense(after), float)
    if len(B) < 2 or len(A) < 2:
        return 0.0
    return float(max(_dist_to_polyline(B, A).max(), _dist_to_polyline(A, B).max()))


def shift_residual(before: dict, after: dict, offsets: np.ndarray, step: float = 3.0) -> float:
    """Largest distance (px) from a target point P + offset * N of ``before`` to the
    drawn curve of ``after``. Samples on a sharp corner (where the normal itself is
    ambiguous) are left out."""
    r = _resample(dense(before), step)
    if r is None:
        return 0.0
    _s, P, N = r
    T = P + np.asarray(offsets, dtype=float)[:, None] * N
    keep = np.ones(len(T), bool)
    if len(N) >= 3:
        keep[1:-1] = (N[:-2] * N[2:]).sum(axis=1) >= SHARP_COS
    line = dense(after)
    if len(line) < 2 or not keep.any():
        return 0.0
    return float(_dist_to_polyline(T[keep], np.asarray(line, dtype=float)).max())


SIMPLIFY_TOL = 2.0  # px: Douglas-Peucker tolerance for an all-L result (calibrated 2026-10-18: half the 4 px training line width; was 3.0, originally 1.0)
MERGE_TOL = 2.0  # px: how far dense(new) may stray from the target when merging inserted L (calibrated 2026-10-18: half the 4 px training line width; was 3.0, originally 1.5)


def _dp_poly(pts: np.ndarray, closed: bool, tol: float) -> np.ndarray:
    pts = np.asarray(pts, dtype=float)
    if not closed:
        return pts[[0] + _dp(pts, tol) + [len(pts) - 1]]
    if len(pts) <= 3:
        return pts
    far = int(np.argmax(np.hypot(*(pts - pts[0]).T)))
    a, b = pts[:far + 1], np.vstack([pts[far:], pts[:1]])
    ia = [0] + _dp(a, tol) + [far]
    ib = [far] + [far + k for k in _dp(b, tol)]
    return pts[ia + ib[1:]]


def _simplify(poly: dict, new: dict, offsets: np.ndarray, ins: list[bool], step: float) -> dict:
    """Fewer vertices for a successful shift, while the residual stays in bounds."""
    types = poly.get("types") or "L" * len(poly["vertices"])
    if "C" not in types:
        for tol in (SIMPLIFY_TOL, 0.5):
            cand = copy.deepcopy(new)
            v = _dp_poly(np.asarray(new["vertices"], float), bool(new.get("closed")), tol)
            if len(v) >= len(new["vertices"]):
                break
            cand["vertices"], cand["types"] = v.tolist(), "L" * len(v)
            if shift_residual(poly, cand, offsets, step) <= SHIFT_RESIDUAL_MAX:
                return cand
        return new
    cur_v, cur_t, cur_i = list(new["vertices"]), str(new["types"]), list(ins)
    j = 0
    while j < len(cur_v):  # one pass: drop inserted L vertices that cost < MERGE_TOL
        if cur_i[j] and cur_t[j] == "L":
            cand = copy.deepcopy(new)
            cand["vertices"] = cur_v[:j] + cur_v[j + 1:]
            cand["types"] = cur_t[:j] + cur_t[j + 1:]
            if shift_residual(poly, cand, offsets, step) <= MERGE_TOL:
                cur_v, cur_t, cur_i = cand["vertices"], cand["types"], cur_i[:j] + cur_i[j + 1:]
                continue
        j += 1
    out = copy.deepcopy(new)
    out["vertices"], out["types"] = cur_v, cur_t
    return out


def partial_shift(poly: dict, offsets: np.ndarray, step: float = 3.0) -> dict:
    """``shift_poly`` that also works when only part of the line is offset: break
    vertices are inserted first, then the residual against the target is checked,
    then the result is simplified (all-L: Douglas-Peucker; curves: merge the inserted
    vertices that are not needed). Raises ShiftNotRepresentable when it is over
    SHIFT_RESIDUAL_MAX anywhere."""
    offsets = np.asarray(offsets, dtype=float)
    split, ins = _insert(poly, offsets, step)
    s_old = _resample(dense(poly), step)[0]
    s_new = _resample(dense(split), step)[0]
    # splitting a Bezier refines its dense polyline, which can move the resampled
    # points a hair along the curve: carry the offsets over by arc fraction
    offs = np.interp(s_new * (s_old[-1] / s_new[-1]), s_old, offsets)
    new = shift_poly(split, offs, step)
    res = shift_residual(poly, new, offsets, step)
    if res > SHIFT_RESIDUAL_MAX:
        raise ShiftNotRepresentable(
            f"partial shift not representable — redraw by hand (residual {res:.1f} px)")
    final = _simplify(poly, new, offsets, ins, step)
    if shift_residual(poly, final, offsets, step) > SHIFT_RESIDUAL_MAX:
        return new
    return final


def change_anchor(before: dict | None, after: dict | None) -> list[int] | None:
    """[x, y] (ints) where a change is: the point of the BEFORE curve farthest from the
    AFTER curve; the before-label's dense midpoint for a delete or a category-only change
    (nothing moved); the after midpoint for an added label. Snapshots are
    ``{"category", "poly2d"}``. None when there is no geometry."""
    def polys(s):
        out = []
        for p in (s or {}).get("poly2d") or []:
            if len(p.get("vertices") or []) < 2:
                continue
            d = np.asarray(dense(p), float)
            r = _resample(d, max(3.0, float(_arc(d)[-1]) / 3000.0))
            out.append(r[1] if r is not None else d)
        return out

    def mid(ps):
        if not ps:
            return None
        p = max(ps, key=lambda q: float(_arc(q)[-1]))
        c = _arc(p)
        k = int(np.searchsorted(c, c[-1] / 2.0))
        return [int(round(p[min(k, len(p) - 1)][0])), int(round(p[min(k, len(p) - 1)][1]))]

    pb, pa = polys(before), polys(after)
    if not pb:
        return mid(pa)
    if not pa:
        return mid(pb)
    best, at = 0.0, None
    for p in pb:
        q = p
        d = np.min([_dist_to_polyline(q, a) for a in pa if len(a) >= 2] or [np.zeros(len(q))], axis=0)
        k = int(np.argmax(d))
        if d[k] > best:
            best, at = float(d[k]), q[k]
    if at is None or best < 0.05:
        return mid(pb)
    return [int(round(at[0])), int(round(at[1]))]


def point_at_arc(poly: dict, arc: float) -> list[float]:
    """[x, y] at ``arc`` px along ``dense(poly)`` (clamped to the ends)."""
    d = np.asarray(dense(poly), float)
    cum = _arc(d)
    return [float(np.interp(arc, cum, d[:, 0])), float(np.interp(arc, cum, d[:, 1]))]


MIN_SPLIT_PIECE = 3.0  # px: no piece of a split may be shorter


def split_poly(poly: dict, cuts: list[float], tol: float = SIMPLIFY_TOL) -> list[dict]:
    """Cut an open poly's dense curve at ``cuts`` (arc px, increasing) into len(cuts)+1
    all-L pieces. Each piece is Douglas-Peucker simplified (``tol``) with its ends kept
    exactly; neighbouring pieces share their cut point exactly, the first piece starts at the
    poly's first vertex and the last ends at its last vertex. Bezier segments are sampled with
    ``dense`` (poly_to_points), so a curve becomes a polyline within ``tol`` of it."""
    if poly.get("closed"):
        raise ValueError("closed polys are not split")
    d = np.asarray(dense(poly), float)
    if len(d) < 2 or not np.isfinite(d).all():
        raise ValueError("poly has no drawable curve")
    keep = np.concatenate([[True], np.linalg.norm(np.diff(d, axis=0), axis=1) > 1e-9])
    d = d[keep]
    cum = _arc(d)
    total = float(cum[-1])
    cuts = [float(c) for c in cuts]
    bounds = [0.0] + cuts + [total]
    if not cuts or any(b - a < MIN_SPLIT_PIECE for a, b in zip(bounds, bounds[1:])):
        raise ValueError(f"bad split cuts {cuts} for a {total:.1f} px line")
    cut_pts = [np.array([np.interp(c, cum, d[:, 0]), np.interp(c, cum, d[:, 1])]) for c in cuts]
    ends = [d[0]] + cut_pts + [d[-1]]
    out = []
    for k, (a, b) in enumerate(zip(bounds, bounds[1:])):
        inner = d[(cum > a + 1e-6) & (cum < b - 1e-6)]
        pts = np.vstack([ends[k][None], inner, ends[k + 1][None]])
        v = _dp_poly(pts, False, tol)
        verts = v.tolist()
        verts[0], verts[-1] = ends[k].tolist(), ends[k + 1].tolist()  # exact shared ends
        out.append({"vertices": verts, "types": "L" * len(verts), "closed": False})
    return out


# ---- self-crossing removal (the "join issue") ----
# Calibrated 2026-10-18 on the self-crossing calibration cards and Roshan's own fixes
# (283_verification MgLLWDKf5dOIzRXB, 937_verification l4eFvSQVYDndAHBN: a ring drawn as an
# open line whose two ends overlap by 1-3 px; he self-closed it with the editor's join).
# Most self-crossings are such overlapping ends; the rest are small loops / hooks where two
# joined pieces overlapped (1-36 px of line) and long genuine doublings that stay manual.
UNCROSS_LOOP_MAX = 150.0  # px of dense length all removed detours may have together
UNCROSS_LOOP_FRAC = 0.25  # ... and at most this share of the line
UNCROSS_END_MAX = 50.0  # px: an open line becomes a ring only when both ends overshoot <= this
UNCROSS_MAX_ITER = 8  # crossings removed one by one, at most this many
UNCROSS_STUB = 1.0  # px: a kept end piece shorter than this is dropped (line starts at the crossing)
UNCROSS_TRIM_CHANGE = 2 * LINE_WIDTH  # px: an end hook is trimmed only when it retraces the line


def self_crossings(poly: dict) -> list[tuple[float, float, tuple[float, float]]]:
    """Every proper crossing of two non-adjacent segments of ``dense(poly)`` as
    (arc a, arc b, (x, y)) with a < b (arc px along the dense curve; a ring's closing edge
    is part of the dense curve). Touching / collinear overlaps are not crossings, the same
    rule as the shape detector."""
    d = np.asarray(dense(poly), float)
    if len(d) < 4 or not np.isfinite(d).all():
        return []
    cum = _arc(d)
    A, B = d[:-1], d[1:]
    m = len(A)
    ring = bool(poly.get("closed"))

    def orient(p, q, r):
        return (q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1]) - \
               (q[..., 1] - p[..., 1]) * (r[..., 0] - p[..., 0])

    out = []
    for i in range(m - 2):
        if cum[i + 1] - cum[i] <= 1e-9:
            continue
        js = np.arange(i + 2, m)
        o1, o2 = orient(A[i], B[i], A[js]), orient(A[i], B[i], B[js])
        o3, o4 = orient(A[js], B[js], A[i]), orient(A[js], B[js], B[i])
        hit = (o1 * o2 < 0) & (o3 * o4 < 0)
        for k in np.flatnonzero(hit):
            j = int(js[k])
            if ring and i == 0 and np.allclose(B[j:], d[0]):  # edges meeting at the seam
                continue
            ti = o3[k] / (o3[k] - o4[k])
            tj = o1[k] / (o1[k] - o2[k])
            x = A[j] + tj * (B[j] - A[j])
            out.append((float(cum[i] + ti * (cum[i + 1] - cum[i])),
                        float(cum[j] + tj * (cum[j + 1] - cum[j])), (float(x[0]), float(x[1]))))
    return out


def _sub_cubic(p, t0: float, t1: float) -> list[np.ndarray]:
    """The exact control polygon [p0, c1, c2, p3] of cubic ``p`` between t0 < t1."""
    p0, p1, p2, p3 = (np.asarray(v, float) for v in p)
    if t1 < 1.0 - 1e-9:
        a, d, m, *_ = _split_cubic(p0, p1, p2, p3, [t1])
        p1, p2, p3 = a, d, m
        t0 = t0 / t1
    if t0 > 1e-9:
        *_, m, e, c, end = _split_cubic(p0, p1, p2, p3, [t0])
        p0, p1, p2, p3 = m, e, c, end
    return [p0, p1, p2, p3]


def _pieces(poly: dict) -> list[dict]:
    """The drawn primitives of ``poly`` in order: {"kind", "pts", "s0", "s1", "cum", "i0"}.

    L pieces take their ends from the dense curve (a ring's closing edge too); C pieces keep
    their exact control points, with dense samples i0..i0+39 uniform in t."""
    verts = np.asarray(poly["vertices"], dtype=float)
    d = np.asarray(dense(poly), float)
    cum = _arc(d)
    out = []
    for kind, idx, i0, s0, s1 in _spans(poly, verts, cum):
        if s1 - s0 <= 1e-9:
            continue
        if kind == "C":
            i1 = min(i0 + 39, len(d) - 1)
            out.append({"kind": "C", "pts": [d[i0]] + [verts[q] for q in idx[1:]],
                        "s0": s0, "s1": s1, "cum": cum[i0:i1 + 1]})
        else:
            out.append({"kind": "L", "pts": [d[i0], d[i0 + 1]], "s0": s0, "s1": s1})
    return out


def _cut_piece(pc: dict, u: float, v: float) -> tuple[str, list[np.ndarray]]:
    """Primitive ``pc`` between arc positions u < v (inside [s0, s1])."""
    if pc["kind"] == "L":
        a, b = pc["pts"]
        ln = pc["s1"] - pc["s0"]
        f0, f1 = (u - pc["s0"]) / ln, (v - pc["s0"]) / ln
        return "L", [a + f0 * (b - a), a + f1 * (b - a)]
    tt = np.linspace(0.0, 1.0, len(pc["cum"]))
    t0, t1 = (float(np.interp(x, pc["cum"], tt)) for x in (u, v))
    if t0 <= 1e-9 and t1 >= 1 - 1e-9:
        return "C", [np.asarray(q, float) for q in pc["pts"]]
    sub = _sub_cubic(pc["pts"], t0, t1)
    g = np.linspace(0.0, 1.0, 40)[:, None]
    b0, b1, b2, b3 = _basis(g)
    curve = b0 * sub[0] + b1 * sub[1] + b2 * sub[2] + b3 * sub[3]
    if _dist_to_polyline(curve, np.stack([sub[0], sub[3]])).max() <= SIMPLIFY_TOL:
        return "L", [sub[0], sub[3]]  # a trimmed, nearly straight bit of curve: one L edge
    return "C", sub


def _keep(pieces: list[dict], u: float, v: float) -> list[tuple[str, list[np.ndarray]]]:
    out = []
    for pc in pieces:
        lo, hi = max(u, pc["s0"]), min(v, pc["s1"])
        if hi - lo > 1e-6:
            out.append(_cut_piece(pc, lo, hi))
    return out


def _assemble(runs: list[list[tuple[str, list[np.ndarray]]]], joints: list[np.ndarray],
              closed: bool) -> dict:
    """Vertices / types from consecutive runs of primitives. ``joints[k]`` replaces the
    meeting point of run k and run k+1 (and, for a ring, joints[-1] is where the last run
    ends at the first one's start)."""
    verts: list[np.ndarray] = []
    types: list[str] = []
    for k, run in enumerate(runs):
        for q, (kind, pts) in enumerate(run):
            if not verts:
                verts.append(np.asarray(pts[0], float))
                types.append("L")
            if kind == "C":
                verts += [pts[1], pts[2]]
                types += ["C", "C"]
            verts.append(np.asarray(pts[-1], float))
            types.append("L")
        if k < len(joints):
            verts[-1] = np.asarray(joints[k], float)
    if closed and len(verts) > 1:
        if len(runs) == len(joints):  # the ring's start is the last joint too
            verts[0] = np.asarray(joints[-1], float)
        verts, types = verts[:-1], types[:-1]  # the end is the start again
    return {"vertices": [[float(x), float(y)] for x, y in verts], "types": "".join(types),
            "closed": bool(closed)}


def _apex(poly: dict, a: float, b: float, x) -> float:
    """Arc position of the point of the detour (a, b) farthest from the crossing ``x``: the
    tip of a hook that runs out and back."""
    d = np.asarray(dense(poly), float)
    cum = _arc(d)
    k = np.flatnonzero((cum > a) & (cum < b))
    if not len(k):
        return 0.5 * (a + b)
    return float(cum[k[int(np.argmax(np.hypot(*(d[k] - np.asarray(x, float)).T)))]])


def _uncross_once(poly: dict, a: float, b: float, x, mode: str,
                  apex: float | None = None) -> dict | None:
    """``poly`` with one crossing (a, b, x) removed: "cut" drops the detour [a, b] and joins
    both arms at x; "ring" keeps [a, b] as a closed ring that starts and ends at x;
    "trim_head" / "trim_tail" (an out-and-back hook at an end of an open line) drop the
    line from its start to the hook's tip ``apex`` / from the tip to its end, so the line
    still reaches the tip."""
    pieces = _pieces(poly)
    if not pieces:
        return None
    total = pieces[-1]["s1"]
    x = np.asarray(x, float)
    closed = bool(poly.get("closed"))
    if mode in ("trim_head", "trim_tail"):
        run = _keep(pieces, apex, total) if mode == "trim_head" else _keep(pieces, 0.0, apex)
        return _assemble([run], [], False) if run else None
    if mode == "ring":
        run = _keep(pieces, a, b)
        if not run:
            return None
        return _assemble([run], [x], True)
    head = _keep(pieces, 0.0, a) if a >= UNCROSS_STUB or closed else []
    tail = _keep(pieces, b, total) if total - b >= UNCROSS_STUB or closed else []
    if closed:  # the ring's seam is in the kept part: start ... head, x, tail ... start
        if not head or not tail:  # the seam sits on the crossing: one run from x to x
            return _assemble([head or tail], [x], True) if (head or tail) else None
        return _assemble([head, tail], [x], True)
    if head and tail:
        return _assemble([head, tail], [x], False)
    if head:  # the tail stub is dropped: the line ends at the crossing
        return _assemble([head], [x], False)
    if tail:  # the head stub is dropped: the line starts at the crossing
        out = _assemble([tail], [], False)
        out["vertices"][0] = [float(x[0]), float(x[1])]
        return out
    return None


def remove_self_crossings(poly: dict) -> tuple[dict, dict] | None:
    """Remove the self-crossings of ``poly`` the way the editor's join would have avoided
    them; returns (new poly, info) or None when it refuses (the line goes to hand review).

    Per crossing (a, b) on the dense curve, with ``total`` the line length, the candidates:
    * "cut": drop the detour [a, b] (a small loop where two joined pieces overlapped),
      keeping both arms, joined exactly at the crossing point;
    * "ring": an open line whose two ends overshoot each other (head a and tail total-b both
      <= UNCROSS_END_MAX) becomes a closed ring of [a, b] that starts at the crossing, like
      the editor's self-close join; on a ring the same mode removes a detour through the seam;
    * "trim_head" / "trim_tail": an out-and-back hook at an end of an open line (head or tail
      <= UNCROSS_END_MAX) loses its first leg / its return leg, so the line still reaches the
      hook's tip -- only when the hook retraces the line (the drawn line moves at most
      UNCROSS_TRIM_CHANGE px).
    The candidate that changes the drawn line least wins (smallest distance from the old
    curve to the new one, then fewest px removed); crossings are removed one at a time until
    the line is simple. Curves keep their exact control points: a Bezier the cut runs through
    is split with de Casteljau, and a trimmed, nearly straight bit of curve becomes one L
    edge. Refuses when the removed parts together exceed UNCROSS_LOOP_MAX px or
    UNCROSS_LOOP_FRAC of the line, or the line is still not simple after UNCROSS_MAX_ITER
    removals.

    info: {"modes": [...], "removed": [px per removal], "removed_len": px, "length": px,
           "n_crossings": int}"""
    if len(poly.get("vertices") or []) < 2:
        return None
    d0 = np.asarray(dense(poly), float)
    if not np.isfinite(d0).all():
        return None
    total0 = float(_arc(d0)[-1])
    budget = min(UNCROSS_LOOP_MAX, UNCROSS_LOOP_FRAC * total0)
    cur = copy.deepcopy(poly)
    modes, removed = [], []
    xs = self_crossings(cur)
    n0 = len(xs)
    if not xs:
        return None
    for _ in range(UNCROSS_MAX_ITER):
        if not xs:
            break
        dc = np.asarray(dense(cur), float)
        total = float(_arc(dc)[-1])
        probe = _resample(dc, 2.0)
        probe = dc if probe is None else probe[1]
        closed = bool(cur.get("closed"))
        best = None
        for a, b, x in xs:
            opts = [("cut", b - a, None)]
            if closed or (a <= UNCROSS_END_MAX and total - b <= UNCROSS_END_MAX):
                opts.append(("ring", a + total - b, None))
            if not closed and (a <= UNCROSS_END_MAX or total - b <= UNCROSS_END_MAX):
                apex = _apex(cur, a, b, x)
                if a <= UNCROSS_END_MAX:
                    opts.append(("trim_head", apex, apex))
                if total - b <= UNCROSS_END_MAX:
                    opts.append(("trim_tail", total - apex, apex))
            for mode, rem, apex in opts:
                if sum(removed) + rem > budget:
                    continue
                new = _uncross_once(cur, a, b, x, mode, apex)
                if new is None or len(new["vertices"]) < (3 if new["closed"] else 2):
                    continue
                dn = np.asarray(dense(new), float)
                if len(dn) < 2 or not np.isfinite(dn).all():
                    continue
                change = float(_dist_to_polyline(probe, dn).max())
                if mode.startswith("trim") and change > UNCROSS_TRIM_CHANGE:
                    continue  # not an out-and-back retrace: the first leg is real line
                if best is None or (round(change, 1), rem) < (round(best[0], 1), best[1]):
                    best = (change, rem, mode, new)
        if best is None:
            return None
        _, rem, mode, new = best
        cur = {**cur, **new}
        modes.append(mode)
        removed.append(float(rem))
        xs = self_crossings(cur)
    if xs:
        return None
    return cur, {"modes": modes, "removed": removed, "removed_len": float(sum(removed)),
                 "length": total0, "n_crossings": n0}


# ---- joining two overlapping pieces of one line (duplicates "overlapping_pieces") ----
# Roshan, after calibrating duplicates (overlapping_pieces 14/14 real): "Use auto join for
# fixing such cases." Two same-category labels A (shorter) and B that run along each other
# for part of A are joined like the editor's join tool: B keeps its curve and id, A's part
# beyond the overlap is spliced onto B's end, A's doubled stretch is dropped, A is deleted.
JOIN_NEAR = 4.0  # px: "along" = within this of the other line (the duplicates detector's NEAR)
JOIN_PARALLEL_COS = 0.94  # ... with tangents within ~20 deg (the detector's PARALLEL_COS)
JOIN_GAP_FILL = 6.0  # px: aligned stretches of A this close together count as one overlap
JOIN_RUN_MIN = 5.0  # px: shorter aligned stretches are ignored
JOIN_END_TOL = 3 * LINE_WIDTH  # px: an overlap "reaches" a line end when it stops within this
JOIN_BRIDGE = LINE_WIDTH  # px: both lines are cut back this far from the splice and bridged
JOIN_MIN_REST = 2.0  # px: A must reach at least this far beyond B to extend it
JOIN_CONTAINED = 2 * LINE_WIDTH  # px: A wholly this close to B -> the union is B itself
JOIN_DOUBLE_D = 2.0  # px: two stretches of one line this close ...
JOIN_DOUBLE_ARC = 20.0  # px: ... and this far apart along it double back on each other
JOIN_DOUBLE_MAX = 2 * LINE_WIDTH  # px of doubled line tolerated in a joined result


def reverse_poly(poly: dict) -> dict:
    """The same open curve drawn the other way (vertices and types reversed; a cubic's
    control points stay between its end points)."""
    if poly.get("closed"):
        raise ValueError("a ring has no direction to reverse here")
    v = [[float(x), float(y)] for x, y in reversed(poly["vertices"])]
    t = (poly.get("types") or "L" * len(v))[::-1]
    if t[:1] != "L":
        raise ValueError(f"cannot reverse types {poly.get('types')!r}")
    return {**poly, "vertices": v, "types": t, "closed": False}


def doubled_length(poly: dict) -> float:
    """px of ``poly`` that run within JOIN_DOUBLE_D of another stretch of the same line
    more than JOIN_DOUBLE_ARC away along it (an out-and-back or doubled section)."""
    from scipy.spatial import cKDTree
    d = np.asarray(dense(poly), float)
    if len(d) < 2 or not np.isfinite(d).all():
        return 0.0
    r = _resample(d, 1.0)
    if r is None:
        return 0.0
    s, P = r[0], r[1]
    total = float(s[-1])
    pairs = cKDTree(P).query_pairs(JOIN_DOUBLE_D, output_type="ndarray")
    if not len(pairs):
        return 0.0
    ds = np.abs(s[pairs[:, 0]] - s[pairs[:, 1]])
    if poly.get("closed"):
        ds = np.minimum(ds, total - ds)
    far = pairs[ds > JOIN_DOUBLE_ARC]
    hit = np.zeros(len(P), bool)
    hit[far.ravel()] = True
    return float(hit.sum())


def _project(d: np.ndarray, cum: np.ndarray, pt, lo: float = -np.inf,
             hi: float = np.inf) -> tuple[float, float]:
    """(arc, distance) of the point of polyline ``d`` nearest to ``pt``, among the arc
    positions in [lo, hi] (anywhere when none is)."""
    pt = np.asarray(pt, float)
    a, b = d[:-1], d[1:]
    ab = b - a
    l2 = np.maximum((ab ** 2).sum(axis=1), 1e-12)
    t = np.clip(((pt - a) * ab).sum(axis=1) / l2, 0.0, 1.0)
    q = a + t[:, None] * ab
    arc = cum[:-1] + t * (cum[1:] - cum[:-1])
    dist = np.linalg.norm(q - pt, axis=1)
    ok = (arc >= lo) & (arc <= hi)
    if not ok.any():
        ok[:] = True
    k = int(np.argmin(np.where(ok, dist, np.inf)))
    return float(arc[k]), float(dist[k])


def _join_runs(a: dict, b: dict) -> dict | None:
    """Where open poly ``a`` runs along open poly ``b``: aligned stretches of a (1 px samples
    within JOIN_NEAR of b, tangents within ~20 deg; gaps up to JOIN_GAP_FILL bridged), each
    with its arc span on a (a0, a1) and b (b0 at a0, b1 at a1, bmin, bmax), the line ends it
    reaches ("head" / "tail" / "both" / None) and its direction on b (sigma = +1 when b's arc
    grows with a's, -1 when it shrinks, 0 when unclear)."""
    from scipy.spatial import cKDTree
    da, db = np.asarray(dense(a), float), np.asarray(dense(b), float)
    if len(da) < 2 or len(db) < 2 or not (np.isfinite(da).all() and np.isfinite(db).all()):
        return None
    ra, rb = _resample(da, 1.0), _resample(db, 1.0)
    if ra is None or rb is None:
        return None
    sa, Pa, Na = ra
    sb, Pb, Nb = rb
    d, idx = cKDTree(Pb).query(Pa)
    al = (d <= JOIN_NEAR) & (np.abs((Na * Nb[idx]).sum(axis=1)) >= JOIN_PARALLEL_COS)
    LA, LB = float(sa[-1]), float(sb[-1])
    spans = []
    on = np.flatnonzero(al)
    if len(on):
        start = prev = int(on[0])
        for k in on[1:]:
            if sa[k] - sa[prev] > JOIN_GAP_FILL:
                spans.append((start, prev))
                start = int(k)
            prev = int(k)
        spans.append((start, prev))

    def ends(lo, hi, total):
        h, t = lo <= JOIN_END_TOL, total - hi <= JOIN_END_TOL
        return "both" if h and t else "head" if h else "tail" if t else None

    runs = []
    for i0, i1 in spans:
        a0, a1 = float(sa[i0]), float(sa[i1])
        if a1 - a0 < JOIN_RUN_MIN:
            continue
        sel = np.arange(i0, i1 + 1)[al[i0:i1 + 1]]
        bs = sb[idx[sel]]
        b0, b1 = float(sb[idx[i0]]), float(sb[idx[i1]])
        runs.append({"a0": a0, "a1": a1, "b0": b0, "b1": b1, "bmin": float(bs.min()),
                     "bmax": float(bs.max()), "a_end": ends(a0, a1, LA),
                     "b_end": ends(float(bs.min()), float(bs.max()), LB),
                     "sigma": 0 if abs(b1 - b0) < 2.0 else (1 if b1 > b0 else -1)})
    return {"LA": LA, "LB": LB, "runs": runs, "dmax": float(d.max()), "da": da, "db": db}


def _want_sigma(a_end: str, b_end: str) -> int:
    """The direction on b an overlap must have so a's free part continues past b's end: a
    run at a's head on b's tail (or a's tail on b's head) goes the same way, else opposite."""
    return 1 if (a_end == "head") == (b_end == "tail") else -1


def _resolve_b_end(run: dict) -> dict:
    """A run that covers both ends of a short b: the end it continues past follows from a's
    end and the direction."""
    if run["b_end"] == "both" and run["a_end"] in ("head", "tail") and run["sigma"]:
        tail = (run["a_end"] == "head") == (run["sigma"] > 0)
        return {**run, "b_end": "tail" if tail else "head"}
    return run


def _bridge(p, q) -> list:
    return [("L", [np.asarray(p, float), np.asarray(q, float)])]


def _splice_at_end(an: dict, run: dict, b_end: str, a_lo: float, a_hi: float):
    """(sB, tA, gap) of the splice at b's ``b_end``: b's end point when it lies on a (else
    the last point of b along a), projected onto a within arc [a_lo, a_hi]."""
    da, db = an["da"], an["db"]
    ca, cb = _arc(da), _arc(db)
    sB = an["LB"] if b_end == "tail" else 0.0
    E = db[-1] if b_end == "tail" else db[0]
    tA, gap = _project(da, ca, E, a_lo, a_hi)
    if gap > JOIN_NEAR:  # b's end hooks away from a: splice where b last runs along a
        sB = run["bmax"] if b_end == "tail" else run["bmin"]
        E = np.array([np.interp(sB, cb, db[:, 0]), np.interp(sB, cb, db[:, 1])])
        tA, gap = _project(da, ca, E, a_lo, a_hi)
    return sB, tA, gap


def _join_info(mode, splices, gaps, a, b, new, **kw) -> dict:
    la = float(_arc(np.asarray(dense(a), float))[-1])
    lb = float(_arc(np.asarray(dense(b), float))[-1])
    ln = float(_arc(np.asarray(dense(new), float))[-1])
    return {"mode": mode, "splices": [[float(x), float(y)] for x, y in splices],
            "gaps": [float(g) for g in gaps], "length_a": la, "length_b": lb,
            "length": ln, "added_len": ln - lb, **kw}


def _join_check(new: dict, a: dict, b: dict, info: dict):
    if len(new["vertices"]) < (3 if new["closed"] else 2):
        return None, "the joined line is degenerate"
    # only what the join adds counts: b may already cross itself or double back (a shape issue)
    nx, bx = len(self_crossings(new)), len(self_crossings(b))
    if nx > bx:
        return None, "the joined line would cross itself"
    dbl, bdbl = doubled_length(new), doubled_length(b)
    if dbl > bdbl + JOIN_DOUBLE_MAX:
        return None, f"the joined line would double back on itself ({dbl - bdbl:.0f} px)"
    info["b_crossings"], info["b_doubled"] = bx, bdbl
    out = {k: v for k, v in b.items() if k not in ("vertices", "types", "closed")}
    out.update(vertices=new["vertices"], types=new["types"], closed=bool(new["closed"]))
    return out, info


def _join_extend(a: dict, b: dict, run: dict):
    """b + a's part beyond b's end, a orientated to continue b; spliced at b's end."""
    bb = b if run["b_end"] == "tail" else reverse_poly(b)
    aa = a if run["a_end"] == "head" else reverse_poly(a)
    an = _join_runs(aa, bb)
    cand = [r for r in (an["runs"] if an else []) if r["a_end"] in ("head", "both")]
    cand = [_resolve_b_end(r) for r in cand]
    cand = [r for r in cand if r["b_end"] == "tail" and r["sigma"] > 0]
    if not cand:
        return None, "the overlap does not line up once orientated"
    r = max(cand, key=lambda q: q["a1"] - q["a0"])
    sB, tA, gap = _splice_at_end(an, r, "tail", -np.inf, r["a1"] + JOIN_END_TOL)
    if gap > 2 * LINE_WIDTH:
        return None, f"the pieces are {gap:.1f} px apart at the splice"
    h = JOIN_BRIDGE if gap > 0.5 else 0.0
    if an["LA"] - (tA + h) < JOIN_MIN_REST:
        return None, "nothing of the shorter piece reaches beyond the other one"
    if sB - h < JOIN_MIN_REST:
        return None, "the other piece is too short to splice"
    runB = _keep(_pieces(bb), 0.0, sB - h)
    runA = _keep(_pieces(aa), tA + h, an["LA"])
    if not runB or not runA:
        return None, "nothing left to splice"
    pB, pA = runB[-1][1][-1], runA[0][1][0]
    if h:
        new = _assemble([runB, _bridge(pB, pA), runA], [pB, pA], False)
    else:
        new = _assemble([runB, runA], [pB], False)
    if run["b_end"] == "head":
        new = reverse_poly(new)
    splice = (np.asarray(pB, float) + np.asarray(pA, float)) / 2
    info = _join_info("extend", [splice], [gap], a, b, new, a_end=run["a_end"],
                      b_end=run["b_end"], dropped_len=float(tA))
    return _join_check(new, a, b, info)


def _join_ring(a: dict, b: dict, head_run: dict, tail_run: dict):
    """a bridges b's two ends: the closed ring b + a's middle, spliced at both of b's ends."""
    aa = a if head_run["b_end"] == "tail" else reverse_poly(a)
    an = _join_runs(aa, b)
    runs = an["runs"] if an else []
    hr = [r for r in runs if r["a_end"] == "head" and r["b_end"] == "tail" and r["sigma"] > 0]
    tr = [r for r in runs if r["a_end"] == "tail" and r["b_end"] == "head" and r["sigma"] > 0]
    if not hr or not tr:
        return None, "the overlaps do not line up once orientated"
    hr = max(hr, key=lambda q: q["a1"] - q["a0"])
    tr = max(tr, key=lambda q: q["a1"] - q["a0"])
    sB1, tA1, g1 = _splice_at_end(an, hr, "tail", -np.inf, hr["a1"] + JOIN_END_TOL)
    sB0, tA2, g0 = _splice_at_end(an, tr, "head", tr["a0"] - JOIN_END_TOL, np.inf)
    if max(g0, g1) > 2 * LINE_WIDTH:
        return None, f"the pieces are {max(g0, g1):.1f} px apart at a splice"
    h1 = JOIN_BRIDGE if g1 > 0.5 else 0.0
    h0 = JOIN_BRIDGE if g0 > 0.5 else 0.0
    if (sB1 - h1) - (sB0 + h0) < JOIN_MIN_REST or (tA2 - h0) - (tA1 + h1) < JOIN_MIN_REST:
        return None, "the overlaps meet: nothing left to close the ring with"
    runB = _keep(_pieces(b), sB0 + h0, sB1 - h1)
    runA = _keep(_pieces(aa), tA1 + h1, tA2 - h0)
    if not runB or not runA:
        return None, "nothing left to splice"
    pB0, pB1 = runB[0][1][0], runB[-1][1][-1]
    pA1, pA2 = runA[0][1][0], runA[-1][1][-1]
    runs_, joints = [runB], [pB1]
    if h1:
        runs_.append(_bridge(pB1, pA1))
        joints.append(pA1)
    runs_.append(runA)
    joints.append(pA2 if h0 else pB0)
    if h0:
        runs_.append(_bridge(pA2, pB0))
        joints.append(pB0)
    new = _assemble(runs_, joints, True)
    s1 = (np.asarray(pB1, float) + np.asarray(pA1, float)) / 2
    s0 = (np.asarray(pB0, float) + np.asarray(pA2, float)) / 2
    info = _join_info("ring", [s1, s0], [g1, g0], a, b, new, a_end="both", b_end="both",
                      dropped_len=float(tA1 + an["LA"] - tA2))
    return _join_check(new, a, b, info)


def join_overlapping(a: dict, b: dict) -> tuple[dict | None, dict | str]:
    """Join open poly ``a`` (the shorter piece) into open poly ``b`` where they overlap;
    returns (b's new poly, info) or (None, reason it refuses -- the pair stays a hand fix).

    Modes, from where a runs along b (``_join_runs``):
    * "extend": a's overlap reaches one end of a and one end of b, running so that a's free
      part continues past b's end: b keeps its whole curve, a's doubled stretch is dropped and
      its free part is spliced on at b's end;
    * "ring": a overlaps both ends of b (a closes the gap of an outline drawn in two pieces):
      the closed ring b + a's middle;
    * "contained": all of a lies within JOIN_CONTAINED of b: b unchanged (a is deleted).
    At a splice both lines are cut back JOIN_BRIDGE px and joined by one straight edge (no
    bridge when they meet within 0.5 px); curves keep their exact control points (a Bezier
    the cut runs through is split with de Casteljau). Refuses a fork (the overlap stops in
    the middle of a or b), pieces that overlap running the wrong way (joining would double
    back), rings, and any result that would cross itself or double back.

    info: {"mode", "splices": [[x, y]], "gaps": [px], "length_a", "length_b", "length",
           "added_len", "a_end", "b_end", "dropped_len"}"""
    if a.get("closed") or b.get("closed"):
        return None, "a ring cannot be joined"
    if len(a.get("vertices") or []) < 2 or len(b.get("vertices") or []) < 2:
        return None, "line too short"
    an = _join_runs(a, b)
    if an is None:
        return None, "line too short"
    if an["dmax"] <= JOIN_CONTAINED:
        new = {"vertices": [[float(x), float(y)] for x, y in b["vertices"]],
               "types": b.get("types") or "L" * len(b["vertices"]), "closed": False}
        info = _join_info("contained", [], [], a, b, new, a_end="both", b_end=None,
                          dropped_len=an["LA"], max_offset=an["dmax"])
        return _join_check(new, a, b, info)
    runs = [_resolve_b_end(r) for r in an["runs"]]
    if not runs:
        return None, "no stretch runs along the other piece"
    ends = [r for r in runs if r["a_end"] in ("head", "tail") and r["b_end"] in ("head", "tail")
            and r["sigma"] == _want_sigma(r["a_end"], r["b_end"])]
    heads = [r for r in ends if r["a_end"] == "head"]
    tails = [r for r in ends if r["a_end"] == "tail"]
    if heads and tails:
        h = max(heads, key=lambda q: q["a1"] - q["a0"])
        t = max(tails, key=lambda q: q["a1"] - q["a0"])
        if h["b_end"] != t["b_end"]:
            return _join_ring(a, b, h, t)
    main = max(runs, key=lambda q: q["a1"] - q["a0"])
    if main["a_end"] == "both":
        return None, "the shorter piece lies along the other one but strays more than " \
                     f"{JOIN_CONTAINED:.0f} px from it"
    if main["a_end"] is None:
        return None, "the overlap is in the middle of the shorter piece (a fork / crossing)"
    if main["b_end"] not in ("head", "tail"):
        return None, "the shorter piece leaves the other one in its middle (a fork / branch)"
    if main["sigma"] != _want_sigma(main["a_end"], main["b_end"]):
        return None, "the pieces overlap running the wrong way (joining would double back)"
    return _join_extend(a, b, main)
