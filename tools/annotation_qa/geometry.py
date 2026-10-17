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
SHIFT_RESIDUAL_MAX = 3.5  # px between the shifted curve and its target, anywhere (calibrated 2026-10-17 against the user's "a few pixels is fine" guidance; was 2.0)
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


SIMPLIFY_TOL = 3.0  # px: Douglas-Peucker tolerance for an all-L result (calibrated 2026-10-17 against the user's "a few pixels is fine" guidance; was 1.0)
MERGE_TOL = 3.0  # px: how far dense(new) may stray from the target when merging inserted L (calibrated 2026-10-17 against the user's "a few pixels is fine" guidance; was 1.5)


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
