"""Bad-shape detector: turn-backs / zigzags, self-crossings and tiny fragments.

Everything is measured on the dense curve (``geometry.dense``), so Bezier
control points are never mistaken for corners. A label gets at most one issue:
self-crossing > turn-back > fragment; the others are named in ``reason``.
"""

from __future__ import annotations

import numpy as np

from annotation_qa.context import FrameContext
from annotation_qa.fix import droppable_vertices
from annotation_qa.geometry import _arc, _segments, dense, length
from annotation_qa.issues import Issue

CHORD = 10.0  # px each side of a vertex for the direction change
TURN_MIN = 120.0  # degrees; strictly above this is a turn-back
FRAGMENT_MAX = 30.0  # px of dense length
FRAGMENT_CONF = 0.6
SELF_CROSS_CONF = 0.9
VERTEX_TOL = 10.0  # px: a turn-back this close to a vertex is "at" that vertex


def _clean(pts: np.ndarray) -> np.ndarray:
    """Drop consecutive duplicate points (zero-length segments)."""
    if len(pts) < 2:
        return pts
    keep = np.concatenate([[True], np.linalg.norm(np.diff(pts, axis=0), axis=1) > 1e-9])
    return pts[keep]


def _turn_backs(pts: np.ndarray) -> list[tuple[float, float]]:
    """[(arc position, peak angle in degrees)] per run of angles above TURN_MIN."""
    pts = _clean(pts)
    if len(pts) < 3:
        return []
    cum = _arc(pts)
    total = cum[-1]
    if total < 2 * CHORD:
        return []
    s = np.arange(CHORD, total - CHORD + 1e-9, 1.0)
    P = lambda q: np.stack([np.interp(q, cum, pts[:, 0]), np.interp(q, cum, pts[:, 1])], axis=1)
    a, b = P(s) - P(s - CHORD), P(s + CHORD) - P(s)
    na, nb = np.linalg.norm(a, axis=1), np.linalg.norm(b, axis=1)
    ok = (na > 1e-9) & (nb > 1e-9)
    cos = np.einsum("ij,ij->i", a, b) / np.where(ok, na * nb, 1.0)
    ang = np.where(ok, np.degrees(np.arccos(np.clip(cos, -1, 1))), 0.0)
    hot = ang > TURN_MIN
    out, i = [], 0
    while i < len(hot):
        if hot[i]:
            j = i
            while j + 1 < len(hot) and hot[j + 1]:
                j += 1
            k = i + int(np.argmax(ang[i:j + 1]))
            out.append((float(s[k]), float(ang[k])))
            i = j + 1
        else:
            i += 1
    return out


def _self_cross(pts: np.ndarray, closed: bool):
    """First proper crossing of two non-adjacent dense segments: (x, y) or None."""
    pts = _clean(pts)
    if closed and len(pts) > 1 and np.allclose(pts[0], pts[-1]):
        pts = pts[:-1]
        wrap = True  # segment n-1 closes back to point 0
        pts = np.vstack([pts, pts[:1]])
    else:
        wrap = False
    m = len(pts) - 1
    if m < 3:
        return None
    A, B = pts[:-1], pts[1:]
    d = B - A

    def orient(p, q, r):
        return (q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1]) - \
               (q[..., 1] - p[..., 1]) * (r[..., 0] - p[..., 0])

    for i in range(m - 2):
        js = np.arange(i + 2, m)
        if wrap and i == 0:
            js = js[js != m - 1]  # first and last edge of a ring are adjacent
        if not len(js):
            continue
        o1 = orient(A[i], B[i], A[js])
        o2 = orient(A[i], B[i], B[js])
        o3 = orient(A[js], B[js], A[i])
        o4 = orient(A[js], B[js], B[i])
        hit = (o1 * o2 < 0) & (o3 * o4 < 0)
        if hit.any():
            j = js[int(np.argmax(hit))]
            t = o1[js == j][0] / (o1[js == j][0] - o2[js == j][0])
            return tuple(float(v) for v in A[j] + t * d[j])
    return None


