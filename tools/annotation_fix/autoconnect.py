"""Batch endpoint connection for imported polylines.

This is the offline equivalent of the editor's drag-an-endpoint-onto-another
gesture. In the app, ``Label2DList.findNearestEndpoint`` finds a target within
15 screen px and ``Polygon2D.mergeWith`` splices the two lines into one label.
Here the same rules run over the export JSON before import, so a batch of
predictions arrives already joined.

Two deliberate differences from the interactive path:

* Tolerance is in IMAGE pixels, not screen pixels. There is no zoom level
  offline, and results must not depend on one.
* Only same-``category`` pairs are merged. The editor also snaps endpoints
  across categories (coordinates align, labels stay distinct), but that relies
  on a visible indicator and one-step undo. Neither exists at import time, so
  moving vertices with nobody watching is not worth it.

The four splice orientations mirror ``Polygon2D.mergeWith`` exactly, including
dropping the duplicate junction vertex.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

# Matches the editor's 15 px snap radius, reinterpreted in image space.
DEFAULT_TOLERANCE = 15.0

# Optional straightness guard. Two lines meeting end-to-end at ~180 degrees are
# a real continuation; two meeting sharply are more likely a genuine fork. Off
# by default so the first runs are pure-distance and easy to reason about.
DEFAULT_MIN_ANGLE = 0.0


@dataclass
class Connection:
    """One merge of two polylines."""

    kept_id: str
    absorbed_id: str
    category: str
    junction: Tuple[float, float]
    gap: float
    angle: Optional[float] = None

    def to_dict(self) -> dict:
        """Serialise for the run report."""
        payload = {
            "keptId": self.kept_id,
            "absorbedId": self.absorbed_id,
            "category": self.category,
            "junction": [round(v, 3) for v in self.junction],
            "gap": round(self.gap, 3),
        }
        if self.angle is not None:
            payload["angle"] = round(self.angle, 2)
        return payload


@dataclass
class ConnectResult:
    """Outcome of auto-connecting one frame."""

    connections: List[Connection] = field(default_factory=list)
    labels_before: int = 0
    labels_after: int = 0

    @property
    def merged_count(self) -> int:
        """How many merges were applied."""
        return len(self.connections)


def _open_polyline(label: dict) -> Optional[dict]:
    """Return a label's single open ``poly2d``, or None if not eligible.

    Closed rings never participate, matching the editor. Multi-polygon labels
    are skipped: splicing one part of a compound shape has no clear meaning.
    """
    polys = label.get("poly2d") or []
    if len(polys) != 1:
        return None
    poly = polys[0]
    if poly.get("closed"):
        return None
    vertices = poly.get("vertices") or []
    if len(vertices) < 2:
        return None
    return poly


def _endpoint(poly: dict, is_start: bool) -> Tuple[float, float]:
    """Coordinates of a polyline's start or end vertex."""
    vertices = poly["vertices"]
    vertex = vertices[0] if is_start else vertices[-1]
    return float(vertex[0]), float(vertex[1])


def _direction(poly: dict, is_start: bool) -> Optional[np.ndarray]:
    """Unit vector pointing outward from the given endpoint.

    Uses the neighbouring vertex, so it reflects the line's local heading where
    it terminates.
    """
    vertices = poly["vertices"]
    if len(vertices) < 2:
        return None
    if is_start:
        tip, neighbour = vertices[0], vertices[1]
    else:
        tip, neighbour = vertices[-1], vertices[-2]
    vector = np.array(
        [float(tip[0]) - float(neighbour[0]), float(tip[1]) - float(neighbour[1])],
        dtype=float,
    )
    norm = float(np.linalg.norm(vector))
    if norm < 1e-9:
        return None
    return vector / norm


def _junction_angle(
    poly_a: dict, is_start_a: bool, poly_b: dict, is_start_b: bool
) -> Optional[float]:
    """Angle in degrees at a prospective junction.

    180 means the two lines continue straight through each other; small values
    mean they double back sharply.
    """
    dir_a = _direction(poly_a, is_start_a)
    dir_b = _direction(poly_b, is_start_b)
    if dir_a is None or dir_b is None:
        return None
    # Each direction points outward from its own tip, so continuation shows up
    # as the vectors being opposed.
    cosine = float(np.clip(np.dot(dir_a, -dir_b), -1.0, 1.0))
    return 180.0 - float(np.degrees(np.arccos(cosine)))


