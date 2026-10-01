"""Pull out-of-ROI annotation vertices back inside the imagery.

Measured on 12 frames of a real batch: 97 of 2479 vertices (3.9%) fall outside
the ROI, and every frame is affected. The spill distribution decides the fix --
median 8 px, p90 16 px, max 198 px, against images 5000-20000 px wide. A median
spill of ~0.2% of image width is an annotator tracing a curb a hair past where
imagery ends, not misplaced geometry.

So: clamp, don't clip. There is no meaningful segment to truncate at 8 px, and
clipping would rebuild lines and change vertex counts for what is effectively
rounding error. Clamping preserves vertex count, curve types and identity --
only coordinates move. Corrections beyond ``flag_distance`` are still applied
but reported, so genuine mistakes (the 198 px outlier) surface rather than being
silently absorbed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .mask import RoiMask

# Corrections beyond this many pixels are reported for review. Chosen at ~6x the
# measured p90 (16 px), so ordinary trace overshoot stays quiet.
DEFAULT_FLAG_DISTANCE = 50.0

# Nudge clamped vertices this far inside the boundary. Landing exactly on the
# edge leaves them ambiguous for any later inside/outside test.
DEFAULT_INSET = 1.5


@dataclass
class VertexCorrection:
    """One vertex moved back inside the ROI."""

    label_id: str
    category: str
    vertex_index: int
    original: Tuple[float, float]
    corrected: Tuple[float, float]
    distance: float

    @property
    def flagged(self) -> bool:
        """Whether this correction exceeded the review threshold."""
        return self.distance > DEFAULT_FLAG_DISTANCE

    def to_dict(self) -> dict:
        """Serialise for the run report."""
        return {
            "labelId": self.label_id,
            "category": self.category,
            "vertexIndex": self.vertex_index,
            "original": [round(v, 3) for v in self.original],
            "corrected": [round(v, 3) for v in self.corrected],
            "distance": round(self.distance, 3),
        }


@dataclass
class ClampResult:
    """Outcome of clamping one frame."""

    total_vertices: int = 0
    corrections: List[VertexCorrection] = field(default_factory=list)

    @property
    def corrected_count(self) -> int:
        """How many vertices moved."""
        return len(self.corrections)

    def flagged(self, flag_distance: float) -> List[VertexCorrection]:
        """Corrections large enough to warrant a human look."""
        return [c for c in self.corrections if c.distance > flag_distance]


def _inset_point(
    roi: RoiMask, nx: int, ny: int, ox: float, oy: float, inset: float
) -> Tuple[float, float]:
    """Step a boundary point slightly inward, along the incoming direction.

    Moves from the original outside point toward the nearest inside pixel and
    keeps going by ``inset``. If that overshoots into a thin part of the region
    and lands back outside, the un-inset boundary point is kept instead.
    """
    dx, dy = nx - ox, ny - oy
    norm = float(np.hypot(dx, dy))
    if norm < 1e-9:
        return float(nx), float(ny)
    ux, uy = dx / norm, dy / norm
    cx, cy = nx + ux * inset, ny + uy * inset
    if roi.contains(cx, cy):
        return float(cx), float(cy)
    return float(nx), float(ny)


HANDLE = "C"
"""Per-vertex type marking a bezier control point ("handle")."""


def _handle_owner(index: int, types: str, count: int, closed: bool) -> int:
    """The anchor a curve handle belongs to, or -1 if there is none.

    A curved segment is stored as anchor, handle, handle, anchor ("LCCL"): each
    handle steers the curve where it leaves the nearer anchor, so the owner is
    the closest anchor along the vertex list, the earlier one on a tie. Closed
    shapes wrap around the end of the list.
    """

    def at(i: int) -> int:
        return i % count if closed else i

    back = forward = -1
    back_steps = forward_steps = 0
    for step in range(1, count):
        i = at(index - step)
        if i < 0:
            break
        if types[i : i + 1] != HANDLE:
            back, back_steps = i, step
            break
    for step in range(1, count):
        i = at(index + step)
        if i >= count:
            break
        if types[i : i + 1] != HANDLE:
            forward, forward_steps = i, step
            break
    if back == -1:
        return forward
    if forward == -1:
        return back
    return back if back_steps <= forward_steps else forward


def clamp_vertices(
    roi: RoiMask,
    vertices: List[List[float]],
    label_id: str = "",
    category: str = "",
    inset: float = DEFAULT_INSET,
    types: str = "",
    closed: bool = False,
) -> Tuple[List[List[float]], List[VertexCorrection]]:
    """Clamp one polyline's vertices into the ROI.

    Returns new vertices and the corrections applied. Vertices already inside
    are returned untouched, preserving their exact original values.

    Curve handles (type "C") are never clamped on their own: they do not lie on
    the line, so a handle out in the padding is normal for a curve that hugs
    the image edge, and dragging it inside would reshape the curve. Only
    anchors are tested. When an anchor does have to move, its handles move by
    the same offset, so the curve keeps its shape and shifts with the anchor.
    """
    out: List[List[float]] = [[float(v[0]), float(v[1])] for v in vertices]
    corrections: List[VertexCorrection] = []
    moved = {}  # anchor index -> how far it moved

    for index, (x, y) in enumerate(list(out)):
        if types[index : index + 1] == HANDLE or roi.contains(x, y):
            continue

        nx, ny, distance = roi.nearest_inside(x, y)
        cx, cy = _inset_point(roi, nx, ny, x, y, inset)
        out[index] = [cx, cy]
        moved[index] = (cx - x, cy - y)
        corrections.append(
            VertexCorrection(
                label_id=label_id,
                category=category,
                vertex_index=index,
                original=(x, y),
                corrected=(cx, cy),
                distance=distance,
            )
        )

    if moved:
        for index, (x, y) in enumerate(list(out)):
            if types[index : index + 1] != HANDLE:
                continue
            offset = moved.get(_handle_owner(index, types, len(out), closed))
            if offset is not None:
                out[index] = [x + offset[0], y + offset[1]]

    return out, corrections


def clamp_labels(
    roi: RoiMask,
    labels: List[dict],
    inset: float = DEFAULT_INSET,
) -> ClampResult:
    """Clamp every ``poly2d`` anchor across a frame's labels, in place.

    Only coordinates change: vertex count, ``types``, ``closed``, category and
    id are all preserved. Curve handles follow their anchors; see
    ``clamp_vertices``.
    """
    result = ClampResult()

    for label in labels:
        polys = label.get("poly2d") or []
        for poly in polys:
            vertices = poly.get("vertices") or []
            result.total_vertices += len(vertices)
            corrected, corrections = clamp_vertices(
                roi,
                vertices,
                label_id=str(label.get("id", "")),
                category=str(label.get("category", "")),
                inset=inset,
                types=str(poly.get("types") or ""),
                closed=bool(poly.get("closed")),
            )
            if corrections:
                poly["vertices"] = corrected
                result.corrections.extend(corrections)

    return result
