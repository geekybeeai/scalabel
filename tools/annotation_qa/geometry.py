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

    # arc position at the end of each segment along the dense curve
    cum = _arc(pts)
    segs = list(_segments(poly))
    ends, starts, count = [], [], 1
    for kind, idx in segs:
        sub = {"vertices": verts[list(idx)].tolist(),
               "types": "LCCL" if kind == "C" else "LL", "closed": False}
        starts.append(count - 1)
        count += len(dense(sub)) - 1
        ends.append(cum[min(count - 1, len(cum) - 1)])

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