def _splice(
    vertices_a: List[List[float]],
    types_a: str,
    is_start_a: bool,
    vertices_b: List[List[float]],
    types_b: str,
    is_start_b: bool,
) -> Tuple[List[List[float]], str]:
    """Join two vertex runs at the touching endpoints.

    The four cases mirror ``Polygon2D.mergeWith``. In each, B's duplicate
    junction vertex is dropped so the seam carries a single vertex, and the
    per-vertex ``types`` string is spliced identically so curve flags follow
    their vertices.
    """
    list_a, list_b = list(vertices_a), list(vertices_b)
    seq_a, seq_b = list(types_a), list(types_b)

    if not is_start_a and is_start_b:
        # A.end -> B.start
        return list_a + list_b[1:], "".join(seq_a + seq_b[1:])
    if is_start_a and not is_start_b:
        # A.start -> B.end
        return list_b[:-1] + list_a, "".join(seq_b[:-1] + seq_a)
    if is_start_a and is_start_b:
        # A.start -> B.start: reverse A so its tail meets B's head
        return list_a[::-1] + list_b[1:], "".join(seq_a[::-1] + seq_b[1:])
    # A.end -> B.end: reverse B so its head meets A's tail
    return list_a + list_b[::-1][1:], "".join(seq_a + seq_b[::-1][1:])


def connect_labels(
    labels: List[dict],
    tolerance: float = DEFAULT_TOLERANCE,
    min_angle: float = DEFAULT_MIN_ANGLE,
) -> Tuple[List[dict], ConnectResult]:
    """Merge same-category polylines whose endpoints nearly touch.

    Pairs are considered shortest-gap first and each endpoint is consumed at
    most once, so three lines converging on one point cannot all claim it. After
    each merge the surviving line's endpoints have moved, so candidates are
    re-derived from scratch; chains (A-B-C) therefore resolve across passes.
    """
    result = ConnectResult(labels_before=len(labels))
    working: List[dict] = list(labels)
    absorbed: set = set()

    while True:
        # (label index, is_start) for every free endpoint of an eligible line.
        endpoints: List[Tuple[int, bool, dict, Tuple[float, float]]] = []
        for index, label in enumerate(working):
            if id(label) in absorbed:
                continue
            poly = _open_polyline(label)
            if poly is None:
                continue
            for is_start in (True, False):
                endpoints.append((index, is_start, poly, _endpoint(poly, is_start)))

        best: Optional[Tuple[float, int, bool, int, bool, Optional[float]]] = None

        for i in range(len(endpoints)):
            idx_a, start_a, poly_a, point_a = endpoints[i]
            label_a = working[idx_a]
            for j in range(i + 1, len(endpoints)):
                idx_b, start_b, poly_b, point_b = endpoints[j]
                if idx_a == idx_b:
                    continue  # Self-closing is an editor gesture, not a batch one.
                label_b = working[idx_b]
                if label_a.get("category") != label_b.get("category"):
                    continue

                gap = float(np.hypot(point_a[0] - point_b[0], point_a[1] - point_b[1]))
                if gap > tolerance:
                    continue

                angle = None
                if min_angle > 0.0:
                    angle = _junction_angle(poly_a, start_a, poly_b, start_b)
                    if angle is not None and angle < min_angle:
                        continue

                if best is None or gap < best[0]:
                    best = (gap, idx_a, start_a, idx_b, start_b, angle)

        if best is None:
            break

        gap, idx_a, start_a, idx_b, start_b, angle = best
        label_a, label_b = working[idx_a], working[idx_b]
        poly_a, poly_b = _open_polyline(label_a), _open_polyline(label_b)
        if poly_a is None or poly_b is None:
            break

        junction = _endpoint(poly_a, start_a)
        vertices, types = _splice(
            poly_a["vertices"],
            str(poly_a.get("types", "")),
            start_a,
            poly_b["vertices"],
            str(poly_b.get("types", "")),
            start_b,
        )
        poly_a["vertices"] = vertices
        poly_a["types"] = types

        absorbed.add(id(label_b))
        result.connections.append(
            Connection(
                kept_id=str(label_a.get("id", "")),
                absorbed_id=str(label_b.get("id", "")),
                category=str(label_a.get("category", "")),
                junction=junction,
                gap=gap,
                angle=angle,
            )
        )

    survivors = [label for label in working if id(label) not in absorbed]
    result.labels_after = len(survivors)
    return survivors, result