def _vertex_arcs(poly: dict, pts: np.ndarray) -> list[tuple[int, float]]:
    """(raw vertex index, arc position on ``pts``) for every on-line vertex."""
    verts = np.asarray(poly["vertices"], dtype=float)
    cum = _arc(pts)
    out = [(0, 0.0)]
    count = 1
    for kind, idx in _segments(poly):
        sub = {"vertices": verts[list(idx)].tolist(),
               "types": "LCCL" if kind == "C" else "LL", "closed": False}
        count += len(dense(sub)) - 1
        if idx[-1] != 0:
            out.append((idx[-1], float(cum[min(count - 1, len(cum) - 1)])))
    return out


def _point_at(pts: np.ndarray, s: float) -> tuple[float, float]:
    cum = _arc(pts)
    return (float(np.interp(s, cum, pts[:, 0])), float(np.interp(s, cum, pts[:, 1])))


def detect(ctx: FrameContext) -> list[Issue]:
    stem = ctx.export.stem
    frame_name = ctx.frame.get("name", "")
    issues: list[Issue] = []
    for label in ctx.frame.get("labels") or []:
        polys = label.get("poly2d") or []
        if not polys:
            continue
        crosses, backs, total = [], [], 0.0
        for pi, poly in enumerate(polys):
            if not poly.get("vertices"):
                continue
            pts = dense(poly)
            total += length(pts)
            closed = bool(poly.get("closed"))
            c = _self_cross(pts, closed)
            if c is not None:
                crosses.append({"poly_index": pi, "point": c})
            for s, ang in _turn_backs(pts):
                backs.append({"poly_index": pi, "s": s, "angle": ang, "poly": poly, "pts": pts})
        base = dict(issue_type="shape", export_stem=stem, frame_name=frame_name,
                    label_id=label["id"], category=label["category"])
        notes = []
        if crosses:
            notes.append(f"{len(crosses)} self-crossing")
        if backs:
            notes.append(f"{len(backs)} turn-back")
        if crosses:
            c = crosses[0]
            others = "; also " + ", ".join(n for n in notes if "turn-back" in n) if backs else ""
            issues.append(Issue(
                **base, confidence=SELF_CROSS_CONF, anchor=c["point"], fix=None, hand_fix=True,
                reason=f"line crosses itself at ({c['point'][0]:.0f}, {c['point'][1]:.0f}); "
                       f"redraw{others}",
                features={"kind": "self_cross", "poly_index": c["poly_index"],
                          "point": list(c["point"]), "n_self_cross": len(crosses),
                          "n_turn_back": len(backs), "length": total}))
        elif backs:
            t = max(backs, key=lambda b: b["angle"])
            poly, pts = t["poly"], t["pts"]
            conf = float(np.clip((t["angle"] - TURN_MIN) / 60.0, 0.0, 1.0))
            anchor = _point_at(pts, t["s"])
            near = min(_vertex_arcs(poly, pts), key=lambda va: abs(va[1] - t["s"]))
            vi, dist = near[0], abs(near[1] - t["s"])
            ok = dist <= VERTEX_TOL and vi in droppable_vertices(poly) \
                and len(poly["vertices"]) > (3 if poly.get("closed") else 2)
            fix = {"op": "drop_vertices", "poly_index": t["poly_index"], "indices": [vi]} \
                if ok else None
            more = f"; {len(backs)} turn-backs in label" if len(backs) > 1 else ""
            issues.append(Issue(
                **base, confidence=conf, anchor=anchor, fix=fix, hand_fix=not ok,
                reason=f"{t['angle']:.0f} deg turn-back at vertex {vi}"
                       + ("" if ok else " (curve/end point; redraw)") + more,
                features={"kind": "turn_back", "poly_index": t["poly_index"],
                          "angle": t["angle"], "vertex": vi, "vertex_dist": dist,
                          "n_turn_back": len(backs), "length": total}))
        elif total < FRAGMENT_MAX:
            first = next(p for p in polys if p.get("vertices"))
            pts = dense(first)
            issues.append(Issue(
                **base, confidence=FRAGMENT_CONF, anchor=tuple(float(v) for v in pts.mean(axis=0)),
                fix={"op": "delete"}, hand_fix=False,
                reason=f"fragment only {total:.0f} px long",
                features={"kind": "fragment", "length": total}))
    return issues
